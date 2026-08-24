"""Order intake, approval and cancellation endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, authorize, rate_limit, utc
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import ApprovalRequest, CancelOrderRequest, CreateOrderRequest
from taxstamp.authz.actions import Action
from taxstamp.enums import Role
from taxstamp.errors import NotFound
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Approval, Order, PaymentIntent
from taxstamp.services import orders as order_service
from taxstamp.services.context import Actor

router = APIRouter(prefix="/v1/orders", tags=["orders"])


def _order_document(order: Order) -> JsonObject:
    return {
        "id": str(order.id),
        "order_ref": order.order_ref,
        "company_id": str(order.company_id),
        "product_category": order.product_category,
        "product_id": str(order.product_id) if order.product_id is not None else None,
        "licence_id": str(order.licence_id) if order.licence_id is not None else None,
        "quantity": order.quantity,
        "unit_price_minor": order.unit_price_minor,
        "subtotal_minor": order.subtotal_minor,
        "vat_bps": order.vat_bps,
        "vat_minor": order.vat_minor,
        "total_minor": order.total_minor,
        "currency": order.currency,
        "status": order.status,
        "risk_tier": order.risk_tier,
        "compliance": order.compliance_evidence,
        "created_at": utc(order.created_at),
        "updated_at": utc(order.updated_at),
    }


@router.post("", status_code=201)
def create_order(
    body: CreateOrderRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.ORDER_CREATE)
    rate_limit(runtime, actor, "orders", runtime.settings.rate_limit_default)

    def work(session: Session) -> JsonObject:
        order = order_service.submit_order(
            session,
            actor=actor,
            command=order_service.SubmitOrderCommand(
                company_id=body.company_id,
                product_category=body.product_category,
                product_id=body.product_id,
                quantity=body.quantity,
                delivery_state=body.delivery_state,
                delivery_address=body.delivery_address,
            ),
            compliance=runtime.compliance,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
            max_quantity=runtime.settings.max_order_quantity,
            idempotency_key=key,
        )
        return _order_document(order)

    status, document = run_idempotent(
        runtime,
        scope="orders.create",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/{order_id}")
def get_order(order_id: uuid.UUID, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    actor = current.actor
    with runtime.session_factory() as session:
        order = session.get(Order, order_id)
        if order is None:
            raise NotFound("order not found")
        if actor.role is Role.REQUESTER:
            actor.require_company(order.company_id)
        document = _order_document(order)
        approvals = session.execute(select(Approval).where(Approval.order_id == order.id)).scalars()
        document["approvals"] = [
            {
                "level": approval.level,
                "decision": approval.decision,
                "reason": approval.reason,
                "created_at": utc(approval.created_at),
            }
            for approval in approvals
        ]
        intent = session.execute(
            select(PaymentIntent).where(PaymentIntent.order_id == order.id)
        ).scalar_one_or_none()
        document["payment"] = (
            {
                "reference": intent.reference,
                "amount_minor": intent.amount_minor,
                "currency": intent.currency,
                "status": intent.status,
            }
            if intent is not None
            else None
        )
        return document


@router.get("")
def list_orders(
    runtime: RuntimeDep,
    current: CurrentActor,
    limit: int = 50,
    offset: int = 0,
) -> JsonObject:
    actor = current.actor
    bounded_limit = max(1, min(limit, 200))
    with runtime.session_factory() as session:
        query = select(Order).order_by(Order.created_at.desc())
        if actor.role is Role.REQUESTER:
            if actor.company_id is None:
                raise NotFound("no company is associated with this credential")
            query = query.where(Order.company_id == actor.company_id)
        rows = session.execute(query.limit(bounded_limit).offset(max(offset, 0))).scalars().all()
        return {"orders": [_order_document(order) for order in rows], "limit": bounded_limit}


@router.post("/{order_id}/approvals", status_code=201)
def decide(
    order_id: uuid.UUID,
    body: ApprovalRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor: Actor = current.actor
    rate_limit(runtime, actor, "approvals", runtime.settings.rate_limit_default)

    def work(session: Session) -> JsonObject:
        order = order_service.decide_approval(
            session,
            actor=actor,
            command=order_service.ApprovalCommand(
                order_id=order_id,
                level=body.level,
                decision=body.decision,
                reason=body.reason,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
            idempotency_key=key,
        )
        return _order_document(order)

    status, document = run_idempotent(
        runtime,
        scope="orders.approve",
        key=key,
        actor=actor,
        payload={"order_id": str(order_id), **body.model_dump(mode="json")},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/{order_id}/cancel")
def cancel(
    order_id: uuid.UUID,
    body: CancelOrderRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        order = order_service.cancel_order(
            session,
            actor=actor,
            order_id=order_id,
            reason=body.reason,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return _order_document(order)

    status, document = run_idempotent(
        runtime,
        scope="orders.cancel",
        key=key,
        actor=actor,
        payload={"order_id": str(order_id), **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)

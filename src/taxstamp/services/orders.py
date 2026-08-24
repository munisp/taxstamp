"""Order intake and maker-checker approval.

Prices are computed from the effective tariff row, never from client input. Compliance
is checked against configured registries before an order can await approval, and every
transition is written to history and to the audit chain.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import (
    REQUIRED_APPROVALS,
    ApprovalDecision,
    ApprovalLevel,
    KybStatus,
    OrderStatus,
    PaymentIntentStatus,
    RiskTier,
    Role,
    assert_order_transition,
)
from taxstamp.errors import Conflict, Forbidden, IllegalState, NotFound, ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Approval, Company, Order, OrderTransition, PaymentIntent, Tariff
from taxstamp.money import Money, price_order
from taxstamp.outbox import enqueue
from taxstamp.providers.compliance import ComplianceService
from taxstamp.services.context import Actor

REFERENCE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _reference(prefix: str, now: dt.datetime) -> str:
    suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(10))
    return f"{prefix}-{now.year:04d}-{suffix}"


@dataclass(frozen=True, slots=True)
class SubmitOrderCommand:
    company_id: uuid.UUID
    product_category: str
    quantity: int
    delivery_state: str
    delivery_address: str


def _current_tariff(session: Session, product_category: str, now: dt.datetime) -> Tariff:
    tariff = session.execute(
        select(Tariff)
        .where(
            Tariff.product_category == product_category,
            Tariff.effective_from <= now,
            (Tariff.effective_to.is_(None)) | (Tariff.effective_to > now),
        )
        .order_by(Tariff.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    if tariff is None:
        raise ValidationFailed(
            f"no effective tariff for product category {product_category!r}",
            detail={"product_category": product_category},
        )
    return tariff


def _transition(
    session: Session,
    order: Order,
    target: OrderStatus,
    *,
    actor: Actor | None,
    reason: str,
    now: dt.datetime,
) -> None:
    current = OrderStatus(order.status)
    assert_order_transition(current, target)
    order.status = target.value
    order.updated_at = now
    session.add(
        OrderTransition(
            order_id=order.id,
            from_status=current.value,
            to_status=target.value,
            actor_principal_id=actor.principal_id if actor else None,
            reason=reason,
            created_at=now,
        )
    )
    session.flush()


def submit_order(
    session: Session,
    *,
    actor: Actor,
    command: SubmitOrderCommand,
    compliance: ComplianceService,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
    max_quantity: int,
    idempotency_key: str | None,
) -> Order:
    actor.require_role(Role.REQUESTER, Role.ADMIN)
    actor.require_company(command.company_id)
    if command.quantity <= 0:
        raise ValidationFailed("quantity must be positive")
    if command.quantity > max_quantity:
        raise ValidationFailed(f"quantity exceeds the per-order maximum of {max_quantity}")

    company = session.get(Company, command.company_id)
    if company is None:
        raise NotFound("company not found")
    if company.kyb_status != KybStatus.VERIFIED:
        raise ValidationFailed(
            "company business verification is not complete",
            detail={"kyb_status": company.kyb_status},
        )

    tariff = _current_tariff(session, command.product_category, now)
    breakdown = price_order(
        command.quantity,
        Money(tariff.unit_price_minor, tariff.currency),
        tariff.vat_bps,
    )

    # Refuses with CapabilityNotConfigured when a required registry is unconfigured, so an
    # order is never recorded with an unknown compliance state.
    outcome = compliance.check(
        tin=company.tin, product_category=command.product_category, quantity=command.quantity
    )

    order = Order(
        order_ref=_reference("ORD", now),
        company_id=company.id,
        submitted_by=actor.principal_id,
        product_category=command.product_category,
        quantity=command.quantity,
        tariff_id=tariff.id,
        unit_price_minor=breakdown.unit_price.minor,
        subtotal_minor=breakdown.subtotal.minor,
        vat_bps=breakdown.vat_bps,
        vat_minor=breakdown.vat.minor,
        total_minor=breakdown.total.minor,
        currency=breakdown.total.currency,
        status=OrderStatus.SUBMITTED.value,
        risk_tier=company.risk_tier,
        delivery_state=command.delivery_state,
        delivery_address=command.delivery_address,
        compliance_evidence=outcome.as_evidence(),
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    session.flush()
    session.add(
        OrderTransition(
            order_id=order.id,
            from_status=None,
            to_status=OrderStatus.SUBMITTED.value,
            actor_principal_id=actor.principal_id,
            reason="submitted",
            created_at=now,
        )
    )

    if outcome.compliant:
        _transition(
            session,
            order,
            OrderStatus.AWAITING_APPROVAL,
            actor=actor,
            reason="compliance checks passed",
            now=now,
        )
    else:
        _transition(
            session,
            order,
            OrderStatus.COMPLIANCE_PENDING,
            actor=actor,
            reason="compliance decision recorded",
            now=now,
        )
        _transition(
            session,
            order,
            OrderStatus.COMPLIANCE_REJECTED,
            actor=actor,
            reason="one or more registries reported non-compliance",
            now=now,
        )

    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="order.submit",
            target_type="order",
            target_id=str(order.id),
            outcome="success",
            after_state=_order_snapshot(order),
            request_id=actor.request_id,
            idempotency_key=idempotency_key,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return order


def _order_snapshot(order: Order) -> JsonObject:
    return {
        "order_ref": order.order_ref,
        "status": order.status,
        "quantity": order.quantity,
        "total_minor": order.total_minor,
        "currency": order.currency,
        "product_category": order.product_category,
    }


@dataclass(frozen=True, slots=True)
class ApprovalCommand:
    order_id: uuid.UUID
    level: ApprovalLevel
    decision: ApprovalDecision
    reason: str


_LEVEL_ROLES: dict[ApprovalLevel, Role] = {
    ApprovalLevel.ANALYST: Role.ANALYST,
    ApprovalLevel.SUPERVISOR: Role.SUPERVISOR,
}


def decide_approval(
    session: Session,
    *,
    actor: Actor,
    command: ApprovalCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
    idempotency_key: str | None,
) -> Order:
    required_role = _LEVEL_ROLES[command.level]
    actor.require_role(required_role)

    order = session.execute(
        select(Order).where(Order.id == command.order_id).with_for_update()
    ).scalar_one_or_none()
    if order is None:
        raise NotFound("order not found")
    if OrderStatus(order.status) is not OrderStatus.AWAITING_APPROVAL:
        raise IllegalState(f"order in status {order.status} cannot be approved")
    if order.submitted_by == actor.principal_id:
        raise Forbidden("the submitter of an order may not approve it")

    existing = session.execute(select(Approval).where(Approval.order_id == order.id)).scalars().all()
    if any(approval.level == command.level.value for approval in existing):
        raise Conflict(f"a {command.level.value} decision already exists for this order")
    if any(approval.actor_principal_id == actor.principal_id for approval in existing):
        raise Forbidden("the same person may not provide two approval levels")

    session.add(
        Approval(
            order_id=order.id,
            level=command.level.value,
            decision=command.decision.value,
            actor_principal_id=actor.principal_id,
            reason=command.reason,
            created_at=now,
        )
    )
    session.flush()

    intent_created = False
    if command.decision is ApprovalDecision.REJECTED:
        _transition(session, order, OrderStatus.REJECTED, actor=actor, reason=command.reason, now=now)
    else:
        required = REQUIRED_APPROVALS[RiskTier(order.risk_tier)]
        approved_levels = {
            approval.level
            for approval in session.execute(
                select(Approval).where(
                    Approval.order_id == order.id,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                )
            ).scalars()
        }
        if all(level.value in approved_levels for level in required):
            _transition(
                session, order, OrderStatus.APPROVED, actor=actor, reason="all approvals recorded", now=now
            )
            _transition(
                session,
                order,
                OrderStatus.AWAITING_PAYMENT,
                actor=actor,
                reason="payment intent created",
                now=now,
            )
            intent = PaymentIntent(
                order_id=order.id,
                reference=_reference("PAY", now),
                amount_minor=order.total_minor,
                currency=order.currency,
                status=PaymentIntentStatus.AWAITING_PAYMENT.value,
                created_at=now,
            )
            session.add(intent)
            session.flush()
            intent_created = True
            enqueue(
                session,
                aggregate_type="order",
                aggregate_id=order.id,
                event_type="order.awaiting_payment",
                dedupe_key=f"order.awaiting_payment:{order.id}",
                payload={
                    "order_id": str(order.id),
                    "payment_reference": intent.reference,
                    "amount_minor": intent.amount_minor,
                    "currency": intent.currency,
                },
                available_at=now,
            )

    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action=f"order.approval.{command.decision.value}",
            target_type="order",
            target_id=str(order.id),
            outcome="success",
            after_state={**_order_snapshot(order), "payment_intent_created": intent_created},
            request_id=actor.request_id,
            idempotency_key=idempotency_key,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return order


def cancel_order(
    session: Session,
    *,
    actor: Actor,
    order_id: uuid.UUID,
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Order:
    order = session.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise NotFound("order not found")
    if actor.role is not Role.ADMIN:
        actor.require_role(Role.REQUESTER)
        actor.require_company(order.company_id)
    _transition(session, order, OrderStatus.CANCELLED, actor=actor, reason=reason, now=now)
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="order.cancel",
            target_type="order",
            target_id=str(order.id),
            outcome="success",
            after_state=_order_snapshot(order),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return order

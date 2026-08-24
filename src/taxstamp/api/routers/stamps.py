"""Stamp activation, voiding, lookup and batch inspection."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, authorize, rate_limit, utc
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import (
    ActivateStampsRequest,
    DispositionRequest,
    InspectionRequest,
    VoidStampsRequest,
)
from taxstamp.authz.actions import Action
from taxstamp.enums import Role
from taxstamp.errors import NotFound
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Inspection, Order, StampBatch, StampDisposition
from taxstamp.services import accountability as accountability_service
from taxstamp.services import inspection as inspection_service
from taxstamp.services import stamps as stamp_service

router = APIRouter(prefix="/v1", tags=["stamps"])


def _bulk_document(result: stamp_service.BulkResult) -> JsonObject:
    return {
        "changed": result.changed_count,
        "results": [
            {
                "serial": outcome.serial,
                "status": outcome.status,
                "changed": outcome.changed,
                "reason": outcome.reason,
            }
            for outcome in result.outcomes
        ],
    }


@router.post("/stamps/activate")
def activate(
    body: ActivateStampsRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    rate_limit(runtime, actor, "activate", runtime.settings.rate_limit_default)

    def work(session: Session) -> JsonObject:
        return _bulk_document(
            stamp_service.activate_stamps(
                session,
                actor=actor,
                serials=list(body.serials),
                now=runtime.clock.now(),
                audit_secret=runtime.settings.audit_chain_secret,
                revision=runtime.settings.revision,
                idempotency_key=key,
            )
        )

    status, document = run_idempotent(
        runtime,
        scope="stamps.activate",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/stamps/void")
def void(
    body: VoidStampsRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        return _bulk_document(
            stamp_service.void_stamps(
                session,
                actor=actor,
                serials=list(body.serials),
                reason=body.reason,
                now=runtime.clock.now(),
                audit_secret=runtime.settings.audit_chain_secret,
                revision=runtime.settings.revision,
            )
        )

    status, document = run_idempotent(
        runtime,
        scope="stamps.void",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/stamps/{serial}")
def get_stamp(serial: str, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    actor = current.actor
    with runtime.session_factory() as session:
        stamp = stamp_service.get_stamp(session, serial=serial, actor=actor)
        return {
            "serial": stamp.serial,
            "status": stamp.status,
            "product_category": stamp.product_category,
            "order_id": str(stamp.order_id),
            "batch_id": str(stamp.batch_id),
            "issued_at": utc(stamp.issued_at),
            "activated_at": utc(stamp.activated_at) if stamp.activated_at else None,
            "expires_at": utc(stamp.expires_at),
        }


@router.get("/batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    actor = authorize(runtime, current.actor, Action.BATCH_READ)
    with runtime.session_factory() as session:
        batch = session.get(StampBatch, batch_id)
        if batch is None:
            raise NotFound("batch not found")
        if actor.role is Role.REQUESTER:
            company_id = session.execute(
                select(Order.company_id).where(Order.id == batch.order_id)
            ).scalar_one()
            actor.require_company(company_id)
        inspection = session.execute(
            select(Inspection).where(Inspection.batch_id == batch.id)
        ).scalar_one_or_none()
        return {
            "id": str(batch.id),
            "order_id": str(batch.order_id),
            "requested_count": batch.requested_count,
            "issued_count": batch.issued_count,
            "status": batch.status,
            "created_at": utc(batch.created_at),
            "completed_at": utc(batch.completed_at) if batch.completed_at else None,
            "inspection": (
                {
                    "accepted": inspection.accepted,
                    "sample_size": inspection.sample_size,
                    "accept_number": inspection.accept_number,
                    "reject_number": inspection.reject_number,
                    "defects_found": inspection.defects_found,
                    "created_at": utc(inspection.created_at),
                }
                if inspection is not None
                else None
            ),
        }


@router.post("/batches/{batch_id}/dispositions", status_code=201)
def declare_disposition(
    batch_id: uuid.UUID,
    body: DispositionRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        result = accountability_service.declare_disposition(
            session,
            actor=actor,
            command=accountability_service.DeclareDispositionCommand(
                batch_id=batch_id,
                kind=body.kind,
                serials=tuple(body.serials),
                reason=body.reason,
                evidence_reference=body.evidence_reference,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return {
            "disposition_id": str(result.disposition_id),
            "batch_id": str(result.batch_id),
            "kind": result.kind.value,
            "stamp_count": result.stamp_count,
        }

    status, document = run_idempotent(
        runtime,
        scope="batches.dispositions",
        key=key,
        actor=actor,
        payload={"batch_id": str(batch_id), **body.model_dump(mode="json")},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/batches/{batch_id}/account")
def batch_account(batch_id: uuid.UUID, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    """Stamp accountability for one batch: where every issued stamp is."""
    actor = authorize(runtime, current.actor, Action.BATCH_READ)
    with runtime.session_factory() as session:
        batch = session.get(StampBatch, batch_id)
        if batch is None:
            raise NotFound("batch not found")
        if actor.role is Role.REQUESTER:
            company_id = session.execute(
                select(Order.company_id).where(Order.id == batch.order_id)
            ).scalar_one()
            actor.require_company(company_id)
        account = accountability_service.batch_account(session, batch_id)
        dispositions = (
            session.execute(
                select(StampDisposition)
                .where(StampDisposition.batch_id == batch_id)
                .order_by(StampDisposition.created_at)
            )
            .scalars()
            .all()
        )
        return {
            "batch_id": str(account.batch_id),
            "order_ref": account.order_ref,
            "issued_count": account.issued_count,
            "active": account.active,
            "unused": account.unused,
            "void": account.void,
            "expired": account.expired,
            "declared_disposed": account.declared_disposed,
            "balances": account.balances,
            "dispositions": [
                {
                    "id": str(disposition.id),
                    "kind": disposition.kind,
                    "stamp_count": disposition.stamp_count,
                    "reason": disposition.reason,
                    "evidence_reference": disposition.evidence_reference,
                    "created_at": utc(disposition.created_at),
                }
                for disposition in dispositions
            ],
        }


@router.post("/batches/{batch_id}/inspections", status_code=201)
def inspect(
    batch_id: uuid.UUID,
    body: InspectionRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        result = inspection_service.record_inspection(
            session,
            actor=actor,
            command=inspection_service.InspectionCommand(
                batch_id=batch_id,
                defects_found=body.defects_found,
                defective_serials=list(body.defective_serials),
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return {
            "inspection_id": str(result.inspection_id),
            "accepted": result.accepted,
            "sample_size": result.sample_size,
            "accept_number": result.accept_number,
            "reject_number": result.reject_number,
            "voided_serials": result.voided_serials,
        }

    status, document = run_idempotent(
        runtime,
        scope="batches.inspect",
        key=key,
        actor=actor,
        payload={"batch_id": str(batch_id), **body.model_dump(mode="json")},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)

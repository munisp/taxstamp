"""Treasury endpoints for quarantined receipts."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, utc
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import ApplyReceiptRequest, RefundReceiptRequest
from taxstamp.enums import Role
from taxstamp.jsontypes import JsonObject
from taxstamp.services import treasury as treasury_service

router = APIRouter(prefix="/v1/treasury", tags=["treasury"])


def _resolution_document(result: treasury_service.ResolutionResult) -> JsonObject:
    return {
        "resolution_id": str(result.resolution_id),
        "receipt_id": str(result.receipt_id),
        "kind": result.kind.value,
        "order_id": str(result.order_id) if result.order_id is not None else None,
        "journal_reference": result.journal_reference,
    }


@router.get("/unapplied-receipts")
def list_unapplied(runtime: RuntimeDep, current: CurrentActor, limit: int = 50) -> JsonObject:
    actor = current.actor
    actor.require_role(Role.TREASURY, Role.AUDITOR, Role.ADMIN)
    bounded = max(1, min(limit, 200))
    with runtime.session_factory() as session:
        receipts = treasury_service.unresolved_receipts(session, limit=bounded)
        return {
            "receipts": [
                {
                    "id": str(receipt.id),
                    "external_reference": receipt.external_reference,
                    "declared_reference": receipt.declared_reference,
                    "status": receipt.status,
                    "amount_minor": receipt.amount_minor,
                    "currency": receipt.currency,
                    "value_date": utc(receipt.value_date),
                    "ingested_at": utc(receipt.ingested_at),
                }
                for receipt in receipts
            ],
            "limit": bounded,
        }


@router.post("/unapplied-receipts/{receipt_id}/application")
def apply_receipt(
    receipt_id: uuid.UUID,
    body: ApplyReceiptRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        return _resolution_document(
            treasury_service.apply_receipt_to_order(
                session,
                actor=actor,
                receipt_id=receipt_id,
                order_id=body.order_id,
                reason=body.reason,
                now=runtime.clock.now(),
                audit_secret=runtime.settings.audit_chain_secret,
                revision=runtime.settings.revision,
            )
        )

    status, document = run_idempotent(
        runtime,
        scope="treasury.apply",
        key=key,
        actor=actor,
        payload={"receipt_id": str(receipt_id), **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/unapplied-receipts/{receipt_id}/refund")
def refund_receipt(
    receipt_id: uuid.UUID,
    body: RefundReceiptRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        return _resolution_document(
            treasury_service.refund_receipt(
                session,
                actor=actor,
                receipt_id=receipt_id,
                beneficiary_reference=body.beneficiary_reference,
                reason=body.reason,
                now=runtime.clock.now(),
                audit_secret=runtime.settings.audit_chain_secret,
                revision=runtime.settings.revision,
            )
        )

    status, document = run_idempotent(
        runtime,
        scope="treasury.refund",
        key=key,
        actor=actor,
        payload={"receipt_id": str(receipt_id), **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)

"""Outbox message handlers.

Each handler is idempotent and either completes its effect or raises. An effect whose
external dependency is unconfigured raises ``CapabilityNotConfigured``, which keeps the
message pending and visible instead of marking it delivered.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditActor, AuditRecord, record_audit_event
from taxstamp.jsontypes import JsonObject, optional_int, require_int, require_str
from taxstamp.models import OutboxMessage, StampBatch
from taxstamp.runtime import Runtime
from taxstamp.services.issuance import issue_order

logger = structlog.get_logger(__name__)
SYSTEM_ACTOR = AuditActor(principal_id=None, subject="system:worker", role="operator", company_id=None)

Handler = Callable[[Runtime, Session, JsonObject], JsonObject]


def handle_issue_stamps(runtime: Runtime, session: Session, payload: JsonObject) -> JsonObject:
    """Issue the stamps an order paid for. Runs in its own transactions and is resumable."""
    del session
    order_id = uuid.UUID(require_str(payload, "order_id"))
    progress = issue_order(
        runtime.session_factory,
        order_id=order_id,
        chunk_size=runtime.settings.issuance_chunk_size,
        secure_code_secret=runtime.settings.device_hmac_secret,
        audit_secret=runtime.settings.audit_chain_secret,
        revision=runtime.settings.revision,
        clock=runtime.clock,
    )
    runtime.metrics["stamps_issued"].inc(progress.issued)
    return {"batch_id": str(progress.batch_id), "issued": progress.issued}


def handle_anchor_batch(runtime: Runtime, session: Session, payload: JsonObject) -> JsonObject:
    """Submit the batch Merkle root for external notarisation."""
    batch_id = uuid.UUID(require_str(payload, "batch_id"))
    root = require_str(payload, "merkle_root")
    receipt = runtime.anchor.anchor(batch_id=str(batch_id), root=root)
    batch = session.execute(select(StampBatch).where(StampBatch.id == batch_id)).scalar_one()
    record_audit_event(
        session,
        actor=SYSTEM_ACTOR,
        record=AuditRecord(
            action="batch.anchored",
            target_type="stamp_batch",
            target_id=str(batch.id),
            outcome="success",
            after_state={
                "merkle_root": receipt.root,
                "external_reference": receipt.external_reference,
                "anchored_at": receipt.anchored_at,
            },
        ),
        occurred_at=runtime.clock.now(),
        secret=runtime.settings.audit_chain_secret,
        revision=runtime.settings.revision,
    )
    return {"external_reference": receipt.external_reference}


def _record(
    runtime: Runtime,
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: str,
    payload: JsonObject,
) -> JsonObject:
    record_audit_event(
        session,
        actor=SYSTEM_ACTOR,
        record=AuditRecord(
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome="success",
            after_state=payload,
        ),
        occurred_at=runtime.clock.now(),
        secret=runtime.settings.audit_chain_secret,
        revision=runtime.settings.revision,
    )
    return {"recorded": True}


def handle_notify(runtime: Runtime, session: Session, payload: JsonObject) -> JsonObject:
    """Record an operational notification in the audit trail.

    Delivery to an external channel is not implemented; the event is retained durably so
    that no notification is silently dropped.
    """
    return _record(
        runtime,
        session,
        action="notification.recorded",
        target_type="notification",
        target_id=require_str(payload, "order_id")
        if "order_id" in payload
        else require_str(payload, "receipt_id"),
        payload=payload,
    )


def handle_trace_event(runtime: Runtime, session: Session, payload: JsonObject) -> JsonObject:
    """Retain a recorded movement for onward reporting.

    No regulator repository is integrated, so the movement is retained in the audit trail
    rather than claimed as filed.
    """
    return _record(
        runtime,
        session,
        action="trace.event_relayed",
        target_type="trace_event",
        target_id=require_str(payload, "event_ref"),
        payload=payload,
    )


def handle_consignment_released(runtime: Runtime, session: Session, payload: JsonObject) -> JsonObject:
    """Retain a customs release decision for onward reporting."""
    return _record(
        runtime,
        session,
        action="consignment.release_relayed",
        target_type="consignment",
        target_id=require_str(payload, "consignment_ref"),
        payload=payload,
    )


def handle_refund_authorised(runtime: Runtime, session: Session, payload: JsonObject) -> JsonObject:
    """Retain an authorised refund for treasury to pay out.

    No payout channel is integrated, so this records the authorisation; it does not claim
    that money left the account.
    """
    return _record(
        runtime,
        session,
        action="payment.refund_authorisation_recorded",
        target_type="payment_receipt",
        target_id=require_str(payload, "receipt_id"),
        payload=payload,
    )


def handle_mismatch_review(runtime: Runtime, session: Session, payload: JsonObject) -> JsonObject:
    # A remittance quoting an unknown reference has no intent, so there is no expected
    # amount to compare against; the receipt still needs review.
    expected = optional_int(payload, "expected_minor")
    received = require_int(payload, "amount_minor")
    logger.warning(
        "payment_mismatch_pending_review",
        receipt_id=require_str(payload, "receipt_id"),
        expected_minor=expected,
        received_minor=received,
    )
    return handle_notify(runtime, session, payload)


HANDLERS: dict[str, Handler] = {
    "order.issue_stamps": handle_issue_stamps,
    "batch.anchor_requested": handle_anchor_batch,
    "order.awaiting_payment": handle_notify,
    "payment.mismatch_requires_review": handle_mismatch_review,
    "payment.refund_authorised": handle_refund_authorised,
    "trace.event_recorded": handle_trace_event,
    "consignment.released": handle_consignment_released,
}


def handler_for(message: OutboxMessage) -> Handler:
    try:
        return HANDLERS[message.event_type]
    except KeyError as exc:
        raise LookupError(f"no handler registered for {message.event_type}") from exc

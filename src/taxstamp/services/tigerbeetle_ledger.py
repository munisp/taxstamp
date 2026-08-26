"""Durable, replay-safe TigerBeetle control-subledger boundary.

The implementation persists local intent + outbox first, treats all network ambiguity as
an unknown external outcome, then looks up the same transfer ID before retrying. It never
claims PostgreSQL and TigerBeetle form a distributed transaction.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from taxstamp import outbox
from taxstamp.audit import AuditActor, AuditRecord, record_audit_event
from taxstamp.canonical import canonical_hash
from taxstamp.db import transaction
from taxstamp.enums import (
    PaymentIntentStatus,
    TigerBeetleLedgerIntentState,
    TransitionError,
    assert_tigerbeetle_ledger_intent_transition,
)
from taxstamp.errors import (
    CapabilityNotConfigured,
    Conflict,
    DependencyUnavailable,
    IllegalState,
    ValidationFailed,
)
from taxstamp.jsontypes import JsonObject
from taxstamp.models import PaymentIntent, TigerBeetleLedgerIntent
from taxstamp.runtime import Runtime
from taxstamp.tigerbeetle import TigerBeetleCreateResult, TigerBeetleTransfer

SYSTEM_ACTOR = AuditActor(
    principal_id=None, subject="system:tigerbeetle-relay", role="operator", company_id=None
)


@dataclass(frozen=True, slots=True)
class LedgerIntentRequest:
    payment_intent_id: uuid.UUID
    tigerbeetle_transfer_id: str
    debit_account_id: str
    credit_account_id: str
    ledger_code: int
    transfer_code: int
    transfer_flags: int
    amount_minor: int
    currency: str


def _normalise_id(value: str, field: str) -> str:
    normalised = value.lower()
    if len(normalised) != 32 or any(char not in "0123456789abcdef" for char in normalised):
        raise ValidationFailed(f"{field} must be a 32-character lowercase hexadecimal identifier")
    return normalised


def _validate_request(request: LedgerIntentRequest) -> LedgerIntentRequest:
    transfer_id = _normalise_id(request.tigerbeetle_transfer_id, "tigerbeetle_transfer_id")
    debit = _normalise_id(request.debit_account_id, "debit_account_id")
    credit = _normalise_id(request.credit_account_id, "credit_account_id")
    currency = request.currency.upper()
    if debit == credit:
        raise ValidationFailed("debit_account_id and credit_account_id must differ")
    if request.amount_minor <= 0:
        raise ValidationFailed("amount_minor must be positive")
    if request.ledger_code <= 0 or request.ledger_code > 4_294_967_295:
        raise ValidationFailed("ledger_code must be in the unsigned 32-bit range")
    if request.transfer_code < 0 or request.transfer_code > 65_535:
        raise ValidationFailed("transfer_code must be in the unsigned 16-bit range")
    if request.transfer_flags < 0:
        raise ValidationFailed("transfer_flags must be non-negative")
    if len(currency) != 3 or not currency.isalpha():
        raise ValidationFailed("currency must be a three-letter code")
    return LedgerIntentRequest(
        payment_intent_id=request.payment_intent_id,
        tigerbeetle_transfer_id=transfer_id,
        debit_account_id=debit,
        credit_account_id=credit,
        ledger_code=request.ledger_code,
        transfer_code=request.transfer_code,
        transfer_flags=request.transfer_flags,
        amount_minor=request.amount_minor,
        currency=currency,
    )


def _payload(request: LedgerIntentRequest) -> JsonObject:
    return {
        "amount_minor": request.amount_minor,
        "credit_account_id": request.credit_account_id,
        "currency": request.currency,
        "debit_account_id": request.debit_account_id,
        "ledger_code": request.ledger_code,
        "payment_intent_id": str(request.payment_intent_id),
        "tigerbeetle_transfer_id": request.tigerbeetle_transfer_id,
        "transfer_code": request.transfer_code,
        "transfer_flags": request.transfer_flags,
    }


def create_ledger_intent(
    session: Session,
    *,
    request: LedgerIntentRequest,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
    actor: AuditActor,
) -> TigerBeetleLedgerIntent:
    """Persist intent, audit event and outbox record in one local transaction."""

    checked = _validate_request(request)
    payment = session.execute(
        select(PaymentIntent).where(PaymentIntent.id == checked.payment_intent_id).with_for_update()
    ).scalar_one_or_none()
    if payment is None:
        raise ValidationFailed("payment_intent_id does not exist")
    if payment.status != PaymentIntentStatus.SETTLED.value:
        raise IllegalState("TigerBeetle ledger intent requires a settled payment intent")
    if payment.amount_minor != checked.amount_minor or payment.currency != checked.currency:
        raise ValidationFailed("TigerBeetle intent must match the payment amount and currency")

    payload = _payload(checked)
    payload_hash = canonical_hash(payload)
    existing = session.execute(
        select(TigerBeetleLedgerIntent)
        .where(
            (TigerBeetleLedgerIntent.payment_intent_id == checked.payment_intent_id)
            | (TigerBeetleLedgerIntent.tigerbeetle_transfer_id == checked.tigerbeetle_transfer_id)
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        if existing.payload_hash == payload_hash:
            return existing
        raise Conflict("existing TigerBeetle ledger intent has different immutable transfer material")
    intent = TigerBeetleLedgerIntent(
        payment_intent_id=checked.payment_intent_id,
        tigerbeetle_transfer_id=checked.tigerbeetle_transfer_id,
        debit_account_id=checked.debit_account_id,
        credit_account_id=checked.credit_account_id,
        ledger_code=checked.ledger_code,
        transfer_code=checked.transfer_code,
        transfer_flags=checked.transfer_flags,
        amount_minor=checked.amount_minor,
        currency=checked.currency,
        payload_hash=payload_hash,
        state=TigerBeetleLedgerIntentState.READY,
    )
    savepoint = session.begin_nested()
    session.add(intent)
    try:
        session.flush()
    except IntegrityError:
        savepoint.rollback()
        existing = session.execute(
            select(TigerBeetleLedgerIntent)
            .where(
                (TigerBeetleLedgerIntent.payment_intent_id == checked.payment_intent_id)
                | (TigerBeetleLedgerIntent.tigerbeetle_transfer_id == checked.tigerbeetle_transfer_id)
            )
            .with_for_update()
        ).scalar_one()
        if existing.payload_hash == payload_hash:
            return existing
        raise Conflict(
            "existing TigerBeetle ledger intent has different immutable transfer material"
        ) from None
    savepoint.commit()
    outbox.enqueue(
        session,
        aggregate_type="tigerbeetle_ledger_intent",
        aggregate_id=intent.id,
        event_type="tigerbeetle.transfer_requested",
        dedupe_key=f"tigerbeetle-transfer:{intent.tigerbeetle_transfer_id}",
        payload={"intent_id": str(intent.id)},
        available_at=now,
    )
    record_audit_event(
        session,
        actor=actor,
        record=AuditRecord(
            action="tigerbeetle.intent.created",
            target_type="tigerbeetle_ledger_intent",
            target_id=str(intent.id),
            outcome="success",
            after_state={"payload_hash": intent.payload_hash, "state": intent.state},
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return intent


def _transfer_from_intent(intent: TigerBeetleLedgerIntent) -> TigerBeetleTransfer:
    return TigerBeetleTransfer(
        transfer_id=intent.tigerbeetle_transfer_id,
        debit_account_id=intent.debit_account_id,
        credit_account_id=intent.credit_account_id,
        ledger_code=int(intent.ledger_code),
        transfer_code=int(intent.transfer_code),
        transfer_flags=int(intent.transfer_flags),
        amount_minor=int(intent.amount_minor),
        timestamp=0,
    )


def _matches(intent: TigerBeetleLedgerIntent, transfer: TigerBeetleTransfer) -> bool:
    return (
        transfer.transfer_id == intent.tigerbeetle_transfer_id
        and transfer.debit_account_id == intent.debit_account_id
        and transfer.credit_account_id == intent.credit_account_id
        and transfer.ledger_code == intent.ledger_code
        and transfer.transfer_code == intent.transfer_code
        and transfer.transfer_flags == intent.transfer_flags
        and transfer.amount_minor == intent.amount_minor
    )


def _transition(intent: TigerBeetleLedgerIntent, target: TigerBeetleLedgerIntentState) -> None:
    current = TigerBeetleLedgerIntentState(intent.state)
    try:
        assert_tigerbeetle_ledger_intent_transition(current, target)
    except TransitionError as exc:
        raise ValidationFailed(str(exc)) from exc
    intent.state = target


def _record_state_audit(
    session: Session,
    *,
    intent: TigerBeetleLedgerIntent,
    action: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
    detail: dict[str, int | str],
) -> None:
    record_audit_event(
        session,
        actor=SYSTEM_ACTOR,
        record=AuditRecord(
            action=action,
            target_type="tigerbeetle_ledger_intent",
            target_id=str(intent.id),
            outcome="success" if intent.state != TigerBeetleLedgerIntentState.QUARANTINED else "failure",
            after_state={"state": intent.state, **detail},
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )


def _mark_submission_uncertain(runtime: Runtime, intent_id: uuid.UUID) -> TigerBeetleLedgerIntent | None:
    with transaction(runtime.session_factory) as session:
        intent = session.execute(
            select(TigerBeetleLedgerIntent).where(TigerBeetleLedgerIntent.id == intent_id).with_for_update()
        ).scalar_one()
        current = TigerBeetleLedgerIntentState(intent.state)
        if current in {
            TigerBeetleLedgerIntentState.POSTED,
            TigerBeetleLedgerIntentState.REJECTED,
            TigerBeetleLedgerIntentState.QUARANTINED,
        }:
            return None
        if current is TigerBeetleLedgerIntentState.READY:
            _transition(intent, TigerBeetleLedgerIntentState.SUBMISSION_UNCERTAIN)
        intent.attempt_count += 1
        intent.last_error = None
        intent.updated_at = runtime.clock.now()
        session.flush()
        session.expunge(intent)
        return intent


def _confirm_or_quarantine(
    runtime: Runtime,
    *,
    intent_id: uuid.UUID,
    transfer: TigerBeetleTransfer,
) -> bool:
    with transaction(runtime.session_factory) as session:
        intent = session.execute(
            select(TigerBeetleLedgerIntent).where(TigerBeetleLedgerIntent.id == intent_id).with_for_update()
        ).scalar_one()
        if not _matches(intent, transfer):
            _transition(intent, TigerBeetleLedgerIntentState.QUARANTINED)
            intent.last_error = "external transfer differs from immutable local intent"
            intent.updated_at = runtime.clock.now()
            _record_state_audit(
                session,
                intent=intent,
                action="tigerbeetle.intent.quarantined",
                now=runtime.clock.now(),
                audit_secret=runtime.settings.audit_chain_secret,
                revision=runtime.settings.revision,
                detail={"external_timestamp": transfer.timestamp},
            )
            return False
        current = TigerBeetleLedgerIntentState(intent.state)
        if current is TigerBeetleLedgerIntentState.SUBMISSION_UNCERTAIN:
            _transition(intent, TigerBeetleLedgerIntentState.EXTERNAL_CONFIRMED)
        intent.external_timestamp = transfer.timestamp
        intent.external_confirmed_at = runtime.clock.now()
        intent.updated_at = runtime.clock.now()
        _record_state_audit(
            session,
            intent=intent,
            action="tigerbeetle.intent.external_confirmed",
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
            detail={"external_timestamp": transfer.timestamp},
        )
    return True


def _finalise_posted(runtime: Runtime, intent_id: uuid.UUID) -> None:
    with transaction(runtime.session_factory) as session:
        intent = session.execute(
            select(TigerBeetleLedgerIntent).where(TigerBeetleLedgerIntent.id == intent_id).with_for_update()
        ).scalar_one()
        if TigerBeetleLedgerIntentState(intent.state) is TigerBeetleLedgerIntentState.POSTED:
            return
        _transition(intent, TigerBeetleLedgerIntentState.POSTED)
        intent.posted_at = runtime.clock.now()
        intent.updated_at = runtime.clock.now()
        _record_state_audit(
            session,
            intent=intent,
            action="tigerbeetle.intent.posted",
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
            detail={"external_timestamp": intent.external_timestamp or 0},
        )


def _reject(runtime: Runtime, intent_id: uuid.UUID, reason: str) -> None:
    with transaction(runtime.session_factory) as session:
        intent = session.execute(
            select(TigerBeetleLedgerIntent).where(TigerBeetleLedgerIntent.id == intent_id).with_for_update()
        ).scalar_one()
        _transition(intent, TigerBeetleLedgerIntentState.REJECTED)
        intent.last_error = reason[:2000]
        intent.updated_at = runtime.clock.now()
        _record_state_audit(
            session,
            intent=intent,
            action="tigerbeetle.intent.rejected",
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
            detail={"reason": reason[:500]},
        )


def submit_intent_lookup_before_retry(runtime: Runtime, *, intent_id: uuid.UUID) -> str:
    """Confirm an existing transfer before creating it; network uncertainty never creates a new ID."""

    client = runtime.tigerbeetle_client
    if client is None:
        raise CapabilityNotConfigured("TigerBeetle client adapter is not configured")
    intent = _mark_submission_uncertain(runtime, intent_id)
    if intent is None:
        return "terminal"
    expected = _transfer_from_intent(intent)
    observed = client.lookup_transfer(expected.transfer_id)
    if observed is None:
        result = client.create_transfer(expected)
        if result is TigerBeetleCreateResult.REJECTED:
            _reject(runtime, intent_id, "TigerBeetle rejected the transfer")
            return "rejected"
        observed = client.lookup_transfer(expected.transfer_id)
        if observed is None:
            raise DependencyUnavailable("TigerBeetle did not return a transfer after create confirmation")
    if not _confirm_or_quarantine(runtime, intent_id=intent_id, transfer=observed):
        return "quarantined"
    _finalise_posted(runtime, intent_id)
    return "posted"

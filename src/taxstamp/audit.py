"""Append-only, tamper-evident audit log.

Each event stores the keyed hash of (previous hash + canonical event). Verification
recomputes the chain, so any insertion, reordering, or edit of a historical row is
detectable even by a reader that only has the log and the chain key.
"""

from __future__ import annotations

import datetime as dt
import hmac
import uuid
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.canonical import canonical_bytes
from taxstamp.clock import ensure_utc
from taxstamp.db import LockKey, advisory_xact_lock
from taxstamp.jsontypes import JsonObject
from taxstamp.models import AuditEvent

GENESIS_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditActor:
    principal_id: uuid.UUID | None
    subject: str
    role: str
    company_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    action: str
    target_type: str
    target_id: str
    outcome: str
    before_state: JsonObject | None = None
    after_state: JsonObject | None = None
    request_id: str | None = None
    idempotency_key: str | None = None


def _event_document(
    *,
    event_id: uuid.UUID,
    occurred_at: dt.datetime,
    actor: AuditActor,
    record: AuditRecord,
    revision: str,
) -> JsonObject:
    return {
        "event_id": str(event_id),
        "occurred_at": ensure_utc(occurred_at).isoformat(),
        "actor_principal_id": str(actor.principal_id) if actor.principal_id else None,
        "actor_subject": actor.subject,
        "actor_role": actor.role,
        "company_id": str(actor.company_id) if actor.company_id else None,
        "action": record.action,
        "target_type": record.target_type,
        "target_id": record.target_id,
        "outcome": record.outcome,
        "request_id": record.request_id,
        "idempotency_key": record.idempotency_key,
        "before_state": record.before_state,
        "after_state": record.after_state,
        "revision": revision,
    }


def chain_hash(prev_hash: str, document: JsonObject, *, secret: str) -> str:
    message = prev_hash.encode("ascii") + b"." + canonical_bytes(document)
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


def record_audit_event(
    session: Session,
    *,
    actor: AuditActor,
    record: AuditRecord,
    occurred_at: dt.datetime,
    secret: str,
    revision: str,
) -> AuditEvent:
    """Append one event inside the caller's transaction.

    An advisory lock serialises chain extension so concurrent writers cannot both
    build on the same predecessor hash.
    """
    advisory_xact_lock(session, LockKey.AUDIT_CHAIN)
    prev_hash = session.execute(
        select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
    ).scalar_one_or_none()
    event_id = uuid.uuid4()
    document = _event_document(
        event_id=event_id,
        occurred_at=occurred_at,
        actor=actor,
        record=record,
        revision=revision,
    )
    previous = prev_hash or GENESIS_HASH
    event = AuditEvent(
        event_id=event_id,
        occurred_at=ensure_utc(occurred_at),
        actor_principal_id=actor.principal_id,
        actor_subject=actor.subject,
        actor_role=actor.role,
        company_id=actor.company_id,
        action=record.action,
        target_type=record.target_type,
        target_id=record.target_id,
        outcome=record.outcome,
        request_id=record.request_id,
        idempotency_key=record.idempotency_key,
        before_state=record.before_state,
        after_state=record.after_state,
        revision=revision,
        prev_hash=previous,
        hash=chain_hash(previous, document, secret=secret),
    )
    session.add(event)
    session.flush()
    return event


@dataclass(frozen=True, slots=True)
class ChainVerification:
    events_checked: int
    intact: bool
    first_bad_seq: int | None
    reason: str | None


def verify_audit_chain(session: Session, *, secret: str, revision_column: bool = True) -> ChainVerification:
    """Recompute the whole chain and report the first inconsistency, if any."""
    del revision_column
    previous = GENESIS_HASH
    checked = 0
    for event in session.execute(select(AuditEvent).order_by(AuditEvent.seq)).scalars():
        document = _event_document(
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            actor=AuditActor(
                principal_id=event.actor_principal_id,
                subject=event.actor_subject,
                role=event.actor_role,
                company_id=event.company_id,
            ),
            record=AuditRecord(
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                outcome=event.outcome,
                before_state=event.before_state,
                after_state=event.after_state,
                request_id=event.request_id,
                idempotency_key=event.idempotency_key,
            ),
            revision=event.revision,
        )
        if event.prev_hash != previous:
            return ChainVerification(checked, False, event.seq, "prev_hash does not match predecessor")
        expected = chain_hash(previous, document, secret=secret)
        if not hmac.compare_digest(expected, event.hash):
            return ChainVerification(checked, False, event.seq, "recomputed hash does not match stored hash")
        previous = event.hash
        checked += 1
    return ChainVerification(checked, True, None, None)

"""Transactional outbox.

External effects are recorded in the same transaction as the state change that caused
them, then relayed at least once with a lease, bounded retries with exponential
backoff, and a dead-letter state that is surfaced by reconciliation and metrics.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from taxstamp.jsontypes import JsonObject
from taxstamp.models import OutboxMessage


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    message_id: uuid.UUID | None
    created: bool


def enqueue(
    session: Session,
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    dedupe_key: str,
    payload: JsonObject,
    available_at: dt.datetime,
) -> EnqueueResult:
    """Record an outbound effect. A duplicate dedupe key is a no-op, not an error."""
    message = OutboxMessage(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        dedupe_key=dedupe_key,
        payload=payload,
        available_at=available_at,
    )
    savepoint = session.begin_nested()
    session.add(message)
    try:
        session.flush()
    except IntegrityError:
        savepoint.rollback()
        existing = session.execute(
            select(OutboxMessage.id).where(OutboxMessage.dedupe_key == dedupe_key)
        ).scalar_one_or_none()
        return EnqueueResult(message_id=existing, created=False)
    savepoint.commit()
    return EnqueueResult(message_id=message.id, created=True)


def claim_batch(
    session: Session,
    *,
    worker_id: str,
    now: dt.datetime,
    lease_seconds: int,
    batch_size: int,
) -> list[OutboxMessage]:
    """Lease due messages using SKIP LOCKED so that workers never collide."""
    candidates = (
        session.execute(
            select(OutboxMessage.id)
            .where(
                OutboxMessage.processed_at.is_(None),
                OutboxMessage.dead_lettered_at.is_(None),
                OutboxMessage.available_at <= now,
                (OutboxMessage.locked_until.is_(None)) | (OutboxMessage.locked_until < now),
            )
            .order_by(OutboxMessage.available_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    if not candidates:
        return []
    session.execute(
        update(OutboxMessage)
        .where(OutboxMessage.id.in_(candidates))
        .values(locked_by=worker_id, locked_until=now + dt.timedelta(seconds=lease_seconds))
    )
    return list(
        session.execute(select(OutboxMessage).where(OutboxMessage.id.in_(candidates))).scalars().all()
    )


def mark_processed(session: Session, message_id: uuid.UUID, *, now: dt.datetime) -> None:
    session.execute(
        update(OutboxMessage)
        .where(OutboxMessage.id == message_id)
        .values(processed_at=now, locked_by=None, locked_until=None, last_error=None)
    )


def backoff_seconds(attempts: int) -> int:
    return int(min(2**attempts, 600))


def mark_failed(
    session: Session,
    message: OutboxMessage,
    *,
    error: str,
    now: dt.datetime,
    max_attempts: int,
) -> bool:
    """Record a failed delivery. Returns True when the message was dead-lettered."""
    attempts = message.attempts + 1
    dead = attempts >= max_attempts
    session.execute(
        update(OutboxMessage)
        .where(OutboxMessage.id == message.id)
        .values(
            attempts=attempts,
            last_error=error[:2000],
            locked_by=None,
            locked_until=None,
            available_at=now + dt.timedelta(seconds=backoff_seconds(attempts)),
            dead_lettered_at=now if dead else None,
        )
    )
    return dead

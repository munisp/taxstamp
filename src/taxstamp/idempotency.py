"""Durable idempotency for mutating requests.

A unique (scope, key) row is claimed before any work happens. A replay with the same
payload returns the stored response; a replay with a different payload is rejected so
that a client cannot reuse a key to smuggle a different effect.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from taxstamp.errors import Conflict, IdempotencyKeyReused
from taxstamp.jsontypes import JsonObject
from taxstamp.models import IdempotencyRecord

STATE_IN_PROGRESS = "in_progress"
STATE_COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ReplayedResponse:
    status: int
    body: JsonObject


def claim(
    session: Session,
    *,
    scope: str,
    key: str,
    principal_id: uuid.UUID,
    request_hash: str,
    now: dt.datetime,
    ttl_seconds: int,
) -> ReplayedResponse | None:
    """Claim the key, or return the stored response for an identical replay.

    Raises ``IdempotencyKeyReused`` when the key was used with a different payload and
    ``Conflict`` when an earlier attempt is still in flight.
    """
    existing = session.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.idempotency_key == key
        )
    ).scalar_one_or_none()
    if existing is None:
        record = IdempotencyRecord(
            scope=scope,
            idempotency_key=key,
            principal_id=principal_id,
            request_hash=request_hash,
            state=STATE_IN_PROGRESS,
            expires_at=now + dt.timedelta(seconds=ttl_seconds),
        )
        savepoint = session.begin_nested()
        session.add(record)
        try:
            session.flush()
        except IntegrityError as exc:
            savepoint.rollback()
            raise Conflict("concurrent request with the same idempotency key") from exc
        savepoint.commit()
        return None

    if existing.request_hash != request_hash:
        raise IdempotencyKeyReused("idempotency key was already used with a different payload")
    if existing.state == STATE_IN_PROGRESS:
        raise Conflict("an earlier request with this idempotency key is still in progress")
    if existing.response_status is None or existing.response_body is None:
        raise Conflict("stored idempotent response is incomplete")
    return ReplayedResponse(status=existing.response_status, body=existing.response_body)


def complete(
    session: Session,
    *,
    scope: str,
    key: str,
    status: int,
    body: JsonObject,
    now: dt.datetime,
) -> None:
    record = session.execute(
        select(IdempotencyRecord)
        .where(IdempotencyRecord.scope == scope, IdempotencyRecord.idempotency_key == key)
        .with_for_update()
    ).scalar_one()
    record.state = STATE_COMPLETED
    record.response_status = status
    record.response_body = body
    record.completed_at = now
    session.flush()

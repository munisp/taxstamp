"""Outbox leasing, retry, backoff and dead-lettering."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import Session

from taxstamp import outbox

pytestmark = pytest.mark.integration
NOW = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)


def _enqueue(db: Session, dedupe: str) -> None:
    outbox.enqueue(
        db,
        aggregate_type="order",
        aggregate_id=uuid.uuid4(),
        event_type="order.awaiting_payment",
        dedupe_key=dedupe,
        payload={"order_id": str(uuid.uuid4())},
        available_at=NOW,
    )


def test_duplicate_dedupe_key_enqueues_once(db: Session) -> None:
    key = uuid.uuid4().hex
    _enqueue(db, key)
    _enqueue(db, key)
    db.commit()
    claimed = outbox.claim_batch(db, worker_id="w1", now=NOW, lease_seconds=60, batch_size=10)
    assert len(claimed) == 1


def test_lease_prevents_double_claim_until_it_expires(db: Session) -> None:
    _enqueue(db, uuid.uuid4().hex)
    db.commit()
    first = outbox.claim_batch(db, worker_id="w1", now=NOW, lease_seconds=60, batch_size=10)
    assert len(first) == 1
    db.commit()
    second = outbox.claim_batch(
        db, worker_id="w2", now=NOW + dt.timedelta(seconds=30), lease_seconds=60, batch_size=10
    )
    assert second == []
    db.commit()
    third = outbox.claim_batch(
        db, worker_id="w2", now=NOW + dt.timedelta(seconds=120), lease_seconds=60, batch_size=10
    )
    assert len(third) == 1


def test_failure_backs_off_then_dead_letters(db: Session) -> None:
    _enqueue(db, uuid.uuid4().hex)
    db.commit()
    message = outbox.claim_batch(db, worker_id="w1", now=NOW, lease_seconds=60, batch_size=1)[0]
    dead = False
    for attempt in range(4):
        dead = outbox.mark_failed(
            db,
            message,
            error="boom",
            now=NOW + dt.timedelta(seconds=attempt),
            max_attempts=3,
        )
        db.commit()
    assert dead
    assert message.dead_lettered_at is not None
    assert (
        outbox.claim_batch(
            db, worker_id="w1", now=NOW + dt.timedelta(hours=1), lease_seconds=60, batch_size=10
        )
        == []
    )


def test_backoff_is_bounded_and_monotonic() -> None:
    values = [outbox.backoff_seconds(attempt) for attempt in range(1, 12)]
    assert values == sorted(values)
    assert max(values) <= 600

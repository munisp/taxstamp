"""Kafka projection keeps the transactional outbox retryable and observable."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy.orm import Session

from taxstamp import outbox
from taxstamp.projection import ProjectionEnvelope
from taxstamp.worker.relay import relay_once

pytestmark = pytest.mark.integration


@dataclass
class RecordingPublisher:
    published: list[ProjectionEnvelope] = field(default_factory=list)
    failure: Exception | None = None

    def publish(self, envelope: ProjectionEnvelope) -> None:
        if self.failure is not None:
            raise self.failure
        self.published.append(envelope)


def _enqueue(db: Session) -> None:
    outbox.enqueue(
        db,
        aggregate_type="order",
        aggregate_id=uuid.uuid4(),
        event_type="order.awaiting_payment",
        dedupe_key=uuid.uuid4().hex,
        payload={"order_id": str(uuid.uuid4())},
        available_at=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
    )


def test_relay_projects_a_successful_outbox_event(runtime, db: Session) -> None:  # type: ignore[no-untyped-def]
    publisher = RecordingPublisher()
    runtime.kafka_publisher = publisher
    _enqueue(db)
    db.commit()

    stats = relay_once(runtime, worker_id="kafka-test")

    assert stats.delivered == 1
    assert len(publisher.published) == 1
    assert publisher.published[0].event_type == "order.awaiting_payment"


def test_projection_failure_leaves_the_outbox_message_retryable(runtime, db: Session) -> None:  # type: ignore[no-untyped-def]
    runtime.kafka_publisher = RecordingPublisher(failure=ConnectionError("broker unavailable"))
    _enqueue(db)
    db.commit()

    stats = relay_once(runtime, worker_id="kafka-test")

    assert stats.delivered == 0
    assert stats.failed == 1
    assert stats.dead_lettered == 0

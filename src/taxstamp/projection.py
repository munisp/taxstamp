"""Kafka-compatible transactional-outbox projection boundary.

The database outbox remains the source of delivery truth. The publisher is invoked only
after a local handler succeeds; an unavailable broker raises, leaving the outbox message
retryable and observable rather than silently dropping an external projection.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from typing import Protocol

from taxstamp.config import Settings
from taxstamp.jsontypes import JsonObject
from taxstamp.models import OutboxMessage


@dataclass(frozen=True, slots=True)
class ProjectionEnvelope:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    dedupe_key: str
    occurred_at: str
    payload: JsonObject

    @classmethod
    def from_outbox(cls, message: OutboxMessage, *, occurred_at: dt.datetime) -> ProjectionEnvelope:
        return cls(
            event_id=str(message.id),
            event_type=message.event_type,
            aggregate_type=message.aggregate_type,
            aggregate_id=str(message.aggregate_id),
            dedupe_key=message.dedupe_key,
            occurred_at=occurred_at.astimezone(dt.UTC).isoformat(),
            payload=message.payload,
        )

    def document(self) -> JsonObject:
        return asdict(self)


class ProjectionPublisher(Protocol):
    def publish(self, envelope: ProjectionEnvelope) -> None: ...


class KafkaProjectionPublisher:
    """Synchronous Kafka publisher used by the worker's transactional-outbox relay."""

    def __init__(self, settings: Settings) -> None:
        if not settings.kafka_bootstrap_servers:
            raise ValueError("Kafka projection requires kafka_bootstrap_servers")
        # Import lazily so a broker is an optional runtime dependency for deployments
        # where projection is intentionally disabled.
        from kafka import KafkaProducer  # type: ignore[import-untyped]

        self._topic = settings.kafka_topic
        self._timeout_seconds = settings.kafka_publish_timeout_seconds
        self._producer = KafkaProducer(
            bootstrap_servers=[server.strip() for server in settings.kafka_bootstrap_servers.split(",")],
            security_protocol=settings.kafka_security_protocol,
            acks="all",
            retries=5,
            key_serializer=lambda value: value.encode("utf-8"),
            value_serializer=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
        )

    def publish(self, envelope: ProjectionEnvelope) -> None:
        future = self._producer.send(
            self._topic,
            key=f"{envelope.aggregate_type}:{envelope.aggregate_id}",
            value=envelope.document(),
            headers=[
                ("event_type", envelope.event_type.encode("utf-8")),
                ("event_id", envelope.event_id.encode("utf-8")),
            ],
        )
        future.get(timeout=self._timeout_seconds)

    def close(self) -> None:
        self._producer.flush(timeout=self._timeout_seconds)
        self._producer.close(timeout=self._timeout_seconds)

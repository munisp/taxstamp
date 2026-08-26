"""Structured logging and Prometheus metrics.

Logs are JSON, carry a request id, and pass through a redactor so that tokens,
signatures and secure codes cannot be written to the log stream.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TypedDict

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from structlog.types import EventDict, WrappedLogger

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "token",
        "api_token",
        "secret",
        "signature",
        "secure_code",
        "password",
        "x-signature",
    }
)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)
REDACTED = "[redacted]"


def _redact(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        else:
            value = event_dict[key]
            if isinstance(value, str):
                event_dict[key] = _BEARER.sub(f"Bearer {REDACTED}", value)
    return event_dict


def configure_logging(*, service: str, revision: str, level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service, revision=revision)


class Metrics(TypedDict):
    requests: Counter
    latency: Histogram
    verifications: Counter
    outbox_pending: Gauge
    outbox_dead: Gauge
    stamps_issued: Counter
    money_posted: Counter
    reconciliation_findings: Gauge
    kafka_projection_published: Counter
    kafka_projection_failures: Counter
    kafka_projection_duration: Histogram


def build_metrics(registry: CollectorRegistry) -> Metrics:
    return Metrics(
        requests=Counter(
            "taxstamp_http_requests_total",
            "HTTP requests by route, method and status class",
            ["route", "method", "status"],
            registry=registry,
        ),
        latency=Histogram(
            "taxstamp_http_request_duration_seconds",
            "HTTP request latency",
            ["route", "method"],
            registry=registry,
        ),
        verifications=Counter(
            "taxstamp_verifications_total",
            "Verification attempts by outcome",
            ["outcome"],
            registry=registry,
        ),
        outbox_pending=Gauge(
            "taxstamp_outbox_pending",
            "Outbox messages awaiting delivery",
            registry=registry,
        ),
        outbox_dead=Gauge(
            "taxstamp_outbox_dead_lettered",
            "Outbox messages that exhausted their retries",
            registry=registry,
        ),
        stamps_issued=Counter(
            "taxstamp_stamps_issued_total",
            "Stamps issued",
            registry=registry,
        ),
        money_posted=Counter(
            "taxstamp_money_posted_minor_total",
            "Money posted to the ledger, in minor units",
            ["account"],
            registry=registry,
        ),
        reconciliation_findings=Gauge(
            "taxstamp_reconciliation_findings",
            "Open reconciliation findings by kind",
            ["kind"],
            registry=registry,
        ),
        kafka_projection_published=Counter(
            "taxstamp_kafka_projection_published_total",
            "Outbox events successfully published to Kafka by event type",
            ["event_type"],
            registry=registry,
        ),
        kafka_projection_failures=Counter(
            "taxstamp_kafka_projection_failures_total",
            "Kafka projection failures by event type and exception type",
            ["event_type", "error_type"],
            registry=registry,
        ),
        kafka_projection_duration=Histogram(
            "taxstamp_kafka_projection_duration_seconds",
            "Kafka publication duration by event type",
            ["event_type"],
            registry=registry,
        ),
    )

"""Outbox relay and periodic maintenance jobs."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import structlog

from taxstamp import outbox
from taxstamp.db import transaction
from taxstamp.models import OutboxMessage
from taxstamp.runtime import Runtime
from taxstamp.services.reconciliation import run_reconciliation
from taxstamp.services.stamps import expire_due_stamps
from taxstamp.worker.handlers import handler_for

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RelayStats:
    claimed: int
    delivered: int
    failed: int
    dead_lettered: int


def relay_once(runtime: Runtime, *, worker_id: str) -> RelayStats:
    now = runtime.clock.now()
    with transaction(runtime.session_factory) as session:
        messages = outbox.claim_batch(
            session,
            worker_id=worker_id,
            now=now,
            lease_seconds=runtime.settings.outbox_lease_seconds,
            batch_size=runtime.settings.outbox_batch_size,
        )
        claimed = [
            (message.id, message.event_type, message.payload, message.attempts) for message in messages
        ]

    delivered = failed = dead = 0
    for message_id, event_type, payload, attempts in claimed:
        try:
            with transaction(runtime.session_factory) as session:
                message = session.get_one(OutboxMessage, message_id)
                handler = handler_for(message)
                handler(runtime, session, payload)
                outbox.mark_processed(session, message_id, now=runtime.clock.now())
            delivered += 1
        except Exception as exc:  # noqa: BLE001 - a failed delivery must not stop the relay
            failed += 1
            logger.warning(
                "outbox_delivery_failed",
                message_id=str(message_id),
                event_type=event_type,
                attempts=attempts,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            with transaction(runtime.session_factory) as session:
                message = session.get_one(OutboxMessage, message_id)
                if outbox.mark_failed(
                    session,
                    message,
                    error=f"{type(exc).__name__}: {exc}",
                    now=runtime.clock.now(),
                    max_attempts=runtime.settings.outbox_max_attempts,
                ):
                    dead += 1
    return RelayStats(claimed=len(claimed), delivered=delivered, failed=failed, dead_lettered=dead)


def expire_stamps_once(runtime: Runtime, *, limit: int = 5_000) -> int:
    with transaction(runtime.session_factory) as session:
        return expire_due_stamps(
            session,
            now=runtime.clock.now(),
            limit=limit,
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )


def reconcile_once(runtime: Runtime) -> bool:
    with transaction(runtime.session_factory) as session:
        report = run_reconciliation(
            session,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
        )
        for kind, count in report.counts_by_kind().items():
            runtime.metrics["reconciliation_findings"].labels(kind=kind).set(count)
        for finding in report.findings:
            logger.error("reconciliation_finding", kind=finding.kind, count=finding.count)
        return report.clean


def next_due(last_run: dt.datetime | None, now: dt.datetime, interval_seconds: int) -> bool:
    return last_run is None or (now - last_run).total_seconds() >= interval_seconds

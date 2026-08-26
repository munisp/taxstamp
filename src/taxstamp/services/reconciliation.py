"""Reconciliation.

Every run recomputes the platform's invariants from stored data and records the result.
A finding is never auto-corrected: it is reported so an operator can act, because silent
repair of a funds or issuance discrepancy would destroy the evidence of its cause.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import verify_audit_chain
from taxstamp.enums import OrderStatus, PaymentIntentStatus, ReceiptStatus, TigerBeetleLedgerIntentState
from taxstamp.jsontypes import JsonObject, JsonValue
from taxstamp.ledger import Account, account_balance, unbalanced_journals
from taxstamp.models import (
    Journal,
    Order,
    OutboxMessage,
    PaymentIntent,
    PaymentReceipt,
    ReconciliationRun,
    Stamp,
    StampBatch,
    TigerBeetleLedgerIntent,
)
from taxstamp.services.external_settlement_reconciliation import (
    ExpectedSettlement,
    ExternalSettlement,
    SettlementProvider,
    reconcile_external_settlements,
)


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    count: int
    detail: JsonObject


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    findings: tuple[Finding, ...]
    checks_run: int

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_document(self) -> JsonObject:
        return {
            "clean": self.clean,
            "checks_run": self.checks_run,
            "findings": [
                {"kind": finding.kind, "count": finding.count, "detail": finding.detail}
                for finding in self.findings
            ],
        }


def _unbalanced_journal_finding(session: Session) -> Finding | None:
    rows = unbalanced_journals(session)
    if not rows:
        return None
    return Finding(
        kind="unbalanced_journal",
        count=len(rows),
        detail={"journals": [str(journal_id) for journal_id, _, _ in rows[:20]]},
    )


def _settlement_conservation(session: Session) -> Finding | None:
    """Collections must equal the duty and VAT recognised plus unapplied receipts."""
    currencies = list(session.execute(select(Journal.currency).distinct()).scalars().all())
    mismatches: list[JsonValue] = []
    for currency in currencies:
        collections = account_balance(session, Account.BANK_COLLECTIONS, currency).minor
        duty = -account_balance(session, Account.DUTY_PAYABLE, currency).minor
        vat = -account_balance(session, Account.VAT_PAYABLE, currency).minor
        unapplied = -account_balance(session, Account.UNAPPLIED_RECEIPTS, currency).minor
        if collections != duty + vat + unapplied:
            mismatches.append(
                {
                    "currency": currency,
                    "collections_minor": collections,
                    "duty_minor": duty,
                    "vat_minor": vat,
                    "unapplied_minor": unapplied,
                }
            )
    if not mismatches:
        return None
    return Finding(kind="funds_not_conserved", count=len(mismatches), detail={"currencies": mismatches})


def _settled_orders_without_matching_receipt(session: Session) -> Finding | None:
    rows = list(
        session.execute(
            select(Order.order_ref)
            .join(PaymentIntent, PaymentIntent.order_id == Order.id)
            .outerjoin(
                PaymentReceipt,
                (PaymentReceipt.payment_intent_id == PaymentIntent.id)
                & (PaymentReceipt.status == ReceiptStatus.MATCHED.value),
            )
            .where(
                Order.status.in_(
                    [OrderStatus.PAID.value, OrderStatus.ISSUING.value, OrderStatus.ISSUED.value]
                ),
                PaymentReceipt.id.is_(None),
            )
            .limit(50)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    return Finding(kind="paid_order_without_receipt", count=len(rows), detail={"orders": list(rows)})


def _settled_intents_without_paid_order(session: Session) -> Finding | None:
    rows = list(
        session.execute(
            select(PaymentIntent.reference)
            .join(Order, Order.id == PaymentIntent.order_id)
            .where(
                PaymentIntent.status == PaymentIntentStatus.SETTLED.value,
                Order.status.notin_(
                    [OrderStatus.PAID.value, OrderStatus.ISSUING.value, OrderStatus.ISSUED.value]
                ),
            )
            .limit(50)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    return Finding(
        kind="settled_intent_without_paid_order", count=len(rows), detail={"references": list(rows)}
    )


def _issuance_count_mismatch(session: Session) -> Finding | None:
    actual = select(Stamp.batch_id, func.count().label("actual")).group_by(Stamp.batch_id).subquery()
    rows = list(
        session.execute(
            select(StampBatch.id, StampBatch.issued_count, StampBatch.requested_count, actual.c.actual)
            .outerjoin(actual, actual.c.batch_id == StampBatch.id)
            .where(
                (StampBatch.issued_count != func.coalesce(actual.c.actual, 0))
                | (StampBatch.issued_count > StampBatch.requested_count)
            )
            .limit(50)
        ).all()
    )
    if not rows:
        return None
    return Finding(
        kind="issuance_count_mismatch",
        count=len(rows),
        detail={
            "batches": [
                {
                    "batch_id": str(row[0]),
                    "recorded": int(row[1]),
                    "requested": int(row[2]),
                    "actual": int(row[3] or 0),
                }
                for row in rows
            ]
        },
    )


def _duplicate_serials(session: Session) -> Finding | None:
    rows = list(
        session.execute(
            select(Stamp.serial, func.count()).group_by(Stamp.serial).having(func.count() > 1).limit(50)
        ).all()
    )
    if not rows:
        return None
    return Finding(kind="duplicate_serial", count=len(rows), detail={"serials": [row[0] for row in rows]})


def _outbox_health(session: Session, *, now: dt.datetime, stale_after_seconds: int) -> Finding | None:
    dead = int(
        session.execute(
            select(func.count()).select_from(OutboxMessage).where(OutboxMessage.dead_lettered_at.is_not(None))
        ).scalar_one()
    )
    stale = int(
        session.execute(
            select(func.count())
            .select_from(OutboxMessage)
            .where(
                OutboxMessage.processed_at.is_(None),
                OutboxMessage.dead_lettered_at.is_(None),
                OutboxMessage.created_at < now - dt.timedelta(seconds=stale_after_seconds),
            )
        ).scalar_one()
    )
    if dead == 0 and stale == 0:
        return None
    return Finding(kind="outbox_backlog", count=dead + stale, detail={"dead_lettered": dead, "stale": stale})


def _tigerbeetle_intent_health(
    session: Session, *, now: dt.datetime, stale_after_seconds: int
) -> Finding | None:
    stale_states = (
        TigerBeetleLedgerIntentState.READY.value,
        TigerBeetleLedgerIntentState.SUBMISSION_UNCERTAIN.value,
        TigerBeetleLedgerIntentState.EXTERNAL_CONFIRMED.value,
    )
    rows = list(
        session.execute(
            select(TigerBeetleLedgerIntent.id, TigerBeetleLedgerIntent.state)
            .where(
                (TigerBeetleLedgerIntent.state == TigerBeetleLedgerIntentState.QUARANTINED.value)
                | (
                    TigerBeetleLedgerIntent.state.in_(stale_states)
                    & (TigerBeetleLedgerIntent.created_at < now - dt.timedelta(seconds=stale_after_seconds))
                )
            )
            .order_by(TigerBeetleLedgerIntent.created_at)
            .limit(50)
        ).all()
    )
    if not rows:
        return None
    by_state: JsonObject = {}
    for _, state in rows:
        existing = by_state.get(state, 0)
        if isinstance(existing, bool) or not isinstance(existing, int):
            raise TypeError("TigerBeetle reconciliation state count is not an integer")
        by_state[state] = existing + 1
    return Finding(
        kind="tigerbeetle_intent_control_failure",
        count=len(rows),
        detail={"states": by_state, "intent_ids": [str(intent_id) for intent_id, _ in rows]},
    )


def _audit_chain(session: Session, *, secret: str) -> Finding | None:
    verification = verify_audit_chain(session, secret=secret)
    if verification.intact:
        return None
    return Finding(
        kind="audit_chain_broken",
        count=1,
        detail={
            "first_bad_seq": verification.first_bad_seq,
            "reason": verification.reason,
            "events_checked": verification.events_checked,
        },
    )


def _external_settlement_findings(
    session: Session,
    *,
    snapshots: tuple[ExternalSettlement, ...],
    providers: tuple[SettlementProvider, ...],
) -> tuple[Finding, ...]:
    expected = tuple(
        ExpectedSettlement(reference=row.reference, amount_minor=row.amount_minor, currency=row.currency)
        for row in session.execute(
            select(PaymentIntent).where(PaymentIntent.status == PaymentIntentStatus.SETTLED.value)
        ).scalars()
    )
    return tuple(
        Finding(kind=finding.kind, count=finding.count, detail=finding.detail)
        for finding in reconcile_external_settlements(expected, snapshots, providers=providers)
    )


def run_reconciliation(
    session: Session,
    *,
    now: dt.datetime,
    audit_secret: str,
    outbox_stale_after_seconds: int = 900,
    external_settlement_snapshots: tuple[ExternalSettlement, ...] = (),
    external_settlement_providers: tuple[SettlementProvider, ...] = (),
) -> ReconciliationReport:
    checks = [
        _unbalanced_journal_finding(session),
        _settlement_conservation(session),
        _settled_orders_without_matching_receipt(session),
        _settled_intents_without_paid_order(session),
        _issuance_count_mismatch(session),
        _duplicate_serials(session),
        _outbox_health(session, now=now, stale_after_seconds=outbox_stale_after_seconds),
        _tigerbeetle_intent_health(session, now=now, stale_after_seconds=outbox_stale_after_seconds),
        _audit_chain(session, secret=audit_secret),
    ]
    external_findings = _external_settlement_findings(
        session,
        snapshots=external_settlement_snapshots,
        providers=external_settlement_providers,
    )
    findings = tuple(finding for finding in checks if finding is not None) + external_findings
    report = ReconciliationReport(
        findings=findings,
        checks_run=len(checks) + (1 if external_settlement_providers else 0),
    )
    session.add(
        ReconciliationRun(
            kind="full",
            started_at=now,
            finished_at=now,
            status="clean" if report.clean else "findings",
            findings=report.as_document(),
        )
    )
    session.flush()
    return report

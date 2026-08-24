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
from taxstamp.enums import (
    ORDERING_LICENCE_TYPES,
    LicenceStatus,
    OrderStatus,
    PaymentIntentStatus,
    ReceiptStatus,
    ResolutionKind,
)
from taxstamp.jsontypes import JsonArray, JsonObject, JsonValue
from taxstamp.ledger import Account, account_balance, unbalanced_journals
from taxstamp.models import (
    Journal,
    Licence,
    Order,
    OutboxMessage,
    PaymentIntent,
    PaymentReceipt,
    ReceiptResolution,
    ReconciliationRun,
    Stamp,
    StampBatch,
)
from taxstamp.services.accountability import unaccounted_dispositions
from taxstamp.services.customs import consignments_short_of_stamps
from taxstamp.services.exports import export_integrity_failures
from taxstamp.services.registry import overlapping_tariffs
from taxstamp.services.traceability import units_with_broken_conservation
from taxstamp.services.transparency import checkpoints_with_broken_root

#: Every kind a run can report. Published unconditionally so a resolved finding
#: reports zero instead of keeping its last non-zero value in the gauge.
FINDING_KINDS: tuple[str, ...] = (
    "unbalanced_journal",
    "funds_not_conserved",
    "paid_order_without_receipt",
    "settled_intent_without_paid_order",
    "issuance_count_mismatch",
    "duplicate_serial",
    "outbox_backlog",
    "audit_chain_broken",
    "overlapping_tariff",
    "order_without_effective_licence",
    "disposition_not_voided",
    "unit_quantity_not_conserved",
    "consignment_short_of_stamps",
    "export_integrity_broken",
    "checkpoint_root_broken",
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

    def counts_by_kind(self) -> dict[str, int]:
        """Every known kind, with zero for kinds this run did not report."""
        counts = dict.fromkeys(FINDING_KINDS, 0)
        for finding in self.findings:
            counts[finding.kind] = finding.count
        return counts

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
    """A paid order must be backed by a matched receipt or an applied resolution.

    Treasury can settle an order from funds held under a different reference, which is
    evidenced by a resolution row rather than a matched receipt.
    """
    rows = list(
        session.execute(
            select(Order.order_ref)
            .join(PaymentIntent, PaymentIntent.order_id == Order.id)
            .outerjoin(
                PaymentReceipt,
                (PaymentReceipt.payment_intent_id == PaymentIntent.id)
                & (PaymentReceipt.status == ReceiptStatus.MATCHED.value),
            )
            .outerjoin(
                ReceiptResolution,
                (ReceiptResolution.order_id == Order.id)
                & (ReceiptResolution.kind == ResolutionKind.APPLIED.value),
            )
            .where(
                Order.status.in_(
                    [OrderStatus.PAID.value, OrderStatus.ISSUING.value, OrderStatus.ISSUED.value]
                ),
                PaymentReceipt.id.is_(None),
                ReceiptResolution.id.is_(None),
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


def _overlapping_tariffs(session: Session) -> Finding | None:
    overlaps = overlapping_tariffs(session)
    if not overlaps:
        return None
    return Finding(
        kind="overlapping_tariff",
        count=len(overlaps),
        detail={
            "pairs": [
                {"product_category": category, "tariff_ids": [str(first), str(second)]}
                for category, first, second in overlaps[:20]
            ]
        },
    )


def _orders_without_effective_licence(session: Session, *, now: dt.datetime) -> Finding | None:
    """Live orders whose licence has since lapsed, been suspended or been revoked.

    Not a fraud signal on its own: it tells the authority which in-flight procurement
    now sits behind an unlicensed entity and needs a decision.
    """
    rows = list(
        session.execute(
            select(Order.order_ref, Licence.licence_number, Licence.status)
            .outerjoin(Licence, Licence.id == Order.licence_id)
            .where(
                Order.status.notin_(
                    [
                        OrderStatus.ISSUED.value,
                        OrderStatus.CANCELLED.value,
                        OrderStatus.REJECTED.value,
                        OrderStatus.COMPLIANCE_REJECTED.value,
                    ]
                ),
                Order.licence_id.is_(None)
                | (Licence.status != LicenceStatus.ACTIVE.value)
                | (Licence.valid_to.is_not(None) & (Licence.valid_to <= now))
                | (Licence.licence_type.notin_([kind.value for kind in ORDERING_LICENCE_TYPES])),
            )
            .limit(50)
        ).all()
    )
    if not rows:
        return None
    return Finding(
        kind="order_without_effective_licence",
        count=len(rows),
        detail={
            "orders": [
                {"order_ref": row[0], "licence_number": row[1], "licence_status": row[2]} for row in rows
            ]
        },
    )


def _dispositions_not_voided(session: Session) -> Finding | None:
    rows = unaccounted_dispositions(session)
    if not rows:
        return None
    return Finding(
        kind="disposition_not_voided",
        count=len(rows),
        detail={
            "dispositions": [
                {"disposition_id": str(disposition_id), "declared": declared, "still_live": live}
                for disposition_id, declared, live in rows[:20]
            ]
        },
    )


def _units_not_conserved(session: Session) -> Finding | None:
    rows = units_with_broken_conservation(session)
    if not rows:
        return None
    return Finding(
        kind="unit_quantity_not_conserved",
        count=len(rows),
        detail={
            "units": [
                {"unit_code": code, "recorded": recorded, "contained": contained}
                for code, recorded, contained in rows[:20]
            ]
        },
    )


def _consignments_short_of_stamps(session: Session) -> Finding | None:
    rows = consignments_short_of_stamps(session)
    if not rows:
        return None
    return Finding(
        kind="consignment_short_of_stamps",
        count=len(rows),
        detail={
            "consignments": [
                {"consignment_ref": ref, "declared": declared, "linked": linked}
                for ref, declared, linked in rows[:20]
            ]
        },
    )


def _export_integrity(session: Session, *, export_secret: str) -> Finding | None:
    refs = export_integrity_failures(session, export_secret=export_secret)
    if not refs:
        return None
    listed: JsonArray = list(refs[:20])
    return Finding(kind="export_integrity_broken", count=len(refs), detail={"export_refs": listed})


def _checkpoint_integrity(session: Session, *, checkpoint_secret: str) -> Finding | None:
    refs = checkpoints_with_broken_root(session, checkpoint_secret=checkpoint_secret)
    if not refs:
        return None
    listed: JsonArray = list(refs[:20])
    return Finding(kind="checkpoint_root_broken", count=len(refs), detail={"checkpoint_refs": listed})


def run_reconciliation(
    session: Session,
    *,
    now: dt.datetime,
    audit_secret: str,
    export_secret: str,
    transparency_secret: str,
    outbox_stale_after_seconds: int = 900,
) -> ReconciliationReport:
    checks = [
        _unbalanced_journal_finding(session),
        _settlement_conservation(session),
        _settled_orders_without_matching_receipt(session),
        _settled_intents_without_paid_order(session),
        _issuance_count_mismatch(session),
        _duplicate_serials(session),
        _outbox_health(session, now=now, stale_after_seconds=outbox_stale_after_seconds),
        _audit_chain(session, secret=audit_secret),
        _overlapping_tariffs(session),
        _orders_without_effective_licence(session, now=now),
        _dispositions_not_voided(session),
        _units_not_conserved(session),
        _consignments_short_of_stamps(session),
        _export_integrity(session, export_secret=export_secret),
        _checkpoint_integrity(session, checkpoint_secret=transparency_secret),
    ]
    findings = tuple(finding for finding in checks if finding is not None)
    report = ReconciliationReport(findings=findings, checks_run=len(checks))
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

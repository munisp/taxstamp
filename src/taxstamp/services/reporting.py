"""Programme reporting: operational KPIs and revenue at risk.

Every figure is a count or an exact integer sum over stored records, with the query
window stated alongside it. Revenue at risk is decomposed into its sources so that a
single headline number is never presented without the evidence behind it:

* duty on stamp-liable consignments whose linked stamps do not cover what was declared,
  priced at the tariff effective at the close of the window;
* estimated duty on goods currently seized or forfeited;
* revenue attributed to open enforcement cases, which is itself derived from seizures.

Revenue at risk is an exposure figure, not a receivable: it is what the records suggest
may have escaped duty, and it must never be reported as an assessed or collectable
liability. Nothing here extrapolates to unobserved trade.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from taxstamp.enums import (
    CLOSED_CASE_STATUSES,
    STAMP_LIABLE_REGIMES,
    ConsignmentStatus,
    OrderStatus,
    ReceiptStatus,
    Role,
    SeizureStatus,
    StampStatus,
)
from taxstamp.errors import ValidationFailed
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import (
    Anomaly,
    Consignment,
    ConsignmentStamp,
    ConsumerVerification,
    EnforcementCase,
    Order,
    PaymentReceipt,
    Product,
    Seizure,
    Stamp,
    Tariff,
    Verification,
)
from taxstamp.services.context import Actor

#: Programme-wide reporting is a supervisory view across tenants.
REPORT_READERS: frozenset[Role] = frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN})

MAX_WINDOW = dt.timedelta(days=400)


@dataclass(frozen=True, slots=True)
class Window:
    start: dt.datetime
    end: dt.datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValidationFailed("the reporting window must end after it starts")
        if self.end - self.start > MAX_WINDOW:
            raise ValidationFailed(f"the reporting window may not exceed {MAX_WINDOW.days} days")


def programme_kpis(session: Session, *, actor: Actor, window: Window) -> JsonObject:
    """Operational and fiscal counters for one window, each with its own definition."""
    actor.require_role(*REPORT_READERS)
    issued = _count(session, select(func.count(Stamp.id)).where(_between(Stamp.issued_at, window)))
    activated = _count(session, select(func.count(Stamp.id)).where(_between(Stamp.activated_at, window)))
    voided = _count(
        session,
        select(func.count(Stamp.id)).where(
            Stamp.status == StampStatus.VOID.value, _between(Stamp.issued_at, window)
        ),
    )
    collected = _sum(
        session,
        select(func.coalesce(func.sum(PaymentReceipt.amount_minor), 0)).where(
            PaymentReceipt.status == ReceiptStatus.MATCHED.value,
            _between(PaymentReceipt.value_date, window),
        ),
    )
    orders_paid = _count(
        session,
        select(func.count(Order.id)).where(
            Order.status.in_((OrderStatus.PAID.value, OrderStatus.ISSUED.value)),
            _between(Order.updated_at, window),
        ),
    )
    field_outcomes = _grouped(
        session,
        select(Verification.outcome, func.count(Verification.id))
        .where(_between(Verification.occurred_at, window))
        .group_by(Verification.outcome),
    )
    consumer_outcomes = _grouped(
        session,
        select(ConsumerVerification.outcome, func.count(ConsumerVerification.id))
        .where(_between(ConsumerVerification.occurred_at, window))
        .group_by(ConsumerVerification.outcome),
    )
    anomalies = _grouped(
        session,
        select(Anomaly.kind, func.count(Anomaly.id))
        .where(_between(Anomaly.detected_at, window))
        .group_by(Anomaly.kind),
    )
    cases_opened = _count(
        session,
        select(func.count(EnforcementCase.id)).where(_between(EnforcementCase.created_at, window)),
    )
    cases_closed = _count(
        session,
        select(func.count(EnforcementCase.id)).where(
            EnforcementCase.status.in_([status.value for status in CLOSED_CASE_STATUSES]),
            _between(EnforcementCase.closed_at, window),
        ),
    )
    seizures = _count(session, select(func.count(Seizure.id)).where(_between(Seizure.created_at, window)))
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "stamps": {"issued": issued, "activated": activated, "voided": voided},
        "revenue": {
            "collected_minor": collected,
            "currency": "NGN",
            "orders_paid_or_issued": orders_paid,
            "basis": "matched payment receipts with a value date inside the window",
        },
        "verifications": {"field": field_outcomes, "consumer": consumer_outcomes},
        "detection": {"findings_by_kind": anomalies},
        "enforcement": {
            "cases_opened": cases_opened,
            "cases_closed": cases_closed,
            "seizures_recorded": seizures,
        },
    }


def revenue_at_risk(session: Session, *, actor: Actor, window: Window) -> JsonObject:
    """Exposure implied by the records, itemised by source.

    The components are deliberately not summed into a single number without their
    breakdown, and each component states the evidence it was computed from.
    """
    actor.require_role(*REPORT_READERS)
    unstamped = _consignment_exposure(session, window=window)
    seized = _seizure_exposure(session, window=window)
    open_cases = _sum(
        session,
        select(func.coalesce(func.sum(EnforcementCase.revenue_at_risk_minor), 0)).where(
            EnforcementCase.status.notin_([status.value for status in CLOSED_CASE_STATUSES]),
            _between(EnforcementCase.created_at, window),
        ),
    )
    components: JsonArray = [
        {
            "source": "consignment_stamp_shortfall",
            "amount_minor": unstamped["amount_minor"],
            "observations": unstamped["consignments"],
            "basis": (
                "declared quantity less linked stamps on stamp-liable consignments, priced "
                "at the tariff effective at the end of the window"
            ),
        },
        {
            "source": "goods_in_custody",
            "amount_minor": seized["amount_minor"],
            "observations": seized["seizures"],
            "basis": "estimated duty recorded on seizures still held or forfeited",
        },
        {
            "source": "open_enforcement_cases",
            "amount_minor": open_cases,
            "observations": None,
            "basis": "revenue attributed to open cases, itself derived from their seizures",
        },
    ]
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "currency": "NGN",
        "components": components,
        "caveat": (
            "An exposure figure derived from observed records only. It is not an assessed "
            "liability, contains no extrapolation to unobserved trade, and overlaps "
            "between components (a seizure attached to an open case) are not netted."
        ),
    }


def _consignment_exposure(session: Session, *, window: Window) -> dict[str, int]:
    linked = (
        select(
            ConsignmentStamp.consignment_id.label("consignment_id"),
            func.count(ConsignmentStamp.id).label("linked"),
        )
        .group_by(ConsignmentStamp.consignment_id)
        .subquery()
    )
    rows = session.execute(
        select(
            Product.product_category,
            Consignment.declared_quantity,
            func.coalesce(linked.c.linked, 0),
        )
        .join(Product, Product.id == Consignment.product_id)
        .outerjoin(linked, linked.c.consignment_id == Consignment.id)
        .where(
            Consignment.regime.in_([regime.value for regime in STAMP_LIABLE_REGIMES]),
            Consignment.status.in_([ConsignmentStatus.DECLARED.value, ConsignmentStatus.STAMPS_LINKED.value]),
            _between(Consignment.created_at, window),
        )
    ).all()
    total = 0
    affected = 0
    prices = _tariff_prices(session, at=window.end)
    for category, declared, linked_count in rows:
        shortfall = int(declared) - int(linked_count)
        if shortfall <= 0:
            continue
        affected += 1
        total += shortfall * prices.get(str(category), 0)
    return {"amount_minor": total, "consignments": affected}


def _seizure_exposure(session: Session, *, window: Window) -> dict[str, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(Seizure.estimated_duty_minor), 0),
            func.count(Seizure.id),
        ).where(
            Seizure.status.in_((SeizureStatus.HELD.value, SeizureStatus.FORFEITED.value)),
            _between(Seizure.created_at, window),
        )
    ).one()
    return {"amount_minor": int(row[0]), "seizures": int(row[1])}


def _tariff_prices(session: Session, *, at: dt.datetime) -> dict[str, int]:
    rows = session.execute(
        select(Tariff.product_category, Tariff.unit_price_minor, Tariff.effective_from)
        .where(
            Tariff.effective_from <= at,
            (Tariff.effective_to.is_(None)) | (Tariff.effective_to > at),
        )
        .order_by(Tariff.product_category, Tariff.effective_from)
    ).all()
    return {str(row[0]): int(row[1]) for row in rows}


def _between(
    column: InstrumentedAttribute[dt.datetime] | InstrumentedAttribute[dt.datetime | None],
    window: Window,
) -> ColumnElement[bool]:
    """Half-open window on a timestamp column, so adjacent windows cannot double count."""
    return (column >= window.start) & (column < window.end)


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    return int(session.execute(statement).scalar_one())


def _sum(session: Session, statement: Select[tuple[int]]) -> int:
    return int(session.execute(statement).scalar_one())


def _grouped(session: Session, statement: Select[tuple[str, int]]) -> JsonObject:
    return {str(row[0]): int(row[1]) for row in session.execute(statement).all()}

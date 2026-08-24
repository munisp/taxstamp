"""Deterministic clone and diversion findings.

Every finding is produced by a named rule from stored evidence, carries the rule
version and the inputs it was derived from, and is deduplicated by a stable key so that
re-running detection over unchanged evidence cannot inflate the queue. Nothing here
estimates, scores or predicts: a finding is a contradiction between two records the
platform already holds.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from taxstamp.enums import AnomalyKind, AnomalySeverity, Role, TraceEventType
from taxstamp.geo import distance_km, implied_speed_kmh
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Anomaly, Facility, Product, TraceEvent, TradeUnit, Verification
from taxstamp.services.context import Actor

#: Bumped whenever a rule's inputs or thresholds change, so findings stay reproducible.
RULE_VERSION = "detection-2026.03"

#: Faster than a commercial aircraft: two observations implying this cannot both be the
#: same physical item.
MAX_PLAUSIBLE_SPEED_KMH = 900.0

#: Two scans of one serial closer together than this are treated as the same handling
#: event rather than travel, regardless of distance.
MIN_SEPARATION = dt.timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class AnomalyInput:
    kind: AnomalyKind
    severity: AnomalySeverity
    dedupe_key: str
    explanation: str
    evidence: JsonObject
    company_id: uuid.UUID | None = None
    stamp_id: uuid.UUID | None = None
    trade_unit_id: uuid.UUID | None = None


def record_anomaly(session: Session, *, finding: AnomalyInput, now: dt.datetime) -> Anomaly | None:
    """Persist a finding once. Returns None when the same finding already exists."""
    anomaly = Anomaly(
        kind=finding.kind.value,
        severity=finding.severity.value,
        dedupe_key=finding.dedupe_key,
        company_id=finding.company_id,
        stamp_id=finding.stamp_id,
        trade_unit_id=finding.trade_unit_id,
        rule_version=RULE_VERSION,
        explanation=finding.explanation,
        evidence=finding.evidence,
        detected_at=now,
    )
    savepoint = session.begin_nested()
    session.add(anomaly)
    try:
        session.flush()
    except IntegrityError:
        savepoint.rollback()
        return None
    savepoint.commit()
    return anomaly


def detect_movement_anomalies(
    session: Session, *, event: TraceEvent, previous: TraceEvent | None, now: dt.datetime
) -> list[Anomaly]:
    """Check one newly recorded movement against the unit's own history.

    Three rules apply: the unit cannot have travelled faster than an aircraft between
    two recorded locations, a receiving party cannot observe a different number of
    stamps than the unit is recorded as containing, and goods cannot surface in a market
    other than the one declared for the product without an export event.
    """
    findings: list[Anomaly] = []
    if previous is not None:
        travel = _impossible_travel(session, event=event, previous=previous)
        if travel is not None:
            recorded = record_anomaly(session, finding=travel, now=now)
            if recorded is not None:
                findings.append(recorded)
    for candidate in (_quantity_divergence(event), _market_divergence(session, event=event)):
        if candidate is None:
            continue
        recorded = record_anomaly(session, finding=candidate, now=now)
        if recorded is not None:
            findings.append(recorded)
    return findings


def _impossible_travel(session: Session, *, event: TraceEvent, previous: TraceEvent) -> AnomalyInput | None:
    origin = session.get(Facility, previous.destination_facility_id or previous.origin_facility_id)
    arrival = session.get(Facility, event.origin_facility_id)
    if origin is None or arrival is None:
        return None
    elapsed = event.occurred_at - previous.occurred_at
    if elapsed < dt.timedelta(0):
        return AnomalyInput(
            kind=AnomalyKind.IMPOSSIBLE_TRAVEL,
            severity=AnomalySeverity.HIGH,
            dedupe_key=f"travel:{event.id}:{previous.id}",
            explanation="movement recorded before the unit's previous movement",
            evidence={
                "event_ref": event.event_ref,
                "previous_event_ref": previous.event_ref,
                "elapsed_seconds": int(elapsed.total_seconds()),
                "rule": "monotonic_movement_time",
            },
            company_id=event.company_id,
            trade_unit_id=event.trade_unit_id,
        )
    if elapsed < MIN_SEPARATION:
        return None
    distance = distance_km(
        lat_e7_a=origin.latitude_e7,
        lon_e7_a=origin.longitude_e7,
        lat_e7_b=arrival.latitude_e7,
        lon_e7_b=arrival.longitude_e7,
    )
    speed = implied_speed_kmh(distance=distance, elapsed=elapsed)
    if speed is None or speed <= MAX_PLAUSIBLE_SPEED_KMH:
        return None
    return AnomalyInput(
        kind=AnomalyKind.IMPOSSIBLE_TRAVEL,
        severity=AnomalySeverity.HIGH,
        dedupe_key=f"travel:{event.id}:{previous.id}",
        explanation=(
            f"unit moved {distance:.0f} km in {elapsed.total_seconds() / 3600:.2f} h, "
            f"implying {speed:.0f} km/h"
        ),
        evidence={
            "event_ref": event.event_ref,
            "previous_event_ref": previous.event_ref,
            "distance_km": round(distance, 3),
            "elapsed_seconds": int(elapsed.total_seconds()),
            "implied_speed_kmh": round(speed, 3),
            "threshold_kmh": MAX_PLAUSIBLE_SPEED_KMH,
            "rule": "max_plausible_speed",
        },
        company_id=event.company_id,
        trade_unit_id=event.trade_unit_id,
    )


def _quantity_divergence(event: TraceEvent) -> AnomalyInput | None:
    expected = event.context.get("unit_stamp_count")
    if not isinstance(expected, int) or expected == event.observed_stamp_count:
        return None
    return AnomalyInput(
        kind=AnomalyKind.QUANTITY_NOT_CONSERVED,
        severity=AnomalySeverity.HIGH,
        dedupe_key=f"quantity:{event.id}",
        explanation=(
            f"{TraceEventType(event.event_type).value} observed {event.observed_stamp_count} "
            f"stamps but the unit contains {expected}"
        ),
        evidence={
            "event_ref": event.event_ref,
            "observed_stamp_count": event.observed_stamp_count,
            "unit_stamp_count": expected,
            "rule": "quantity_conservation",
        },
        company_id=event.company_id,
        trade_unit_id=event.trade_unit_id,
    )


def sweep_verification_anomalies(
    session: Session, *, actor: Actor, since: dt.datetime, now: dt.datetime
) -> list[Anomaly]:
    """Detect clone and diversion signals across recorded field verifications.

    Two rules apply: consecutive verifications of one serial that imply impossible
    travel (the same stamp cannot be in two distant places), and a verification whose
    location country differs from the product's intended market.
    """
    actor.require_role(Role.ANALYST, Role.SUPERVISOR, Role.OPERATOR, Role.ADMIN)
    rows = list(
        session.execute(
            select(Verification)
            .where(
                Verification.occurred_at >= since,
                Verification.stamp_id.is_not(None),
                Verification.latitude_e7.is_not(None),
                Verification.longitude_e7.is_not(None),
            )
            .order_by(Verification.serial_presented, Verification.occurred_at)
        )
        .scalars()
        .all()
    )
    findings: list[Anomaly] = []
    by_serial: dict[str, list[Verification]] = {}
    for row in rows:
        by_serial.setdefault(row.serial_presented, []).append(row)
    for serial, scans in by_serial.items():
        for previous, current in zip(scans, scans[1:], strict=False):
            finding = _scan_divergence(serial=serial, previous=previous, current=current)
            if finding is None:
                continue
            recorded = record_anomaly(session, finding=finding, now=now)
            if recorded is not None:
                findings.append(recorded)
    return findings


def _scan_divergence(*, serial: str, previous: Verification, current: Verification) -> AnomalyInput | None:
    if (
        previous.latitude_e7 is None
        or previous.longitude_e7 is None
        or current.latitude_e7 is None
        or current.longitude_e7 is None
    ):
        return None
    elapsed = current.occurred_at - previous.occurred_at
    if elapsed < MIN_SEPARATION:
        return None
    distance = distance_km(
        lat_e7_a=previous.latitude_e7,
        lon_e7_a=previous.longitude_e7,
        lat_e7_b=current.latitude_e7,
        lon_e7_b=current.longitude_e7,
    )
    speed = implied_speed_kmh(distance=distance, elapsed=elapsed)
    if speed is None or speed <= MAX_PLAUSIBLE_SPEED_KMH:
        return None
    return AnomalyInput(
        kind=AnomalyKind.DUPLICATE_SCAN_DIVERGENCE,
        severity=AnomalySeverity.HIGH,
        dedupe_key=f"scan:{previous.id}:{current.id}",
        explanation=(
            f"serial {serial} scanned {distance:.0f} km apart within "
            f"{elapsed.total_seconds() / 3600:.2f} h, implying {speed:.0f} km/h"
        ),
        evidence={
            "serial": serial,
            "first_verification_id": str(previous.id),
            "second_verification_id": str(current.id),
            "distance_km": round(distance, 3),
            "elapsed_seconds": int(elapsed.total_seconds()),
            "implied_speed_kmh": round(speed, 3),
            "threshold_kmh": MAX_PLAUSIBLE_SPEED_KMH,
            "rule": "max_plausible_speed",
        },
        stamp_id=current.stamp_id,
    )


def _market_divergence(session: Session, *, event: TraceEvent) -> AnomalyInput | None:
    """Goods arriving in a market other than the one declared for the product.

    An export event is the legitimate way to leave the intended market, so only
    arrivals and transloads elsewhere are findings.
    """
    if TraceEventType(event.event_type) not in (TraceEventType.ARRIVAL, TraceEventType.TRANSLOAD):
        return None
    unit = session.get(TradeUnit, event.trade_unit_id)
    if unit is None or unit.product_id is None:
        return None
    product = session.get(Product, unit.product_id)
    facility = session.get(Facility, event.destination_facility_id or event.origin_facility_id)
    if product is None or facility is None:
        return None
    if product.intended_market == facility.country:
        return None
    return AnomalyInput(
        kind=AnomalyKind.MARKET_DIVERSION,
        severity=AnomalySeverity.MEDIUM,
        dedupe_key=f"market:{event.id}",
        explanation=(
            f"unit for market {product.intended_market} recorded in {facility.country} "
            f"without an export event"
        ),
        evidence={
            "event_ref": event.event_ref,
            "intended_market": product.intended_market,
            "observed_country": facility.country,
            "facility_code": facility.facility_code,
            "rule": "intended_market_divergence",
        },
        company_id=event.company_id,
        trade_unit_id=event.trade_unit_id,
    )

"""Explainable, deterministic risk scoring for a licence holder.

The score is a weighted count of facts the platform already holds: findings raised by
the detection rules, verification failures against the company's own stamps, licence
status, import consignments whose stamps do not cover what was declared, and enforcement
outcomes. Every contribution names the rule, the weight, the observed count and the
window it was counted over, so a company can be told exactly why it is rated as it is
and can contest the underlying records.

Nothing here is learned, predicted or inferred. There is no model, no training data and
no opaque feature: the same inputs always produce the same score, and the model version
is recorded with every answer so a historical score can be reproduced.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.enums import (
    STAMP_LIABLE_REGIMES,
    AnomalySeverity,
    CaseStatus,
    ConsignmentStatus,
    LicenceStatus,
    RiskTier,
    VerificationOutcome,
)
from taxstamp.errors import NotFound
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import (
    Anomaly,
    Company,
    Consignment,
    ConsignmentStamp,
    EnforcementCase,
    Licence,
    Stamp,
    Verification,
)
from taxstamp.services.context import CROSS_TENANT_READERS, Actor

#: Bumped whenever a weight, window or factor changes, so a score stays reproducible.
MODEL_VERSION = "risk-2026.03"

#: How far back observations are counted.
WINDOW = dt.timedelta(days=90)

#: Points per observation, and the most any one factor may contribute. Caps stop a
#: single noisy factor from saturating the score on its own.
WEIGHTS: dict[str, tuple[int, int]] = {
    "anomaly_high": (15, 45),
    "anomaly_medium": (8, 24),
    "anomaly_low": (3, 9),
    "verification_failure": (2, 20),
    "consignment_stamp_shortfall": (10, 20),
    "substantiated_case": (20, 40),
    "licence_not_active": (25, 25),
}

#: Score boundaries. A tier is a band on the score, not a separate judgement.
MEDIUM_FROM = 25
HIGH_FROM = 60

#: Verification outcomes that indicate a problem with the company's own stamps, as
#: opposed to a consumer mistyping a code.
FAILURE_OUTCOMES: tuple[str, ...] = (
    VerificationOutcome.VOID.value,
    VerificationOutcome.NOT_ACTIVE.value,
    VerificationOutcome.VELOCITY_SUSPECT.value,
)


@dataclass(frozen=True, slots=True)
class Contribution:
    factor: str
    observations: int
    weight: int
    points: int
    explanation: str


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    company_id: uuid.UUID
    score: int
    tier: RiskTier
    model_version: str
    window_from: dt.datetime
    window_to: dt.datetime
    contributions: tuple[Contribution, ...]


def _contribute(factor: str, observations: int, explanation: str) -> Contribution:
    weight, cap = WEIGHTS[factor]
    return Contribution(
        factor=factor,
        observations=observations,
        weight=weight,
        points=min(observations * weight, cap),
        explanation=explanation,
    )


def assess_company(
    session: Session,
    *,
    actor: Actor,
    company_id: uuid.UUID,
    now: dt.datetime,
) -> RiskAssessment:
    """Score one company from stored evidence, with every contribution explained."""
    if actor.role not in CROSS_TENANT_READERS:
        actor.require_company(company_id)
    if session.get(Company, company_id) is None:
        raise NotFound("company not found")
    window_from = now - WINDOW

    contributions = [
        _contribute(
            f"anomaly_{severity.value}",
            _anomaly_count(session, company_id=company_id, severity=severity, since=window_from),
            f"{severity.value}-severity detection findings in the last {WINDOW.days} days",
        )
        for severity in (AnomalySeverity.HIGH, AnomalySeverity.MEDIUM, AnomalySeverity.LOW)
    ]
    contributions.append(
        _contribute(
            "verification_failure",
            _verification_failures(session, company_id=company_id, since=window_from),
            "verifications of this company's stamps that returned void, not-active or "
            f"velocity-suspect in the last {WINDOW.days} days",
        )
    )
    contributions.append(
        _contribute(
            "consignment_stamp_shortfall",
            _consignment_shortfalls(session, company_id=company_id),
            "stamp-liable consignments whose linked stamps do not cover the declared quantity",
        )
    )
    contributions.append(
        _contribute(
            "substantiated_case",
            _substantiated_cases(session, company_id=company_id, since=window_from),
            f"enforcement cases closed as substantiated in the last {WINDOW.days} days",
        )
    )
    contributions.append(
        _contribute(
            "licence_not_active",
            _non_active_licences(session, company_id=company_id),
            "excise licences currently suspended or revoked",
        )
    )
    score = min(sum(item.points for item in contributions), 100)
    return RiskAssessment(
        company_id=company_id,
        score=score,
        tier=tier_for(score),
        model_version=MODEL_VERSION,
        window_from=window_from,
        window_to=now,
        contributions=tuple(contributions),
    )


def tier_for(score: int) -> RiskTier:
    if score >= HIGH_FROM:
        return RiskTier.HIGH
    if score >= MEDIUM_FROM:
        return RiskTier.MEDIUM
    return RiskTier.LOW


def assessment_document(assessment: RiskAssessment) -> JsonObject:
    contributions: JsonArray = [
        {
            "factor": item.factor,
            "observations": item.observations,
            "weight": item.weight,
            "points": item.points,
            "explanation": item.explanation,
        }
        for item in assessment.contributions
    ]
    return {
        "company_id": str(assessment.company_id),
        "score": assessment.score,
        "tier": assessment.tier.value,
        "model_version": assessment.model_version,
        "window_from": assessment.window_from.isoformat(),
        "window_to": assessment.window_to.isoformat(),
        "contributions": contributions,
        "method": (
            "Deterministic weighted counts of stored records, capped per factor. No "
            "statistical model, no learned parameters, no unexplained features."
        ),
    }


def _anomaly_count(
    session: Session, *, company_id: uuid.UUID, severity: AnomalySeverity, since: dt.datetime
) -> int:
    return int(
        session.execute(
            select(func.count(Anomaly.id)).where(
                Anomaly.company_id == company_id,
                Anomaly.severity == severity.value,
                Anomaly.detected_at >= since,
            )
        ).scalar_one()
    )


def _verification_failures(session: Session, *, company_id: uuid.UUID, since: dt.datetime) -> int:
    return int(
        session.execute(
            select(func.count(Verification.id))
            .join(Stamp, Stamp.id == Verification.stamp_id)
            .where(
                Stamp.company_id == company_id,
                Verification.outcome.in_(FAILURE_OUTCOMES),
                Verification.occurred_at >= since,
            )
        ).scalar_one()
    )


def _consignment_shortfalls(session: Session, *, company_id: uuid.UUID) -> int:
    linked = (
        select(
            ConsignmentStamp.consignment_id.label("consignment_id"),
            func.count(ConsignmentStamp.id).label("linked"),
        )
        .group_by(ConsignmentStamp.consignment_id)
        .subquery()
    )
    rows = session.execute(
        select(Consignment.declared_quantity, func.coalesce(linked.c.linked, 0))
        .outerjoin(linked, linked.c.consignment_id == Consignment.id)
        .where(
            Consignment.company_id == company_id,
            Consignment.regime.in_([regime.value for regime in STAMP_LIABLE_REGIMES]),
            Consignment.status.in_([ConsignmentStatus.DECLARED.value, ConsignmentStatus.STAMPS_LINKED.value]),
        )
    ).all()
    return sum(1 for row in rows if int(row[1]) < int(row[0]))


def _substantiated_cases(session: Session, *, company_id: uuid.UUID, since: dt.datetime) -> int:
    return int(
        session.execute(
            select(func.count(EnforcementCase.id)).where(
                EnforcementCase.company_id == company_id,
                EnforcementCase.status == CaseStatus.CLOSED_SUBSTANTIATED.value,
                EnforcementCase.created_at >= since,
            )
        ).scalar_one()
    )


def _non_active_licences(session: Session, *, company_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count(Licence.id)).where(
                Licence.company_id == company_id,
                Licence.status.in_((LicenceStatus.SUSPENDED.value, LicenceStatus.REVOKED.value)),
            )
        ).scalar_one()
    )

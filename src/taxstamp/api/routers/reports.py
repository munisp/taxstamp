"""Programme KPI, revenue-at-risk and risk-scoring endpoints."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter

from taxstamp.api.deps import CurrentActor, RuntimeDep, authorize
from taxstamp.authz.actions import Action
from taxstamp.errors import ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.services import reporting as reporting_service
from taxstamp.services import risk as risk_service

router = APIRouter(prefix="/v1/reports", tags=["reports"])


def _window(start: dt.datetime | None, end: dt.datetime | None, now: dt.datetime) -> reporting_service.Window:
    resolved_end = end if end is not None else now
    resolved_start = start if start is not None else resolved_end - dt.timedelta(days=30)
    if resolved_start.tzinfo is None or resolved_end.tzinfo is None:
        raise ValidationFailed("window bounds must include a timezone offset")
    return reporting_service.Window(start=resolved_start, end=resolved_end)


@router.get("/kpis")
def programme_kpis(
    runtime: RuntimeDep,
    current: CurrentActor,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> JsonObject:
    authorize(runtime, current.actor, Action.REPORT_PROGRAMME)
    window = _window(start, end, runtime.clock.now())
    with runtime.session_factory() as session:
        return reporting_service.programme_kpis(session, actor=current.actor, window=window)


@router.get("/revenue-at-risk")
def revenue_at_risk(
    runtime: RuntimeDep,
    current: CurrentActor,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> JsonObject:
    authorize(runtime, current.actor, Action.REPORT_PROGRAMME)
    window = _window(start, end, runtime.clock.now())
    with runtime.session_factory() as session:
        return reporting_service.revenue_at_risk(session, actor=current.actor, window=window)


@router.get("/risk/{company_id}")
def company_risk(company_id: uuid.UUID, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    authorize(runtime, current.actor, Action.REPORT_RISK)
    with runtime.session_factory() as session:
        assessment = risk_service.assess_company(
            session, actor=current.actor, company_id=company_id, now=runtime.clock.now()
        )
        return risk_service.assessment_document(assessment)

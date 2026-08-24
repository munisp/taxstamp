"""Facilities, aggregation and supply-chain movement endpoints."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, authorize
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import (
    AggregateRequest,
    DisaggregateRequest,
    RegisterFacilityRequest,
    TraceEventRequest,
)
from taxstamp.authz.actions import Action
from taxstamp.enums import FacilityKind, TraceEventType, TradeUnitLevel
from taxstamp.errors import ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.services import repository as repository_service
from taxstamp.services import traceability as trace_service

router = APIRouter(prefix="/v1", tags=["traceability"])

MAX_PAGE = 200


@router.post("/facilities", status_code=201)
def register_facility(
    body: RegisterFacilityRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.TRACE_RECORD)

    def work(session: Session) -> JsonObject:
        facility = trace_service.register_facility(
            session,
            actor=actor,
            command=trace_service.RegisterFacilityCommand(
                facility_code=body.facility_code,
                name=body.name,
                kind=FacilityKind(body.kind),
                country=body.country,
                state=body.state,
                address=body.address,
                latitude_e7=body.latitude_e7,
                longitude_e7=body.longitude_e7,
                company_id=body.company_id,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return trace_service.facility_snapshot(facility)

    status, document = run_idempotent(
        runtime,
        scope="facilities.register",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/units", status_code=201)
def aggregate_unit(
    body: AggregateRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.TRACE_RECORD)

    def work(session: Session) -> JsonObject:
        unit = trace_service.aggregate(
            session,
            actor=actor,
            command=trace_service.AggregateCommand(
                unit_code=body.unit_code,
                level=TradeUnitLevel(body.level),
                facility_code=body.facility_code,
                serials=tuple(body.serials),
                child_unit_codes=tuple(body.child_unit_codes),
                product_id=body.product_id,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return trace_service.unit_snapshot(unit)

    status, document = run_idempotent(
        runtime,
        scope="units.aggregate",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/units/{unit_code}/disaggregation")
def disaggregate_unit(
    unit_code: str,
    body: DisaggregateRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.TRACE_RECORD)

    def work(session: Session) -> JsonObject:
        unit = trace_service.disaggregate(
            session,
            actor=actor,
            unit_code=unit_code,
            reason=body.reason,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return trace_service.unit_snapshot(unit)

    status, document = run_idempotent(
        runtime,
        scope="units.disaggregate",
        key=key,
        actor=actor,
        payload={"unit_code": unit_code, **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/trace-events", status_code=201)
def record_trace_event(
    body: TraceEventRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.TRACE_RECORD)

    def work(session: Session) -> JsonObject:
        result = trace_service.record_trace_event(
            session,
            actor=actor,
            command=trace_service.RecordTraceEventCommand(
                event_ref=body.event_ref,
                event_type=TraceEventType(body.event_type),
                unit_code=body.unit_code,
                origin_facility_code=body.origin_facility_code,
                destination_facility_code=body.destination_facility_code,
                observed_stamp_count=body.observed_stamp_count,
                transport_reference=body.transport_reference,
                occurred_at=body.occurred_at,
                consignment_ref=body.consignment_ref,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return {
            "event": repository_service.event_document(session, result.event),
            "unit_status": result.unit_status.value,
            "anomalies": [repository_service.anomaly_document(finding) for finding in result.anomalies],
        }

    status, document = run_idempotent(
        runtime,
        scope="trace.events",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/units/{unit_code}")
def read_unit(unit_code: str, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    with runtime.session_factory() as session:
        return repository_service.unit_trace(
            session,
            actor=current.actor,
            unit_code=unit_code,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )


@router.get("/stamps/{serial}/trace")
def read_stamp_trace(serial: str, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    with runtime.session_factory() as session:
        return repository_service.stamp_trace(
            session,
            actor=current.actor,
            serial=serial,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )


@router.get("/movements")
def list_movements(
    runtime: RuntimeDep,
    current: CurrentActor,
    company_id: uuid.UUID | None = None,
    unit_code: str | None = None,
    consignment_ref: str | None = None,
    event_type: TraceEventType | None = None,
    occurred_from: dt.datetime | None = None,
    occurred_to: dt.datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> JsonObject:
    if occurred_from is not None and occurred_from.tzinfo is None:
        raise ValidationFailed("occurred_from must carry a timezone")
    if occurred_to is not None and occurred_to.tzinfo is None:
        raise ValidationFailed("occurred_to must carry a timezone")
    with runtime.session_factory() as session:
        return repository_service.movements(
            session,
            actor=current.actor,
            query=repository_service.MovementQuery(
                company_id=company_id,
                unit_code=unit_code,
                consignment_ref=consignment_ref,
                event_type=None if event_type is None else event_type.value,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
                limit=limit,
                offset=offset,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )


@router.get("/anomalies")
def list_anomalies(
    runtime: RuntimeDep,
    current: CurrentActor,
    company_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> JsonObject:
    with runtime.session_factory() as session:
        return repository_service.anomalies(
            session,
            actor=current.actor,
            company_id=company_id,
            limit=limit,
            offset=offset,
        )

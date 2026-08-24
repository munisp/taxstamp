"""Repository queries: what the regulator and the licence holder are allowed to ask.

Every query is tenant-scoped through the actor, and every query that returns
serial-level or movement data is a sensitive read, so the caller records audit evidence
for it. Reads are deliberately paginated and bounded: an unbounded repository query is
an export, and exports go through ``services.exports`` where they are hashed and signed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.errors import Forbidden, NotFound
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import (
    Anomaly,
    Consignment,
    Facility,
    Order,
    Product,
    Stamp,
    TraceEvent,
    TradeUnit,
    UnitMembership,
)
from taxstamp.services.context import CROSS_TENANT_READERS, Actor

MAX_PAGE = 200
DEFAULT_PAGE = 50

#: Roles that may query across tenants. A field device is not one of them.
SUPERVISORY_ROLES = CROSS_TENANT_READERS


@dataclass(frozen=True, slots=True)
class MovementQuery:
    company_id: uuid.UUID | None
    unit_code: str | None
    consignment_ref: str | None
    event_type: str | None
    occurred_from: dt.datetime | None
    occurred_to: dt.datetime | None
    limit: int
    offset: int


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_PAGE)), max(0, offset)


def _tenant_scope(actor: Actor, requested: uuid.UUID | None) -> uuid.UUID | None:
    """The company a query must be restricted to, or None for a supervisory reader."""
    if actor.role in SUPERVISORY_ROLES:
        return requested
    if actor.company_id is None:
        raise Forbidden("this credential may not query the repository")
    if requested is not None and requested != actor.company_id:
        raise Forbidden("resource belongs to another company")
    return actor.company_id


def _facility_codes(session: Session, event: TraceEvent) -> tuple[str, str | None]:
    origin = session.get(Facility, event.origin_facility_id)
    destination = (
        None
        if event.destination_facility_id is None
        else session.get(Facility, event.destination_facility_id)
    )
    return (
        "" if origin is None else origin.facility_code,
        None if destination is None else destination.facility_code,
    )


def event_document(session: Session, event: TraceEvent) -> JsonObject:
    origin_code, destination_code = _facility_codes(session, event)
    unit = session.get(TradeUnit, event.trade_unit_id)
    consignment = None if event.consignment_id is None else session.get(Consignment, event.consignment_id)
    return {
        "event_ref": event.event_ref,
        "event_type": event.event_type,
        "unit_code": None if unit is None else unit.unit_code,
        "company_id": str(event.company_id),
        "origin_facility_code": origin_code,
        "destination_facility_code": destination_code,
        "consignment_ref": None if consignment is None else consignment.consignment_ref,
        "observed_stamp_count": event.observed_stamp_count,
        "transport_reference": event.transport_reference,
        "occurred_at": event.occurred_at.isoformat(),
        "recorded_at": event.recorded_at.isoformat(),
    }


def anomaly_document(anomaly: Anomaly) -> JsonObject:
    return {
        "kind": anomaly.kind,
        "severity": anomaly.severity,
        "dedupe_key": anomaly.dedupe_key,
        "rule_version": anomaly.rule_version,
        "explanation": anomaly.explanation,
        "evidence": anomaly.evidence,
        "detected_at": anomaly.detected_at.isoformat(),
        "company_id": None if anomaly.company_id is None else str(anomaly.company_id),
    }


def stamp_trace(
    session: Session,
    *,
    actor: Actor,
    serial: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> JsonObject:
    """The full history of one serial: product, aggregation and movements."""
    stamp = session.execute(select(Stamp).where(Stamp.serial == serial)).scalar_one_or_none()
    if stamp is None:
        raise NotFound("stamp not found", detail={"serial": serial})
    scope = _tenant_scope(actor, stamp.company_id)
    if scope is not None and scope != stamp.company_id:
        raise Forbidden("resource belongs to another company")

    membership = session.execute(
        select(UnitMembership)
        .where(UnitMembership.stamp_id == stamp.id)
        .order_by(UnitMembership.added_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    unit_codes: JsonArray = []
    events: JsonArray = []
    if membership is not None:
        unit = session.get(TradeUnit, membership.trade_unit_id)
        while unit is not None:
            unit_codes.append(unit.unit_code)
            unit = None if unit.parent_unit_id is None else session.get(TradeUnit, unit.parent_unit_id)
        rows = (
            session.execute(
                select(TraceEvent)
                .join(TradeUnit, TradeUnit.id == TraceEvent.trade_unit_id)
                .where(TradeUnit.unit_code.in_(unit_codes))
                .order_by(TraceEvent.occurred_at)
                .limit(MAX_PAGE)
            )
            .scalars()
            .all()
        )
        events = [event_document(session, row) for row in rows]

    # A stamp carries no product of its own; the ordered product is the authority.
    order = session.get(Order, stamp.order_id)
    product = None if order is None or order.product_id is None else session.get(Product, order.product_id)
    document: JsonObject = {
        "serial": stamp.serial,
        "status": stamp.status,
        "company_id": str(stamp.company_id),
        "batch_id": str(stamp.batch_id),
        "product": None
        if product is None
        else {
            "sku": product.sku,
            "brand": product.brand,
            "product_category": product.product_category,
            "intended_market": product.intended_market,
        },
        "aggregation_path": unit_codes,
        "movements": events,
    }
    _record_query(
        session,
        actor=actor,
        query="repository.stamp_trace",
        target=serial,
        result_count=len(events),
        now=now,
        audit_secret=audit_secret,
        revision=revision,
    )
    return document


def unit_trace(
    session: Session,
    *,
    actor: Actor,
    unit_code: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> JsonObject:
    unit = session.execute(select(TradeUnit).where(TradeUnit.unit_code == unit_code)).scalar_one_or_none()
    if unit is None:
        raise NotFound("trade unit not found", detail={"unit_code": unit_code})
    scope = _tenant_scope(actor, unit.company_id)
    if scope is not None and scope != unit.company_id:
        raise Forbidden("resource belongs to another company")
    rows = (
        session.execute(
            select(TraceEvent)
            .where(TraceEvent.trade_unit_id == unit.id)
            .order_by(TraceEvent.occurred_at)
            .limit(MAX_PAGE)
        )
        .scalars()
        .all()
    )
    serials: JsonArray = list(
        session.execute(
            select(Stamp.serial)
            .join(UnitMembership, UnitMembership.stamp_id == Stamp.id)
            .where(UnitMembership.trade_unit_id == unit.id, UnitMembership.removed_at.is_(None))
            .order_by(Stamp.serial)
            .limit(MAX_PAGE)
        )
        .scalars()
        .all()
    )
    document: JsonObject = {
        "unit_code": unit.unit_code,
        "level": unit.level,
        "status": unit.status,
        "stamp_count": unit.stamp_count,
        "company_id": str(unit.company_id),
        "serials": serials,
        "movements": [event_document(session, row) for row in rows],
    }
    _record_query(
        session,
        actor=actor,
        query="repository.unit_trace",
        target=unit_code,
        result_count=len(rows),
        now=now,
        audit_secret=audit_secret,
        revision=revision,
    )
    return document


def movements(
    session: Session,
    *,
    actor: Actor,
    query: MovementQuery,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> JsonObject:
    """Movement events matching a filter, scoped to what the caller may see."""
    limit, offset = _page(query.limit, query.offset)
    scope = _tenant_scope(actor, query.company_id)
    statement: Select[tuple[TraceEvent]] = select(TraceEvent)
    if scope is not None:
        statement = statement.where(TraceEvent.company_id == scope)
    if query.unit_code is not None:
        statement = statement.join(TradeUnit, TradeUnit.id == TraceEvent.trade_unit_id).where(
            TradeUnit.unit_code == query.unit_code
        )
    if query.consignment_ref is not None:
        statement = statement.join(Consignment, Consignment.id == TraceEvent.consignment_id).where(
            Consignment.consignment_ref == query.consignment_ref
        )
    if query.event_type is not None:
        statement = statement.where(TraceEvent.event_type == query.event_type)
    if query.occurred_from is not None:
        statement = statement.where(TraceEvent.occurred_at >= query.occurred_from)
    if query.occurred_to is not None:
        statement = statement.where(TraceEvent.occurred_at <= query.occurred_to)
    rows = (
        session.execute(statement.order_by(TraceEvent.occurred_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    _record_query(
        session,
        actor=actor,
        query="repository.movements",
        target=query.unit_code or query.consignment_ref or "filter",
        result_count=len(rows),
        now=now,
        audit_secret=audit_secret,
        revision=revision,
    )
    return {
        "movements": [event_document(session, row) for row in rows],
        "limit": limit,
        "offset": offset,
    }


def anomalies(
    session: Session,
    *,
    actor: Actor,
    company_id: uuid.UUID | None,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> JsonObject:
    """The operator and regulator risk queue."""
    bounded, skip = _page(limit, offset)
    scope = _tenant_scope(actor, company_id)
    statement = select(Anomaly).order_by(Anomaly.detected_at.desc())
    if scope is not None:
        statement = statement.where(Anomaly.company_id == scope)
    rows = session.execute(statement.limit(bounded).offset(skip)).scalars().all()
    return {
        "anomalies": [anomaly_document(row) for row in rows],
        "limit": bounded,
        "offset": skip,
    }


def _record_query(
    session: Session,
    *,
    actor: Actor,
    query: str,
    target: str,
    result_count: int,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> None:
    """Sensitive reads leave evidence: who looked at whose serials, and when."""
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action=query,
            target_type="repository_query",
            target_id=target,
            outcome="success",
            after_state={"result_count": result_count},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )

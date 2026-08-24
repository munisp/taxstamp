"""Supply-chain traceability: aggregation and movement events.

Aggregation is a fact about physical goods, so it is enforced rather than described: a
stamp belongs to at most one open case, a case to at most one pallet, and a unit's stamp
count is derived from its members rather than taken from the request. Movement events
are append-only, idempotent per event reference, and drive the unit's state machine, so
a dispatch that never arrived stays visibly in transit.

Destruction is the one event that changes stamp state: destroyed goods cannot be
verified afterwards, so their stamps are voided in the same transaction.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import (
    STAMP_LIABLE_REGIMES,
    TRACE_EVENT_RESULT,
    ConsignmentStatus,
    CustomsRegime,
    FacilityKind,
    Role,
    StampStatus,
    TraceEventType,
    TradeUnitLevel,
    TradeUnitStatus,
    TransitionError,
    assert_trade_unit_transition,
    child_level,
)
from taxstamp.errors import Conflict, Forbidden, NotFound, ValidationFailed
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import (
    Anomaly,
    Company,
    Consignment,
    Facility,
    Product,
    Stamp,
    StampEvent,
    TraceEvent,
    TradeUnit,
    UnitMembership,
)
from taxstamp.outbox import enqueue
from taxstamp.serials import is_valid_serial
from taxstamp.services.anomaly import detect_movement_anomalies
from taxstamp.services.context import Actor

MAX_UNIT_MEMBERS = 1_000
MAX_UNIT_CHILDREN = 200
CODE_MAX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class RegisterFacilityCommand:
    facility_code: str
    name: str
    kind: FacilityKind
    country: str
    state: str
    address: str
    latitude_e7: int
    longitude_e7: int
    company_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class AggregateCommand:
    unit_code: str
    level: TradeUnitLevel
    facility_code: str
    serials: tuple[str, ...]
    child_unit_codes: tuple[str, ...]
    product_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class RecordTraceEventCommand:
    event_ref: str
    event_type: TraceEventType
    unit_code: str
    origin_facility_code: str
    destination_facility_code: str | None
    observed_stamp_count: int
    transport_reference: str
    occurred_at: dt.datetime
    consignment_ref: str | None


@dataclass(frozen=True, slots=True)
class TraceEventResult:
    event: TraceEvent
    unit_status: TradeUnitStatus
    anomalies: tuple[Anomaly, ...]


def register_facility(
    session: Session,
    *,
    actor: Actor,
    command: RegisterFacilityCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Facility:
    actor.require_role(Role.REQUESTER, Role.OPERATOR, Role.ADMIN)
    if command.company_id is not None:
        actor.require_company(command.company_id)
        if session.get(Company, command.company_id) is None:
            raise NotFound("company not found")
    elif actor.role is not Role.ADMIN:
        raise Forbidden("only an administrator may register a facility without a company")
    if len(command.country) != 2 or not command.country.isalpha():
        raise ValidationFailed("country must be an ISO 3166-1 alpha-2 code")
    duplicate = session.execute(
        select(Facility.id).where(Facility.facility_code == command.facility_code)
    ).scalar_one_or_none()
    if duplicate is not None:
        raise Conflict("a facility with this code already exists")

    facility = Facility(
        facility_code=command.facility_code,
        company_id=command.company_id,
        name=command.name,
        kind=command.kind.value,
        country=command.country.upper(),
        state=command.state,
        address=command.address,
        latitude_e7=command.latitude_e7,
        longitude_e7=command.longitude_e7,
        active=True,
        created_at=now,
    )
    session.add(facility)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="facility.register",
            target_type="facility",
            target_id=str(facility.id),
            outcome="success",
            after_state=facility_snapshot(facility),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return facility


def facility_snapshot(facility: Facility) -> JsonObject:
    return {
        "facility_code": facility.facility_code,
        "name": facility.name,
        "kind": facility.kind,
        "country": facility.country,
        "state": facility.state,
        "company_id": str(facility.company_id) if facility.company_id is not None else None,
    }


def _facility_for(session: Session, *, code: str, actor: Actor) -> Facility:
    facility = session.execute(select(Facility).where(Facility.facility_code == code)).scalar_one_or_none()
    if facility is None:
        raise NotFound("facility not found", detail={"facility_code": code})
    if not facility.active:
        raise ValidationFailed("facility is not active", detail={"facility_code": code})
    if facility.company_id is not None:
        actor.require_company(facility.company_id)
    return facility


def _unit_for(session: Session, *, code: str, lock: bool) -> TradeUnit:
    statement = select(TradeUnit).where(TradeUnit.unit_code == code)
    if lock:
        statement = statement.with_for_update()
    unit = session.execute(statement).scalar_one_or_none()
    if unit is None:
        raise NotFound("trade unit not found", detail={"unit_code": code})
    return unit


def aggregate(
    session: Session,
    *,
    actor: Actor,
    command: AggregateCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> TradeUnit:
    """Create a closed aggregation unit from stamps or from units one level below."""
    actor.require_role(Role.REQUESTER, Role.OPERATOR, Role.ADMIN)
    if not command.unit_code or len(command.unit_code) > CODE_MAX_LENGTH:
        raise ValidationFailed("unit_code must be 1-64 characters")
    expects_children = child_level(command.level) is not None
    if expects_children and command.serials:
        raise ValidationFailed(f"a {command.level.value} aggregates units, not serials")
    if not expects_children and command.child_unit_codes:
        raise ValidationFailed("a case aggregates serials, not units")
    if session.execute(
        select(TradeUnit.id).where(TradeUnit.unit_code == command.unit_code)
    ).scalar_one_or_none():
        raise Conflict("a trade unit with this code already exists")
    facility = _facility_for(session, code=command.facility_code, actor=actor)

    if expects_children:
        children = _claimable_children(session, command=command, actor=actor)
        company_id = children[0].company_id
        stamp_count = sum(child.stamp_count for child in children)
        product_id = command.product_id or _single_product(children)
        members: list[Stamp] = []
    else:
        members = _claimable_stamps(session, serials=command.serials, actor=actor)
        children = []
        company_id = members[0].company_id
        stamp_count = len(members)
        product_id = command.product_id

    if facility.company_id is not None and facility.company_id != company_id:
        raise Forbidden("facility belongs to another company")
    if product_id is not None:
        product = session.get(Product, product_id)
        if product is None:
            raise NotFound("product not found")
        if product.company_id != company_id:
            raise Forbidden("product belongs to another company")

    unit = TradeUnit(
        unit_code=command.unit_code,
        level=command.level.value,
        company_id=company_id,
        product_id=product_id,
        status=TradeUnitStatus.CLOSED.value,
        stamp_count=stamp_count,
        facility_id=facility.id,
        created_by=actor.principal_id,
        created_at=now,
        closed_at=now,
    )
    session.add(unit)
    session.flush()
    for stamp in members:
        session.add(UnitMembership(trade_unit_id=unit.id, stamp_id=stamp.id, added_at=now))
    for child in children:
        child.parent_unit_id = unit.id
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="trade_unit.aggregate",
            target_type="trade_unit",
            target_id=str(unit.id),
            outcome="success",
            after_state=unit_snapshot(unit),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return unit


def _claimable_stamps(session: Session, *, serials: tuple[str, ...], actor: Actor) -> list[Stamp]:
    deduped = list(dict.fromkeys(serials))
    if not deduped:
        raise ValidationFailed("at least one serial is required")
    if len(deduped) > MAX_UNIT_MEMBERS:
        raise ValidationFailed(f"at most {MAX_UNIT_MEMBERS} serials may be aggregated at once")
    malformed = [serial for serial in deduped if not is_valid_serial(serial)]
    if malformed:
        raise ValidationFailed(
            "one or more serials are malformed", detail={"invalid_count": str(len(malformed))}
        )
    stamps = list(
        session.execute(select(Stamp).where(Stamp.serial.in_(deduped)).with_for_update()).scalars().all()
    )
    found = {stamp.serial: stamp for stamp in stamps}
    missing = [serial for serial in deduped if serial not in found]
    if missing:
        raise ValidationFailed(
            "one or more serials were never issued", detail={"missing_count": str(len(missing))}
        )
    companies = {stamp.company_id for stamp in stamps}
    if len(companies) > 1:
        raise ValidationFailed("a unit cannot mix stamps from different companies")
    actor.require_company(next(iter(companies)))
    unusable = [stamp.serial for stamp in stamps if StampStatus(stamp.status) not in _AGGREGATABLE_STATUSES]
    if unusable:
        raise Conflict(
            "one or more stamps are void or expired",
            detail={"serials": ",".join(sorted(unusable)[:10])},
        )
    already = list(
        session.execute(
            select(Stamp.serial)
            .join(UnitMembership, UnitMembership.stamp_id == Stamp.id)
            .where(Stamp.id.in_([stamp.id for stamp in stamps]), UnitMembership.removed_at.is_(None))
        )
        .scalars()
        .all()
    )
    if already:
        raise Conflict(
            "one or more stamps are already aggregated into an open unit",
            detail={"serials": ",".join(sorted(already)[:10])},
        )
    return [found[serial] for serial in deduped]


_AGGREGATABLE_STATUSES = frozenset({StampStatus.ISSUED, StampStatus.ACTIVE})


def _claimable_children(session: Session, *, command: AggregateCommand, actor: Actor) -> list[TradeUnit]:
    deduped = list(dict.fromkeys(command.child_unit_codes))
    if not deduped:
        raise ValidationFailed("at least one child unit is required")
    if len(deduped) > MAX_UNIT_CHILDREN:
        raise ValidationFailed(f"at most {MAX_UNIT_CHILDREN} units may be aggregated at once")
    units = list(
        session.execute(select(TradeUnit).where(TradeUnit.unit_code.in_(deduped)).with_for_update())
        .scalars()
        .all()
    )
    found = {unit.unit_code: unit for unit in units}
    missing = [code for code in deduped if code not in found]
    if missing:
        raise NotFound("one or more child units do not exist", detail={"unit_codes": ",".join(missing)})
    companies = {unit.company_id for unit in units}
    if len(companies) > 1:
        raise ValidationFailed("a unit cannot mix units from different companies")
    actor.require_company(next(iter(companies)))
    expected = child_level(command.level)
    wrong_level = [unit.unit_code for unit in units if unit.level != (expected and expected.value)]
    if wrong_level:
        raise ValidationFailed(
            f"a {command.level.value} may only contain units one level below",
            detail={"unit_codes": ",".join(sorted(wrong_level)[:10])},
        )
    claimed = [unit.unit_code for unit in units if unit.parent_unit_id is not None]
    if claimed:
        raise Conflict(
            "one or more units already belong to a parent unit",
            detail={"unit_codes": ",".join(sorted(claimed)[:10])},
        )
    unavailable = [
        unit.unit_code for unit in units if TradeUnitStatus(unit.status) is not TradeUnitStatus.CLOSED
    ]
    if unavailable:
        raise Conflict(
            "one or more units are in transit or terminal and cannot be aggregated",
            detail={"unit_codes": ",".join(sorted(unavailable)[:10])},
        )
    return [found[code] for code in deduped]


def _single_product(children: list[TradeUnit]) -> uuid.UUID | None:
    products = {child.product_id for child in children if child.product_id is not None}
    return products.pop() if len(products) == 1 else None


def disaggregate(
    session: Session,
    *,
    actor: Actor,
    unit_code: str,
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> TradeUnit:
    """Break a unit open, releasing its stamps and child units for re-aggregation."""
    actor.require_role(Role.REQUESTER, Role.OPERATOR, Role.ADMIN)
    if not reason.strip():
        raise ValidationFailed("a reason is required to disaggregate a unit")
    unit = _unit_for(session, code=unit_code, lock=True)
    actor.require_company(unit.company_id)
    if unit.parent_unit_id is not None:
        raise Conflict("remove the unit from its parent before disaggregating it")
    try:
        assert_trade_unit_transition(TradeUnitStatus(unit.status), TradeUnitStatus.DISAGGREGATED)
    except TransitionError as exc:
        raise Conflict(str(exc)) from exc
    before = unit_snapshot(unit)
    session.execute(
        update(UnitMembership)
        .where(UnitMembership.trade_unit_id == unit.id, UnitMembership.removed_at.is_(None))
        .values(removed_at=now)
    )
    session.execute(update(TradeUnit).where(TradeUnit.parent_unit_id == unit.id).values(parent_unit_id=None))
    unit.status = TradeUnitStatus.DISAGGREGATED.value
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="trade_unit.disaggregate",
            target_type="trade_unit",
            target_id=str(unit.id),
            outcome="success",
            before_state=before,
            after_state={**unit_snapshot(unit), "reason": reason},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return unit


def record_trace_event(
    session: Session,
    *,
    actor: Actor,
    command: RecordTraceEventCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> TraceEventResult:
    """Record one movement of a unit, advance its state, and run detection."""
    actor.require_role(Role.REQUESTER, Role.OPERATOR, Role.ADMIN)
    if command.observed_stamp_count <= 0:
        raise ValidationFailed("observed_stamp_count must be positive")
    if command.occurred_at > now:
        raise ValidationFailed("a movement cannot be recorded before it happens")
    existing = session.execute(
        select(TraceEvent).where(TraceEvent.event_ref == command.event_ref)
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("a trace event with this reference already exists")

    unit = _unit_for(session, code=command.unit_code, lock=True)
    actor.require_company(unit.company_id)
    if unit.parent_unit_id is not None:
        raise Conflict("record the movement against the outermost unit")
    origin = _facility_for(session, code=command.origin_facility_code, actor=actor)
    destination = (
        None
        if command.destination_facility_code is None
        else _facility_for(session, code=command.destination_facility_code, actor=actor)
    )
    if destination is not None and destination.id == origin.id:
        raise ValidationFailed("destination must differ from origin")
    if command.event_type is TraceEventType.DISPATCH and destination is None:
        raise ValidationFailed("a dispatch requires a destination facility")
    target = TRACE_EVENT_RESULT[command.event_type]
    try:
        assert_trade_unit_transition(TradeUnitStatus(unit.status), target)
    except TransitionError as exc:
        raise Conflict(str(exc)) from exc
    if command.event_type is TraceEventType.DISPATCH and command.observed_stamp_count != unit.stamp_count:
        raise ValidationFailed(
            "a dispatch must declare the number of stamps the unit contains",
            detail={
                "unit_stamp_count": str(unit.stamp_count),
                "observed_stamp_count": str(command.observed_stamp_count),
            },
        )
    consignment = _linked_consignment(session, command=command, unit=unit, actor=actor)

    previous = session.execute(
        select(TraceEvent)
        .where(TraceEvent.trade_unit_id == unit.id)
        .order_by(TraceEvent.occurred_at.desc(), TraceEvent.recorded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    event = TraceEvent(
        event_ref=command.event_ref,
        event_type=command.event_type.value,
        trade_unit_id=unit.id,
        company_id=unit.company_id,
        origin_facility_id=origin.id,
        destination_facility_id=None if destination is None else destination.id,
        consignment_id=None if consignment is None else consignment.id,
        observed_stamp_count=command.observed_stamp_count,
        transport_reference=command.transport_reference,
        occurred_at=command.occurred_at,
        recorded_at=now,
        recorded_by=actor.principal_id,
        context={"unit_stamp_count": unit.stamp_count, "unit_level": unit.level},
    )
    session.add(event)
    unit.status = target.value
    session.flush()

    if command.event_type is TraceEventType.DESTRUCTION:
        _void_unit_stamps(session, unit=unit, actor=actor, now=now)

    anomalies = detect_movement_anomalies(session, event=event, previous=previous, now=now)
    enqueue(
        session,
        aggregate_type="trace_event",
        aggregate_id=event.id,
        event_type="trace.event_recorded",
        dedupe_key=f"trace:{event.event_ref}",
        payload={
            "event_ref": event.event_ref,
            "event_type": event.event_type,
            "unit_code": unit.unit_code,
            "company_id": str(unit.company_id),
            "occurred_at": command.occurred_at.isoformat(),
            "observed_stamp_count": command.observed_stamp_count,
            "anomaly_count": len(anomalies),
        },
        available_at=now,
    )
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action=f"trace.{command.event_type.value}",
            target_type="trade_unit",
            target_id=str(unit.id),
            outcome="success",
            after_state={
                "event_ref": event.event_ref,
                "unit_status": unit.status,
                "observed_stamp_count": command.observed_stamp_count,
                "anomalies": [anomaly.kind for anomaly in anomalies],
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return TraceEventResult(event=event, unit_status=target, anomalies=tuple(anomalies))


def _linked_consignment(
    session: Session, *, command: RecordTraceEventCommand, unit: TradeUnit, actor: Actor
) -> Consignment | None:
    if command.consignment_ref is None:
        return None
    consignment = session.execute(
        select(Consignment).where(Consignment.consignment_ref == command.consignment_ref)
    ).scalar_one_or_none()
    if consignment is None:
        raise NotFound("consignment not found", detail={"consignment_ref": command.consignment_ref})
    actor.require_company(consignment.company_id)
    if consignment.company_id != unit.company_id:
        raise Forbidden("consignment belongs to another company")
    if ConsignmentStatus(consignment.status) is ConsignmentStatus.REJECTED:
        raise Conflict("a rejected consignment cannot carry movements")
    if (
        CustomsRegime(consignment.regime) in STAMP_LIABLE_REGIMES
        and ConsignmentStatus(consignment.status) is ConsignmentStatus.DECLARED
    ):
        raise Conflict("link stamps to the consignment before recording movements")
    return consignment


def _void_unit_stamps(session: Session, *, unit: TradeUnit, actor: Actor, now: dt.datetime) -> None:
    """Void every stamp under a destroyed unit; destroyed goods must not verify."""
    stamp_ids = _stamp_ids_under(session, unit_id=unit.id)
    if not stamp_ids:
        return
    stamps = list(
        session.execute(select(Stamp).where(Stamp.id.in_(stamp_ids)).with_for_update()).scalars().all()
    )
    for stamp in stamps:
        if StampStatus(stamp.status) in (StampStatus.VOID, StampStatus.EXPIRED):
            continue
        stamp.status = StampStatus.VOID.value
        stamp.voided_at = now
        session.add(
            StampEvent(
                stamp_id=stamp.id,
                event_type="destroyed",
                actor_principal_id=actor.principal_id,
                context={"unit_code": unit.unit_code},
                created_at=now,
            )
        )
    session.flush()


def _stamp_ids_under(session: Session, *, unit_id: uuid.UUID) -> list[uuid.UUID]:
    """Every stamp in a unit or, transitively, in the units it contains."""
    unit_ids = [unit_id]
    frontier = [unit_id]
    while frontier:
        children = list(
            session.execute(select(TradeUnit.id).where(TradeUnit.parent_unit_id.in_(frontier)))
            .scalars()
            .all()
        )
        if not children:
            break
        unit_ids.extend(children)
        frontier = children
    return list(
        session.execute(
            select(UnitMembership.stamp_id).where(
                UnitMembership.trade_unit_id.in_(unit_ids), UnitMembership.removed_at.is_(None)
            )
        )
        .scalars()
        .all()
    )


def unit_snapshot(unit: TradeUnit) -> JsonObject:
    return {
        "unit_code": unit.unit_code,
        "level": unit.level,
        "status": unit.status,
        "stamp_count": unit.stamp_count,
        "company_id": str(unit.company_id),
        "product_id": str(unit.product_id) if unit.product_id is not None else None,
        "parent_unit_id": str(unit.parent_unit_id) if unit.parent_unit_id is not None else None,
    }


def unit_contents(session: Session, *, unit: TradeUnit) -> JsonObject:
    """The unit with its direct members, for operators reconciling a physical count."""
    serials: JsonArray = list(
        session.execute(
            select(Stamp.serial)
            .join(UnitMembership, UnitMembership.stamp_id == Stamp.id)
            .where(UnitMembership.trade_unit_id == unit.id, UnitMembership.removed_at.is_(None))
            .order_by(Stamp.serial)
        )
        .scalars()
        .all()
    )
    children: JsonArray = list(
        session.execute(
            select(TradeUnit.unit_code)
            .where(TradeUnit.parent_unit_id == unit.id)
            .order_by(TradeUnit.unit_code)
        )
        .scalars()
        .all()
    )
    return {**unit_snapshot(unit), "serials": serials, "child_unit_codes": children}


def units_with_broken_conservation(session: Session) -> list[tuple[str, int, int]]:
    """Units whose recorded stamp count no longer matches what they contain.

    Reported by reconciliation rather than repaired silently: a mismatch means either a
    membership was changed outside the aggregation path or a child was reparented.
    """
    membership_counts = (
        select(
            UnitMembership.trade_unit_id.label("unit_id"),
            func.count(UnitMembership.id).label("members"),
        )
        .where(UnitMembership.removed_at.is_(None))
        .group_by(UnitMembership.trade_unit_id)
        .subquery()
    )
    child = TradeUnit.__table__.alias("child")
    child_counts = (
        select(
            child.c.parent_unit_id.label("unit_id"),
            func.coalesce(func.sum(child.c.stamp_count), 0).label("stamps"),
        )
        .where(child.c.parent_unit_id.is_not(None))
        .group_by(child.c.parent_unit_id)
        .subquery()
    )
    rows = session.execute(
        select(
            TradeUnit.unit_code,
            TradeUnit.stamp_count,
            func.coalesce(membership_counts.c.members, 0) + func.coalesce(child_counts.c.stamps, 0),
        )
        .outerjoin(membership_counts, membership_counts.c.unit_id == TradeUnit.id)
        .outerjoin(child_counts, child_counts.c.unit_id == TradeUnit.id)
        .where(TradeUnit.status != TradeUnitStatus.DISAGGREGATED.value)
    ).all()
    return [(str(row[0]), int(row[1]), int(row[2])) for row in rows if int(row[1]) != int(row[2])]

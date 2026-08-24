"""EPCIS 2.0-shaped serialisation of movement and aggregation records.

This is an interoperability *shape*, not a certification: the documents carry the EPCIS
event structure (event time, record time, business step, disposition, read point,
business location, quantity with unit of measure, parent and child identifiers) so a
downstream system that already speaks EPCIS can ingest them. It has not been validated
against a GS1 conformance suite, and the identifiers are platform URNs rather than
GS1-issued keys, because no GS1 company prefix is configured.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.enums import TraceEventType
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import Consignment, Facility, Product, Stamp, TraceEvent, TradeUnit, UnitMembership

EPCIS_CONTEXT = "https://ref.gs1.org/standards/epcis/2.0.0/epcis-context.jsonld"
URN_PREFIX = "urn:taxstamp"

#: EPCIS business step and disposition for each recorded movement.
BUSINESS_STEPS: dict[TraceEventType, tuple[str, str]] = {
    TraceEventType.DISPATCH: ("shipping", "in_transit"),
    TraceEventType.ARRIVAL: ("receiving", "in_progress"),
    TraceEventType.TRANSLOAD: ("transporting", "in_transit"),
    TraceEventType.EXPORT: ("shipping", "in_transit"),
    TraceEventType.DESTRUCTION: ("destroying", "destroyed"),
}


def unit_urn(unit_code: str) -> str:
    return f"{URN_PREFIX}:unit:{unit_code}"


def stamp_urn(serial: str) -> str:
    return f"{URN_PREFIX}:stamp:{serial}"


def facility_urn(facility_code: str) -> str:
    return f"{URN_PREFIX}:facility:{facility_code}"


def _iso(moment: dt.datetime) -> str:
    return moment.isoformat()


def object_event(session: Session, event: TraceEvent) -> JsonObject:
    """The movement itself, as an EPCIS ObjectEvent over the unit identifier."""
    unit = session.get(TradeUnit, event.trade_unit_id)
    origin = session.get(Facility, event.origin_facility_id)
    destination = (
        None
        if event.destination_facility_id is None
        else session.get(Facility, event.destination_facility_id)
    )
    event_type = TraceEventType(event.event_type)
    business_step, disposition = BUSINESS_STEPS[event_type]
    product = None if unit is None or unit.product_id is None else session.get(Product, unit.product_id)
    consignment = None if event.consignment_id is None else session.get(Consignment, event.consignment_id)
    document: JsonObject = {
        "type": "ObjectEvent",
        "eventID": f"{URN_PREFIX}:event:{event.event_ref}",
        "eventTime": _iso(event.occurred_at),
        "recordTime": _iso(event.recorded_at),
        "eventTimeZoneOffset": "+00:00",
        "action": "OBSERVE",
        "bizStep": business_step,
        "disposition": disposition,
        "epcList": [] if unit is None else [unit_urn(unit.unit_code)],
        "readPoint": {"id": facility_urn("" if origin is None else origin.facility_code)},
        "bizLocation": {
            "id": facility_urn(
                (origin.facility_code if origin is not None else "")
                if destination is None
                else destination.facility_code
            )
        },
        "quantityList": [
            {
                "epcClass": f"{URN_PREFIX}:product:" + ("unknown" if product is None else product.sku),
                "quantity": event.observed_stamp_count,
                "uom": "EA",
            }
        ],
    }
    if consignment is not None:
        document["bizTransactionList"] = [
            {
                "type": "urn:epcglobal:cbv:btt:desadv",
                "bizTransaction": f"{URN_PREFIX}:consignment:{consignment.consignment_ref}",
            }
        ]
    if event.transport_reference:
        document["sourceList"] = [
            {"type": "urn:epcglobal:cbv:sdt:possessing_party", "source": event.transport_reference}
        ]
    return document


def aggregation_event(session: Session, unit: TradeUnit, *, recorded_at: dt.datetime) -> JsonObject:
    """The unit's composition, as an EPCIS AggregationEvent."""
    serials = list(
        session.execute(
            select(Stamp.serial)
            .join(UnitMembership, UnitMembership.stamp_id == Stamp.id)
            .where(UnitMembership.trade_unit_id == unit.id, UnitMembership.removed_at.is_(None))
            .order_by(Stamp.serial)
        )
        .scalars()
        .all()
    )
    children = list(
        session.execute(
            select(TradeUnit.unit_code)
            .where(TradeUnit.parent_unit_id == unit.id)
            .order_by(TradeUnit.unit_code)
        )
        .scalars()
        .all()
    )
    child_epcs: JsonArray = [stamp_urn(serial) for serial in serials]
    child_epcs.extend(unit_urn(code) for code in children)
    return {
        "type": "AggregationEvent",
        "eventID": f"{URN_PREFIX}:aggregation:{unit.unit_code}",
        "eventTime": _iso(unit.closed_at or unit.created_at),
        "recordTime": _iso(recorded_at),
        "eventTimeZoneOffset": "+00:00",
        "action": "ADD",
        "bizStep": "packing",
        "disposition": "in_progress",
        "parentID": unit_urn(unit.unit_code),
        "childEPCs": child_epcs,
        "childQuantityList": [
            {
                "epcClass": f"{URN_PREFIX}:unit:{unit.level}",
                "quantity": unit.stamp_count,
                "uom": "EA",
            }
        ],
    }


def epcis_document(events: JsonArray, *, created_at: dt.datetime) -> JsonObject:
    """Wrap events in an EPCIS 2.0 JSON-LD document envelope."""
    return {
        "@context": [EPCIS_CONTEXT],
        "type": "EPCISDocument",
        "schemaVersion": "2.0",
        "creationDate": _iso(created_at),
        "epcisBody": {"eventList": events},
        "conformance": {
            "validated_against_gs1_conformance_suite": False,
            "identifier_scheme": "platform_urn",
            "detail": "EPCIS-shaped output; no GS1 company prefix is configured",
        },
    }

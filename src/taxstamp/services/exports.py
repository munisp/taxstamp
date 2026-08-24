"""Machine-readable exports: portability for a licence holder, disclosure for a regulator.

Every export is hashed and signed, and the hash is recorded in ``data_exports`` before
the document is handed over, so a document produced later can be checked against what
the platform says it released, and so a disclosure has an audit trail naming the
requester.

Delivery to a regulator endpoint is a separate step and fails closed: with no endpoint
configured the export is produced and recorded but the platform states that nothing was
delivered, rather than reporting a submission that never happened.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import ExportKind, Role
from taxstamp.errors import Conflict, NotFound, ValidationFailed
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import (
    Company,
    Consignment,
    DataExport,
    Licence,
    Order,
    Product,
    StampBatch,
    TraceEvent,
    TradeUnit,
)
from taxstamp.security import document_hash, document_signature_matches, sign_document
from taxstamp.services.context import Actor
from taxstamp.services.epcis import aggregation_event, epcis_document, object_event

EXPORT_PURPOSE = "data-export"
MAX_EXPORT_ROWS = 5_000


@dataclass(frozen=True, slots=True)
class ExportResult:
    record: DataExport
    document: JsonObject

    def envelope(self) -> JsonObject:
        return {
            "export_ref": self.record.export_ref,
            "kind": self.record.kind,
            "record_count": self.record.record_count,
            "content_hash": self.record.content_hash,
            "signature": self.record.signature,
            "created_at": self.record.created_at.isoformat(),
            "payload": self.document,
        }


@dataclass(frozen=True, slots=True)
class RegulatorExportCommand:
    export_ref: str
    company_id: uuid.UUID | None
    occurred_from: dt.datetime | None
    occurred_to: dt.datetime | None


def portability_export(
    session: Session,
    *,
    actor: Actor,
    export_ref: str,
    company_id: uuid.UUID,
    now: dt.datetime,
    export_secret: str,
    audit_secret: str,
    revision: str,
) -> ExportResult:
    """Everything the platform holds about one company, in a re-importable shape."""
    actor.require_role(Role.REQUESTER, Role.ADMIN)
    actor.require_company(company_id)
    company = session.get(Company, company_id)
    if company is None:
        raise NotFound("company not found")
    _assert_unused_ref(session, export_ref)

    licences = _rows(session, select(Licence).where(Licence.company_id == company_id))
    products = _rows(session, select(Product).where(Product.company_id == company_id))
    orders = _rows(session, select(Order).where(Order.company_id == company_id))
    batches = _rows(
        session,
        select(StampBatch).join(Order, Order.id == StampBatch.order_id).where(Order.company_id == company_id),
    )
    units = _rows(session, select(TradeUnit).where(TradeUnit.company_id == company_id))
    consignments = _rows(session, select(Consignment).where(Consignment.company_id == company_id))
    events = _rows(session, select(TraceEvent).where(TraceEvent.company_id == company_id))

    document: JsonObject = {
        "schema": "taxstamp.portability.v1",
        "company": {
            "id": str(company.id),
            "name": company.name,
            "tin": company.tin,
        },
        "licences": [
            {
                "licence_number": row.licence_number,
                "licence_type": row.licence_type,
                "status": row.status,
                "product_categories": list(row.product_categories),
                "valid_from": row.valid_from.isoformat(),
                "valid_to": None if row.valid_to is None else row.valid_to.isoformat(),
            }
            for row in licences
        ],
        "products": [
            {
                "sku": row.sku,
                "brand": row.brand,
                "product_category": row.product_category,
                "pack_size": row.pack_size,
                "unit_of_measure": row.unit_of_measure,
                "intended_market": row.intended_market,
                "status": row.status,
            }
            for row in products
        ],
        "orders": [
            {
                "order_ref": row.order_ref,
                "status": row.status,
                "product_category": row.product_category,
                "quantity": row.quantity,
                "total_minor": row.total_minor,
                "currency": row.currency,
                "created_at": row.created_at.isoformat(),
            }
            for row in orders
        ],
        "batches": [
            {
                "batch_id": str(row.id),
                "status": row.status,
                "requested_count": row.requested_count,
                "issued_count": row.issued_count,
            }
            for row in batches
        ],
        "trade_units": [
            {
                "unit_code": row.unit_code,
                "level": row.level,
                "status": row.status,
                "stamp_count": row.stamp_count,
            }
            for row in units
        ],
        "consignments": [
            {
                "consignment_ref": row.consignment_ref,
                "regime": row.regime,
                "declared_quantity": row.declared_quantity,
                "status": row.status,
                "customs_declaration_reference": row.customs_declaration_reference,
            }
            for row in consignments
        ],
        "movements": [
            {
                "event_ref": row.event_ref,
                "event_type": row.event_type,
                "observed_stamp_count": row.observed_stamp_count,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in events
        ],
    }
    record_count = (
        len(licences)
        + len(products)
        + len(orders)
        + len(batches)
        + len(units)
        + len(consignments)
        + len(events)
    )
    return _persist(
        session,
        actor=actor,
        export_ref=export_ref,
        kind=ExportKind.PORTABILITY,
        company_id=company_id,
        scope={"company_id": str(company_id)},
        document=document,
        record_count=record_count,
        now=now,
        export_secret=export_secret,
        audit_secret=audit_secret,
        revision=revision,
    )


def regulator_export(
    session: Session,
    *,
    actor: Actor,
    command: RegulatorExportCommand,
    now: dt.datetime,
    export_secret: str,
    audit_secret: str,
    revision: str,
) -> ExportResult:
    """An EPCIS-shaped disclosure of movements and aggregations for the regulator."""
    actor.require_role(Role.ANALYST, Role.SUPERVISOR, Role.ADMIN)
    _assert_unused_ref(session, command.export_ref)
    if (
        command.occurred_from is not None
        and command.occurred_to is not None
        and command.occurred_to < command.occurred_from
    ):
        raise ValidationFailed("occurred_to must not precede occurred_from")

    statement = select(TraceEvent).order_by(TraceEvent.occurred_at)
    if command.company_id is not None:
        statement = statement.where(TraceEvent.company_id == command.company_id)
    if command.occurred_from is not None:
        statement = statement.where(TraceEvent.occurred_at >= command.occurred_from)
    if command.occurred_to is not None:
        statement = statement.where(TraceEvent.occurred_at <= command.occurred_to)
    events = _rows(session, statement)

    serialised: JsonArray = [object_event(session, event) for event in events]
    unit_ids = {event.trade_unit_id for event in events}
    if unit_ids:
        units = _rows(session, select(TradeUnit).where(TradeUnit.id.in_(unit_ids)))
        serialised.extend(aggregation_event(session, unit, recorded_at=now) for unit in units)
    document = epcis_document(serialised, created_at=now)
    return _persist(
        session,
        actor=actor,
        export_ref=command.export_ref,
        kind=ExportKind.REGULATOR,
        company_id=command.company_id,
        scope={
            "company_id": None if command.company_id is None else str(command.company_id),
            "occurred_from": None if command.occurred_from is None else command.occurred_from.isoformat(),
            "occurred_to": None if command.occurred_to is None else command.occurred_to.isoformat(),
        },
        document=document,
        record_count=len(serialised),
        now=now,
        export_secret=export_secret,
        audit_secret=audit_secret,
        revision=revision,
    )


def _rows[RowT](session: Session, statement: Select[tuple[RowT]]) -> list[RowT]:
    """Bounded read: an export that would exceed the cap is refused, not truncated."""
    rows = list(session.execute(statement.limit(MAX_EXPORT_ROWS + 1)).scalars().all())
    if len(rows) > MAX_EXPORT_ROWS:
        raise ValidationFailed(
            "the requested export is too large; narrow the scope",
            detail={"max_rows": str(MAX_EXPORT_ROWS)},
        )
    return rows


def _assert_unused_ref(session: Session, export_ref: str) -> None:
    if not export_ref.strip():
        raise ValidationFailed("export_ref is required")
    if session.execute(select(DataExport.id).where(DataExport.export_ref == export_ref)).scalar_one_or_none():
        raise Conflict("an export with this reference already exists")


def _persist(
    session: Session,
    *,
    actor: Actor,
    export_ref: str,
    kind: ExportKind,
    company_id: uuid.UUID | None,
    scope: JsonObject,
    document: JsonObject,
    record_count: int,
    now: dt.datetime,
    export_secret: str,
    audit_secret: str,
    revision: str,
) -> ExportResult:
    content_hash = document_hash(document)
    record = DataExport(
        export_ref=export_ref,
        kind=kind.value,
        company_id=company_id,
        scope=scope,
        record_count=record_count,
        content_hash=content_hash,
        signature=sign_document(
            {"export_ref": export_ref, "kind": kind.value, "content_hash": content_hash},
            secret=export_secret,
            purpose=EXPORT_PURPOSE,
        ),
        requested_by=actor.principal_id,
        created_at=now,
    )
    session.add(record)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action=f"export.{kind.value}",
            target_type="data_export",
            target_id=str(record.id),
            outcome="success",
            after_state={
                "export_ref": export_ref,
                "record_count": record_count,
                "content_hash": content_hash,
                "scope": scope,
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return ExportResult(record=record, document=document)


def export_integrity_failures(session: Session, *, export_secret: str) -> list[str]:
    """Exports whose recorded signature does not match their recorded hash."""
    findings: list[str] = []
    for record in _rows(session, select(DataExport)):
        expected = sign_document(
            {
                "export_ref": record.export_ref,
                "kind": record.kind,
                "content_hash": record.content_hash,
            },
            secret=export_secret,
            purpose=EXPORT_PURPOSE,
        )
        if not document_signature_matches(record.signature, expected):
            findings.append(record.export_ref)
    return findings

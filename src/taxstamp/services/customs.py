"""Import, free-zone, transit and duty-free consignments.

No customs system is integrated, so this module records what was declared and controls
what may follow from it. Two rules carry the fiscal weight:

* goods under a stamp-liable regime cannot be released into the domestic market until
  stamps covering the declared quantity are linked to the consignment;
* goods under a regime that is not stamp-liable (transit, free zone, duty free) cannot
  be released domestically at all through this path, because the duty was never paid.

The declaration reference is operator-entered evidence. Release therefore records the
evidence a human relied on; it never claims that customs confirmed anything.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import (
    STAMP_LIABLE_REGIMES,
    ConsignmentStatus,
    CustomsRegime,
    Role,
    StampStatus,
    TransitionError,
    assert_consignment_transition,
)
from taxstamp.errors import Conflict, Forbidden, NotFound, ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.models import (
    Company,
    Consignment,
    ConsignmentStamp,
    Facility,
    Product,
    Stamp,
)
from taxstamp.outbox import enqueue
from taxstamp.serials import is_valid_serial
from taxstamp.services.context import CROSS_TENANT_READERS, Actor
from taxstamp.services.registry import effective_ordering_licence

MAX_LINKED_SERIALS = 5_000

#: Stamp states that may still be assigned to an incoming consignment.
LINKABLE_STAMP_STATUSES = frozenset({StampStatus.ISSUED, StampStatus.ACTIVE})


@dataclass(frozen=True, slots=True)
class DeclareConsignmentCommand:
    consignment_ref: str
    company_id: uuid.UUID
    regime: CustomsRegime
    product_id: uuid.UUID
    declared_quantity: int
    customs_declaration_reference: str
    origin_country: str
    entry_facility_code: str
    order_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class LinkStampsCommand:
    consignment_ref: str
    serials: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseCommand:
    consignment_ref: str
    customs_evidence_reference: str


def declare_consignment(
    session: Session,
    *,
    actor: Actor,
    command: DeclareConsignmentCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Consignment:
    actor.require_role(Role.REQUESTER, Role.OPERATOR, Role.ADMIN)
    actor.require_company(command.company_id)
    if command.declared_quantity <= 0:
        raise ValidationFailed("declared_quantity must be positive")
    if len(command.origin_country) != 2 or not command.origin_country.isalpha():
        raise ValidationFailed("origin_country must be an ISO 3166-1 alpha-2 code")
    if not command.customs_declaration_reference.strip():
        raise ValidationFailed("a customs declaration reference is required")
    if session.get(Company, command.company_id) is None:
        raise NotFound("company not found")
    if session.execute(
        select(Consignment.id).where(Consignment.consignment_ref == command.consignment_ref)
    ).scalar_one_or_none():
        raise Conflict("a consignment with this reference already exists")

    product = session.get(Product, command.product_id)
    if product is None:
        raise NotFound("product not found")
    if product.company_id != command.company_id:
        raise Forbidden("product belongs to another company")
    facility = session.execute(
        select(Facility).where(Facility.facility_code == command.entry_facility_code)
    ).scalar_one_or_none()
    if facility is None:
        raise NotFound("entry facility not found", detail={"facility_code": command.entry_facility_code})
    if not facility.active:
        raise ValidationFailed("entry facility is not active")
    if command.regime in STAMP_LIABLE_REGIMES:
        # An importer that may not procure stamps also may not declare goods that will
        # need them: the shortfall would only surface at release.
        effective_ordering_licence(
            session,
            company_id=command.company_id,
            product_category=product.product_category,
            now=now,
        )

    consignment = Consignment(
        consignment_ref=command.consignment_ref,
        company_id=command.company_id,
        regime=command.regime.value,
        product_id=product.id,
        declared_quantity=command.declared_quantity,
        customs_declaration_reference=command.customs_declaration_reference,
        origin_country=command.origin_country.upper(),
        entry_facility_id=facility.id,
        order_id=command.order_id,
        status=ConsignmentStatus.DECLARED.value,
        status_reason="",
        declared_by=actor.principal_id,
        created_at=now,
    )
    session.add(consignment)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="consignment.declare",
            target_type="consignment",
            target_id=str(consignment.id),
            outcome="success",
            after_state=consignment_snapshot(consignment),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return consignment


def link_stamps(
    session: Session,
    *,
    actor: Actor,
    command: LinkStampsCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Consignment:
    """Attach the stamps that cover an import consignment's declared quantity."""
    actor.require_role(Role.REQUESTER, Role.OPERATOR, Role.ADMIN)
    consignment = _locked_consignment(session, ref=command.consignment_ref, actor=actor)
    if CustomsRegime(consignment.regime) not in STAMP_LIABLE_REGIMES:
        raise Conflict(
            "this regime carries no domestic stamp liability",
            detail={"regime": consignment.regime},
        )
    if ConsignmentStatus(consignment.status) not in (
        ConsignmentStatus.DECLARED,
        ConsignmentStatus.STAMPS_LINKED,
    ):
        raise Conflict("stamps cannot be linked to a released or rejected consignment")

    stamps = _linkable_stamps(session, command=command, consignment=consignment)
    for stamp in stamps:
        session.add(
            ConsignmentStamp(
                consignment_id=consignment.id,
                stamp_id=stamp.id,
                linked_at=now,
                linked_by=actor.principal_id,
            )
        )
    session.flush()
    linked = _linked_count(session, consignment_id=consignment.id)
    if linked > consignment.declared_quantity:
        raise ValidationFailed(
            "more stamps are linked than the consignment declares",
            detail={
                "declared_quantity": str(consignment.declared_quantity),
                "linked": str(linked),
            },
        )
    if linked == consignment.declared_quantity:
        _advance(consignment, ConsignmentStatus.STAMPS_LINKED)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="consignment.link_stamps",
            target_type="consignment",
            target_id=str(consignment.id),
            outcome="success",
            after_state={**consignment_snapshot(consignment), "linked_stamps": linked},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return consignment


def _linkable_stamps(
    session: Session, *, command: LinkStampsCommand, consignment: Consignment
) -> list[Stamp]:
    deduped = list(dict.fromkeys(command.serials))
    if not deduped:
        raise ValidationFailed("at least one serial is required")
    if len(deduped) > MAX_LINKED_SERIALS:
        raise ValidationFailed(f"at most {MAX_LINKED_SERIALS} serials may be linked at once")
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
    foreign = [stamp.serial for stamp in stamps if stamp.company_id != consignment.company_id]
    if foreign:
        raise Forbidden("one or more stamps belong to another company")
    unusable = [stamp.serial for stamp in stamps if StampStatus(stamp.status) not in LINKABLE_STAMP_STATUSES]
    if unusable:
        raise Conflict(
            "one or more stamps are void or expired",
            detail={"serials": ",".join(sorted(unusable)[:10])},
        )
    already = list(
        session.execute(
            select(Stamp.serial)
            .join(ConsignmentStamp, ConsignmentStamp.stamp_id == Stamp.id)
            .where(Stamp.id.in_([stamp.id for stamp in stamps]))
        )
        .scalars()
        .all()
    )
    if already:
        raise Conflict(
            "one or more stamps are already linked to a consignment",
            detail={"serials": ",".join(sorted(already)[:10])},
        )
    return [found[serial] for serial in deduped]


def release_consignment(
    session: Session,
    *,
    actor: Actor,
    command: ReleaseCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Consignment:
    """Release goods into the domestic market once their stamps are accounted for."""
    actor.require_role(Role.SUPERVISOR, Role.ADMIN)
    if not command.customs_evidence_reference.strip():
        raise ValidationFailed("a customs evidence reference is required to release goods")
    consignment = _locked_consignment(session, ref=command.consignment_ref, actor=actor)
    regime = CustomsRegime(consignment.regime)
    if regime not in STAMP_LIABLE_REGIMES:
        raise Conflict(
            "goods under this regime may not be released into the domestic market",
            detail={"regime": regime.value},
        )
    linked = _linked_count(session, consignment_id=consignment.id)
    if linked != consignment.declared_quantity:
        raise Conflict(
            "the declared quantity is not fully covered by linked stamps",
            detail={"declared_quantity": str(consignment.declared_quantity), "linked": str(linked)},
        )
    _advance(consignment, ConsignmentStatus.RELEASED)
    consignment.status_reason = command.customs_evidence_reference
    consignment.released_at = now
    session.flush()
    enqueue(
        session,
        aggregate_type="consignment",
        aggregate_id=consignment.id,
        event_type="consignment.released",
        dedupe_key=f"consignment-release:{consignment.consignment_ref}",
        payload={
            "consignment_ref": consignment.consignment_ref,
            "company_id": str(consignment.company_id),
            "regime": consignment.regime,
            "declared_quantity": consignment.declared_quantity,
            "linked_stamps": linked,
            "customs_evidence_reference": command.customs_evidence_reference,
        },
        available_at=now,
    )
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="consignment.release",
            target_type="consignment",
            target_id=str(consignment.id),
            outcome="success",
            after_state={**consignment_snapshot(consignment), "linked_stamps": linked},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return consignment


def reject_consignment(
    session: Session,
    *,
    actor: Actor,
    consignment_ref: str,
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Consignment:
    actor.require_role(Role.SUPERVISOR, Role.ADMIN)
    if not reason.strip():
        raise ValidationFailed("a reason is required to reject a consignment")
    consignment = _locked_consignment(session, ref=consignment_ref, actor=actor)
    before = consignment_snapshot(consignment)
    _advance(consignment, ConsignmentStatus.REJECTED)
    consignment.status_reason = reason
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="consignment.reject",
            target_type="consignment",
            target_id=str(consignment.id),
            outcome="success",
            before_state=before,
            after_state=consignment_snapshot(consignment),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return consignment


def _advance(consignment: Consignment, target: ConsignmentStatus) -> None:
    try:
        assert_consignment_transition(ConsignmentStatus(consignment.status), target)
    except TransitionError as exc:
        raise Conflict(str(exc)) from exc
    consignment.status = target.value


def _locked_consignment(session: Session, *, ref: str, actor: Actor) -> Consignment:
    consignment = session.execute(
        select(Consignment).where(Consignment.consignment_ref == ref).with_for_update()
    ).scalar_one_or_none()
    if consignment is None:
        raise NotFound("consignment not found", detail={"consignment_ref": ref})
    # Release and rejection are customs decisions taken by staff who hold no company.
    if actor.role not in CROSS_TENANT_READERS:
        actor.require_company(consignment.company_id)
    return consignment


def _linked_count(session: Session, *, consignment_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count(ConsignmentStamp.id)).where(ConsignmentStamp.consignment_id == consignment_id)
        ).scalar_one()
    )


def consignment_for_read(session: Session, *, actor: Actor, consignment_ref: str) -> Consignment:
    """A consignment the actor is entitled to see, without taking a row lock."""
    consignment = session.execute(
        select(Consignment).where(Consignment.consignment_ref == consignment_ref)
    ).scalar_one_or_none()
    if consignment is None:
        raise NotFound("consignment not found", detail={"consignment_ref": consignment_ref})
    if actor.role not in CROSS_TENANT_READERS:
        actor.require_company(consignment.company_id)
    return consignment


def consignment_snapshot(consignment: Consignment) -> JsonObject:
    return {
        "consignment_ref": consignment.consignment_ref,
        "company_id": str(consignment.company_id),
        "regime": consignment.regime,
        "product_id": str(consignment.product_id),
        "declared_quantity": consignment.declared_quantity,
        "customs_declaration_reference": consignment.customs_declaration_reference,
        "origin_country": consignment.origin_country,
        "status": consignment.status,
        "status_reason": consignment.status_reason,
    }


def consignment_document(session: Session, *, consignment: Consignment) -> JsonObject:
    return {
        **consignment_snapshot(consignment),
        "id": str(consignment.id),
        "linked_stamps": _linked_count(session, consignment_id=consignment.id),
        "released_at": (None if consignment.released_at is None else consignment.released_at.isoformat()),
        "created_at": consignment.created_at.isoformat(),
    }


def consignments_short_of_stamps(session: Session) -> list[tuple[str, int, int]]:
    """Stamp-liable consignments whose linked stamps do not cover what was declared.

    Reported rather than blocked at declaration time: linking legitimately lags the
    declaration, so the shortfall is a standing reconciliation finding until release.
    """
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
            Consignment.consignment_ref,
            Consignment.declared_quantity,
            func.coalesce(linked.c.linked, 0),
        )
        .outerjoin(linked, linked.c.consignment_id == Consignment.id)
        .where(
            Consignment.regime.in_([regime.value for regime in STAMP_LIABLE_REGIMES]),
            Consignment.status.in_([ConsignmentStatus.DECLARED.value, ConsignmentStatus.STAMPS_LINKED.value]),
        )
    ).all()
    return [(str(row[0]), int(row[1]), int(row[2])) for row in rows if int(row[2]) != int(row[1])]

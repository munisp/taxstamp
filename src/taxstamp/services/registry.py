"""Licence and product master data.

Licensing is a control, not a record: a company can only procure stamps for a product
category covered by an effective licence of an ordering type, and a suspended or revoked
licence stops new procurement immediately while leaving history intact.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import (
    ORDERING_LICENCE_TYPES,
    LicenceStatus,
    LicenceType,
    ProductStatus,
    Role,
)
from taxstamp.errors import Conflict, Forbidden, NotFound, ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Company, Licence, Product, Tariff
from taxstamp.serials import CATEGORY_CODES
from taxstamp.services.context import Actor


@dataclass(frozen=True, slots=True)
class IssueLicenceCommand:
    company_id: uuid.UUID
    licence_number: str
    licence_type: LicenceType
    product_categories: tuple[str, ...]
    valid_from: dt.datetime
    valid_to: dt.datetime | None
    statutory_reference: str


@dataclass(frozen=True, slots=True)
class RegisterProductCommand:
    company_id: uuid.UUID
    sku: str
    brand: str
    product_category: str
    pack_size: int
    unit_of_measure: str
    intended_market: str


def _validate_categories(categories: tuple[str, ...]) -> tuple[str, ...]:
    if not categories:
        raise ValidationFailed("a licence must cover at least one product category")
    unknown = sorted(set(categories) - set(CATEGORY_CODES))
    if unknown:
        raise ValidationFailed(
            "unsupported product category on licence", detail={"categories": ",".join(unknown)}
        )
    return tuple(sorted(set(categories)))


def issue_licence(
    session: Session,
    *,
    actor: Actor,
    command: IssueLicenceCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Licence:
    actor.require_role(Role.ADMIN)
    company = session.get(Company, command.company_id)
    if company is None:
        raise NotFound("company not found")
    categories = _validate_categories(command.product_categories)
    if command.valid_to is not None and command.valid_to <= command.valid_from:
        raise ValidationFailed("valid_to must be after valid_from")
    duplicate = session.execute(
        select(Licence).where(Licence.licence_number == command.licence_number)
    ).scalar_one_or_none()
    if duplicate is not None:
        raise Conflict("a licence with this number already exists")

    licence = Licence(
        licence_number=command.licence_number,
        company_id=company.id,
        licence_type=command.licence_type.value,
        product_categories=list(categories),
        status=LicenceStatus.ACTIVE.value,
        valid_from=command.valid_from,
        valid_to=command.valid_to,
        statutory_reference=command.statutory_reference,
        created_at=now,
    )
    session.add(licence)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="licence.issue",
            target_type="licence",
            target_id=str(licence.id),
            outcome="success",
            after_state=licence_snapshot(licence),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return licence


def change_licence_status(
    session: Session,
    *,
    actor: Actor,
    licence_id: uuid.UUID,
    status: LicenceStatus,
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Licence:
    actor.require_role(Role.ADMIN)
    licence = session.execute(
        select(Licence).where(Licence.id == licence_id).with_for_update()
    ).scalar_one_or_none()
    if licence is None:
        raise NotFound("licence not found")
    if LicenceStatus(licence.status) is LicenceStatus.REVOKED:
        raise Conflict("a revoked licence cannot change status")
    before = licence_snapshot(licence)
    licence.status = status.value
    licence.status_reason = reason
    licence.status_changed_at = now
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action=f"licence.{status.value}",
            target_type="licence",
            target_id=str(licence.id),
            outcome="success",
            before_state=before,
            after_state=licence_snapshot(licence),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return licence


def licence_snapshot(licence: Licence) -> JsonObject:
    return {
        "licence_number": licence.licence_number,
        "licence_type": licence.licence_type,
        "status": licence.status,
        "product_categories": list(licence.product_categories),
        "company_id": str(licence.company_id),
    }


def effective_ordering_licence(
    session: Session, *, company_id: uuid.UUID, product_category: str, now: dt.datetime
) -> Licence:
    """The licence that entitles this company to procure stamps for this category.

    Raises ``Forbidden`` when no licence covers the request: procurement without an
    effective licence is a control failure, not a validation nicety.
    """
    candidates = (
        session.execute(
            select(Licence)
            .where(
                Licence.company_id == company_id,
                Licence.status == LicenceStatus.ACTIVE.value,
                Licence.valid_from <= now,
                Licence.licence_type.in_([kind.value for kind in ORDERING_LICENCE_TYPES]),
            )
            .order_by(Licence.valid_from.desc())
        )
        .scalars()
        .all()
    )
    for licence in candidates:
        if licence.valid_to is not None and licence.valid_to <= now:
            continue
        if product_category in licence.product_categories:
            return licence
    raise Forbidden(
        "no effective licence covers this product category",
        detail={"product_category": product_category},
    )


def register_product(
    session: Session,
    *,
    actor: Actor,
    command: RegisterProductCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Product:
    actor.require_role(Role.REQUESTER, Role.ADMIN)
    actor.require_company(command.company_id)
    if command.product_category not in CATEGORY_CODES:
        raise ValidationFailed(
            "unsupported product category", detail={"product_category": command.product_category}
        )
    if session.get(Company, command.company_id) is None:
        raise NotFound("company not found")
    existing = session.execute(
        select(Product).where(Product.company_id == command.company_id, Product.sku == command.sku)
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("this company already registered a product with that SKU")

    product = Product(
        company_id=command.company_id,
        sku=command.sku,
        brand=command.brand,
        product_category=command.product_category,
        pack_size=command.pack_size,
        unit_of_measure=command.unit_of_measure,
        intended_market=command.intended_market,
        status=ProductStatus.ACTIVE.value,
        created_at=now,
    )
    session.add(product)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="product.register",
            target_type="product",
            target_id=str(product.id),
            outcome="success",
            after_state=product_snapshot(product),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return product


def withdraw_product(
    session: Session,
    *,
    actor: Actor,
    product_id: uuid.UUID,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Product:
    product = session.execute(
        select(Product).where(Product.id == product_id).with_for_update()
    ).scalar_one_or_none()
    if product is None:
        raise NotFound("product not found")
    actor.require_role(Role.REQUESTER, Role.ADMIN)
    actor.require_company(product.company_id)
    if ProductStatus(product.status) is ProductStatus.WITHDRAWN:
        raise Conflict("product is already withdrawn")
    product.status = ProductStatus.WITHDRAWN.value
    product.withdrawn_at = now
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="product.withdraw",
            target_type="product",
            target_id=str(product.id),
            outcome="success",
            after_state=product_snapshot(product),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return product


def product_snapshot(product: Product) -> JsonObject:
    return {
        "sku": product.sku,
        "brand": product.brand,
        "product_category": product.product_category,
        "pack_size": product.pack_size,
        "unit_of_measure": product.unit_of_measure,
        "intended_market": product.intended_market,
        "status": product.status,
        "company_id": str(product.company_id),
    }


def orderable_product(session: Session, *, product_id: uuid.UUID, company_id: uuid.UUID) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise NotFound("product not found")
    if product.company_id != company_id:
        raise Forbidden("product belongs to another company")
    if ProductStatus(product.status) is not ProductStatus.ACTIVE:
        raise ValidationFailed(
            "product is withdrawn and cannot be ordered against",
            detail={"product_id": str(product.id)},
        )
    return product


def assert_tariff_period_free(
    session: Session,
    *,
    product_category: str,
    effective_from: dt.datetime,
    effective_to: dt.datetime | None,
) -> None:
    """Refuse a tariff whose effective range overlaps an existing one."""
    clash = session.execute(
        select(Tariff.id)
        .where(
            Tariff.product_category == product_category,
            (Tariff.effective_to.is_(None)) | (Tariff.effective_to > effective_from),
            *(() if effective_to is None else (Tariff.effective_from < effective_to,)),
        )
        .limit(1)
    ).scalar_one_or_none()
    if clash is not None:
        raise Conflict(
            "an existing tariff already covers part of this period",
            detail={"product_category": product_category, "existing_tariff_id": str(clash)},
        )


def overlapping_tariffs(session: Session) -> list[tuple[str, uuid.UUID, uuid.UUID]]:
    """Tariff pairs whose effective ranges overlap for one category.

    Two overlapping rows make pricing ambiguous, so the platform reports them instead
    of silently taking the most recent one.
    """
    later = select(Tariff).subquery()
    rows = session.execute(
        select(Tariff.product_category, Tariff.id, later.c.id)
        .join(
            later,
            (later.c.product_category == Tariff.product_category) & (later.c.id != Tariff.id),
        )
        .where(
            (Tariff.effective_from < later.c.effective_from)
            | ((Tariff.effective_from == later.c.effective_from) & (Tariff.id < later.c.id)),
            (Tariff.effective_to.is_(None)) | (Tariff.effective_to > later.c.effective_from),
        )
    ).all()
    return [(str(row[0]), row[1], row[2]) for row in rows]

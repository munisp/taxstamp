"""Licence and product master data endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, utc
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import (
    IssueLicenceRequest,
    LicenceStatusRequest,
    RegisterProductRequest,
)
from taxstamp.enums import LicenceType, Role
from taxstamp.errors import Forbidden
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Licence, Product
from taxstamp.services import registry as registry_service
from taxstamp.services.context import Actor

router = APIRouter(prefix="/v1", tags=["registry"])

#: Roles that supervise the register as a whole and therefore read across tenants.
CROSS_TENANT_READERS: frozenset[Role] = frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN})


def _licence_document(licence: Licence) -> JsonObject:
    return {
        "id": str(licence.id),
        "licence_number": licence.licence_number,
        "company_id": str(licence.company_id),
        "licence_type": licence.licence_type,
        "product_categories": list(licence.product_categories),
        "status": licence.status,
        "status_reason": licence.status_reason,
        "valid_from": utc(licence.valid_from),
        "valid_to": utc(licence.valid_to) if licence.valid_to is not None else None,
        "statutory_reference": licence.statutory_reference,
        "created_at": utc(licence.created_at),
    }


def _tenant_filter(actor: Actor) -> uuid.UUID | None:
    """The company a listing must be restricted to, or None for a supervisory reader.

    Master data identifies brands, markets and entitlements, so a credential without a
    supervisory role sees only its own company's rows and a credential with no company
    at all sees nothing.
    """
    if actor.role in CROSS_TENANT_READERS:
        return None
    if actor.company_id is None:
        raise Forbidden("this credential may not read the register")
    return actor.company_id


def _product_document(product: Product) -> JsonObject:
    return {
        "id": str(product.id),
        "company_id": str(product.company_id),
        "sku": product.sku,
        "brand": product.brand,
        "product_category": product.product_category,
        "pack_size": product.pack_size,
        "unit_of_measure": product.unit_of_measure,
        "intended_market": product.intended_market,
        "status": product.status,
        "created_at": utc(product.created_at),
    }


@router.post("/licences", status_code=201)
def issue_licence(
    body: IssueLicenceRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        licence = registry_service.issue_licence(
            session,
            actor=actor,
            command=registry_service.IssueLicenceCommand(
                company_id=body.company_id,
                licence_number=body.licence_number,
                licence_type=LicenceType(body.licence_type),
                product_categories=tuple(body.product_categories),
                valid_from=body.valid_from,
                valid_to=body.valid_to,
                statutory_reference=body.statutory_reference,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return _licence_document(licence)

    status, document = run_idempotent(
        runtime,
        scope="licences.issue",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/licences/{licence_id}/status")
def change_licence_status(
    licence_id: uuid.UUID,
    body: LicenceStatusRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        licence = registry_service.change_licence_status(
            session,
            actor=actor,
            licence_id=licence_id,
            status=body.status,
            reason=body.reason,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return _licence_document(licence)

    status, document = run_idempotent(
        runtime,
        scope="licences.status",
        key=key,
        actor=actor,
        payload={"licence_id": str(licence_id), **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/licences")
def list_licences(
    runtime: RuntimeDep,
    current: CurrentActor,
    limit: int = 50,
    offset: int = 0,
) -> JsonObject:
    actor = current.actor
    bounded = max(1, min(limit, 200))
    with runtime.session_factory() as session:
        query = select(Licence).order_by(Licence.created_at.desc())
        company_id = _tenant_filter(actor)
        if company_id is not None:
            query = query.where(Licence.company_id == company_id)
        rows = session.execute(query.limit(bounded).offset(max(offset, 0))).scalars().all()
        return {"licences": [_licence_document(row) for row in rows], "limit": bounded}


@router.post("/products", status_code=201)
def register_product(
    body: RegisterProductRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        product = registry_service.register_product(
            session,
            actor=actor,
            command=registry_service.RegisterProductCommand(
                company_id=body.company_id,
                sku=body.sku,
                brand=body.brand,
                product_category=body.product_category,
                pack_size=body.pack_size,
                unit_of_measure=body.unit_of_measure,
                intended_market=body.intended_market,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return _product_document(product)

    status, document = run_idempotent(
        runtime,
        scope="products.register",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/products/{product_id}/withdrawal")
def withdraw_product(
    product_id: uuid.UUID,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        product = registry_service.withdraw_product(
            session,
            actor=actor,
            product_id=product_id,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return _product_document(product)

    status, document = run_idempotent(
        runtime,
        scope="products.withdraw",
        key=key,
        actor=actor,
        payload={"product_id": str(product_id)},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/products")
def list_products(
    runtime: RuntimeDep,
    current: CurrentActor,
    limit: int = 50,
    offset: int = 0,
) -> JsonObject:
    actor = current.actor
    bounded = max(1, min(limit, 200))
    with runtime.session_factory() as session:
        query = select(Product).order_by(Product.created_at.desc())
        company_id = _tenant_filter(actor)
        if company_id is not None:
            query = query.where(Product.company_id == company_id)
        rows = session.execute(query.limit(bounded).offset(max(offset, 0))).scalars().all()
        return {"products": [_product_document(row) for row in rows], "limit": bounded}

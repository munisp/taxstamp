"""Import, free-zone, transit and duty-free consignment endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, authorize
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import (
    DeclareConsignmentRequest,
    LinkConsignmentStampsRequest,
    RejectConsignmentRequest,
    ReleaseConsignmentRequest,
)
from taxstamp.authz.actions import Action
from taxstamp.enums import CustomsRegime
from taxstamp.jsontypes import JsonObject
from taxstamp.services import customs as customs_service

router = APIRouter(prefix="/v1", tags=["customs"])


@router.post("/consignments", status_code=201)
def declare_consignment(
    body: DeclareConsignmentRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CUSTOMS_DECLARE)

    def work(session: Session) -> JsonObject:
        consignment = customs_service.declare_consignment(
            session,
            actor=actor,
            command=customs_service.DeclareConsignmentCommand(
                consignment_ref=body.consignment_ref,
                company_id=body.company_id,
                regime=CustomsRegime(body.regime),
                product_id=body.product_id,
                declared_quantity=body.declared_quantity,
                customs_declaration_reference=body.customs_declaration_reference,
                origin_country=body.origin_country,
                entry_facility_code=body.entry_facility_code,
                order_id=body.order_id,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return customs_service.consignment_document(session, consignment=consignment)

    status, document = run_idempotent(
        runtime,
        scope="consignments.declare",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/consignments/{consignment_ref}/stamps")
def link_consignment_stamps(
    consignment_ref: str,
    body: LinkConsignmentStampsRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CUSTOMS_DECLARE)

    def work(session: Session) -> JsonObject:
        consignment = customs_service.link_stamps(
            session,
            actor=actor,
            command=customs_service.LinkStampsCommand(
                consignment_ref=consignment_ref,
                serials=tuple(body.serials),
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return customs_service.consignment_document(session, consignment=consignment)

    status, document = run_idempotent(
        runtime,
        scope="consignments.link_stamps",
        key=key,
        actor=actor,
        payload={"consignment_ref": consignment_ref, **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/consignments/{consignment_ref}/release")
def release_consignment(
    consignment_ref: str,
    body: ReleaseConsignmentRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CUSTOMS_RELEASE)

    def work(session: Session) -> JsonObject:
        consignment = customs_service.release_consignment(
            session,
            actor=actor,
            command=customs_service.ReleaseCommand(
                consignment_ref=consignment_ref,
                customs_evidence_reference=body.customs_evidence_reference,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return customs_service.consignment_document(session, consignment=consignment)

    status, document = run_idempotent(
        runtime,
        scope="consignments.release",
        key=key,
        actor=actor,
        payload={"consignment_ref": consignment_ref, **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/consignments/{consignment_ref}/rejection")
def reject_consignment(
    consignment_ref: str,
    body: RejectConsignmentRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CUSTOMS_RELEASE)

    def work(session: Session) -> JsonObject:
        consignment = customs_service.reject_consignment(
            session,
            actor=actor,
            consignment_ref=consignment_ref,
            reason=body.reason,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return customs_service.consignment_document(session, consignment=consignment)

    status, document = run_idempotent(
        runtime,
        scope="consignments.reject",
        key=key,
        actor=actor,
        payload={"consignment_ref": consignment_ref, **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/consignments/{consignment_ref}")
def read_consignment(consignment_ref: str, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    with runtime.session_factory() as session:
        consignment = customs_service.consignment_for_read(
            session, actor=current.actor, consignment_ref=consignment_ref
        )
        return customs_service.consignment_document(session, consignment=consignment)

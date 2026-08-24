"""Enforcement case, seizure and chain-of-custody endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, authorize
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import (
    CaseDecisionRequest,
    CaseEvidenceRequest,
    CustodyTransferRequest,
    OpenCaseRequest,
    RecordSeizureRequest,
    SeizureSettlementRequest,
)
from taxstamp.authz.actions import Action
from taxstamp.enums import CaseStatus
from taxstamp.jsontypes import JsonObject
from taxstamp.services import enforcement as enforcement_service

router = APIRouter(prefix="/v1", tags=["enforcement"])


@router.post("/cases", status_code=201)
def open_case(
    body: OpenCaseRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CASE_OPEN)

    def work(session: Session) -> JsonObject:
        case = enforcement_service.open_case(
            session,
            actor=actor,
            command=enforcement_service.OpenCaseCommand(
                case_ref=body.case_ref,
                kind=body.kind,
                severity=body.severity,
                summary=body.summary,
                company_id=body.company_id,
                product_category=body.product_category,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return enforcement_service.case_document(
            session, case=case, audit_secret=runtime.settings.audit_chain_secret
        )

    status, document = run_idempotent(
        runtime,
        scope="cases.open",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/cases/{case_ref}/evidence", status_code=201)
def attach_evidence(
    case_ref: str,
    body: CaseEvidenceRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CASE_OPEN)

    def work(session: Session) -> JsonObject:
        enforcement_service.attach_evidence(
            session,
            actor=actor,
            command=enforcement_service.EvidenceCommand(
                case_ref=case_ref,
                kind=body.kind,
                reference=body.reference,
                detail=dict(body.detail),
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        case = enforcement_service.case_for_read(session, actor=actor, case_ref=case_ref)
        return enforcement_service.case_document(
            session, case=case, audit_secret=runtime.settings.audit_chain_secret
        )

    status, document = run_idempotent(
        runtime,
        scope="cases.evidence",
        key=key,
        actor=actor,
        payload={"case_ref": case_ref, **body.model_dump(mode="json")},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/cases/{case_ref}/decision")
def decide_case(
    case_ref: str,
    body: CaseDecisionRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CASE_DECIDE)

    def work(session: Session) -> JsonObject:
        case = enforcement_service.decide_case(
            session,
            actor=actor,
            command=enforcement_service.CaseDecisionCommand(
                case_ref=case_ref, status=body.status, reason=body.reason
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return enforcement_service.case_document(
            session, case=case, audit_secret=runtime.settings.audit_chain_secret
        )

    status, document = run_idempotent(
        runtime,
        scope="cases.decision",
        key=key,
        actor=actor,
        payload={"case_ref": case_ref, **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/cases/{case_ref}/seizures", status_code=201)
def record_seizure(
    case_ref: str,
    body: RecordSeizureRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.SEIZURE_RECORD)

    def work(session: Session) -> JsonObject:
        enforcement_service.record_seizure(
            session,
            actor=actor,
            command=enforcement_service.SeizureCommand(
                seizure_ref=body.seizure_ref,
                case_ref=case_ref,
                location=body.location,
                description=body.description,
                product_category=body.product_category,
                seized_quantity=body.seized_quantity,
                facility_code=body.facility_code,
                seized_at=body.seized_at,
                custodian=body.custodian,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        case = enforcement_service.case_for_read(session, actor=actor, case_ref=case_ref)
        return enforcement_service.case_document(
            session, case=case, audit_secret=runtime.settings.audit_chain_secret
        )

    status, document = run_idempotent(
        runtime,
        scope="seizures.record",
        key=key,
        actor=actor,
        payload={"case_ref": case_ref, **body.model_dump(mode="json")},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/seizures/{seizure_ref}/custody", status_code=201)
def transfer_custody(
    seizure_ref: str,
    body: CustodyTransferRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.CUSTODY_TRANSFER)

    def work(session: Session) -> JsonObject:
        transfer = enforcement_service.transfer_custody(
            session,
            actor=actor,
            command=enforcement_service.CustodyCommand(
                seizure_ref=seizure_ref,
                from_custodian=body.from_custodian,
                to_custodian=body.to_custodian,
                location=body.location,
                reason=body.reason,
                evidence_reference=body.evidence_reference,
                occurred_at=body.occurred_at,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return {
            "seizure_ref": seizure_ref,
            "sequence": transfer.sequence,
            "from_custodian": transfer.from_custodian,
            "to_custodian": transfer.to_custodian,
            "hash": transfer.hash,
        }

    status, document = run_idempotent(
        runtime,
        scope="seizures.custody",
        key=key,
        actor=actor,
        payload={"seizure_ref": seizure_ref, **body.model_dump(mode="json")},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/seizures/{seizure_ref}/settlement")
def settle_seizure(
    seizure_ref: str,
    body: SeizureSettlementRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor
    authorize(runtime, actor, Action.SEIZURE_RECORD)

    def work(session: Session) -> JsonObject:
        seizure = enforcement_service.settle_seizure(
            session,
            actor=actor,
            seizure_ref=seizure_ref,
            status=body.status,
            reason=body.reason,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return enforcement_service.seizure_snapshot(seizure)

    status, document = run_idempotent(
        runtime,
        scope="seizures.settlement",
        key=key,
        actor=actor,
        payload={"seizure_ref": seizure_ref, **body.model_dump(mode="json")},
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/cases/{case_ref}")
def read_case(case_ref: str, runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    with runtime.session_factory() as session:
        case = enforcement_service.case_for_read(session, actor=current.actor, case_ref=case_ref)
        return enforcement_service.case_document(
            session, case=case, audit_secret=runtime.settings.audit_chain_secret
        )


@router.get("/cases")
def list_cases(
    runtime: RuntimeDep,
    current: CurrentActor,
    company_id: uuid.UUID | None = None,
    status: CaseStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JsonObject:
    with runtime.session_factory() as session:
        return enforcement_service.list_cases(
            session,
            actor=current.actor,
            company_id=company_id,
            status=status,
            limit=limit,
            offset=offset,
        )

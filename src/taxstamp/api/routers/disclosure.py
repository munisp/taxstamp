"""Data exports, retention disclosure and transparency checkpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import PortabilityExportRequest, RegulatorExportRequest
from taxstamp.errors import ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.retention import retention_policy_document
from taxstamp.services import exports as export_service
from taxstamp.services import transparency as transparency_service

router = APIRouter(prefix="/v1", tags=["disclosure"])


def _delivery_state(base_url: str) -> JsonObject:
    """What the platform can honestly say about handing an export to a regulator."""
    if not base_url:
        return {
            "delivered": False,
            "reason": "no regulator repository endpoint is configured",
        }
    return {
        "delivered": False,
        "reason": "delivery is performed by the outbox relay, not by this request",
    }


@router.post("/exports/portability", status_code=201)
def create_portability_export(
    body: PortabilityExportRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        result = export_service.portability_export(
            session,
            actor=actor,
            export_ref=body.export_ref,
            company_id=body.company_id,
            now=runtime.clock.now(),
            export_secret=runtime.settings.export_signing_secret,
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return result.envelope()

    status, document = run_idempotent(
        runtime,
        scope="exports.portability",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.post("/exports/regulator", status_code=201)
def create_regulator_export(
    body: RegulatorExportRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        result = export_service.regulator_export(
            session,
            actor=actor,
            command=export_service.RegulatorExportCommand(
                export_ref=body.export_ref,
                company_id=body.company_id,
                occurred_from=body.occurred_from,
                occurred_to=body.occurred_to,
            ),
            now=runtime.clock.now(),
            export_secret=runtime.settings.export_signing_secret,
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return {
            **result.envelope(),
            "delivery": _delivery_state(runtime.settings.regulator_repository_base_url),
        }

    status, document = run_idempotent(
        runtime,
        scope="exports.regulator",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/retention-policy")
def read_retention_policy(runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    _ = current.actor
    return retention_policy_document(anchoring_configured=bool(runtime.settings.ledger_anchor_base_url))


@router.post("/transparency/checkpoints", status_code=201)
def publish_checkpoint(
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        checkpoint = transparency_service.publish_checkpoint(
            session,
            actor=actor,
            now=runtime.clock.now(),
            checkpoint_secret=runtime.settings.transparency_signing_secret,
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        return {
            **transparency_service.checkpoint_document(checkpoint),
            "external_anchor": {
                "anchored": False,
                "reason": (
                    "no ledger anchor endpoint is configured"
                    if not runtime.settings.ledger_anchor_base_url
                    else "anchoring is performed by the anchor worker, not by this request"
                ),
            },
        }

    status, document = run_idempotent(
        runtime,
        scope="transparency.checkpoint",
        key=key,
        actor=actor,
        payload={},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/transparency/checkpoints/latest")
def read_latest_checkpoint(runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    _ = current.actor
    with runtime.session_factory() as session:
        return transparency_service.checkpoint_document(transparency_service.latest_checkpoint(session))


@router.get("/transparency/checkpoints/{checkpoint_ref}/proof")
def read_inclusion_proof(
    checkpoint_ref: str,
    runtime: RuntimeDep,
    current: CurrentActor,
    audit_seq: int,
) -> JsonObject:
    _ = current.actor
    if audit_seq < 1:
        raise ValidationFailed("audit_seq must be positive")
    with runtime.session_factory() as session:
        proof = transparency_service.prove_inclusion(
            session, checkpoint_ref=checkpoint_ref, audit_seq=audit_seq
        )
        return {
            **proof.document(),
            "audit_seq": audit_seq,
            "verified": transparency_service.verify_proof(proof),
        }

"""Operational and assurance endpoints: health, readiness, capabilities, reconciliation."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select

from taxstamp.api.deps import CurrentActor, RuntimeDep, authorize
from taxstamp.audit import verify_audit_chain
from taxstamp.authz.actions import Action
from taxstamp.capabilities import capability_document
from taxstamp.db import transaction
from taxstamp.jsontypes import JsonObject
from taxstamp.models import OutboxMessage
from taxstamp.services.reconciliation import run_reconciliation

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz(runtime: RuntimeDep) -> JsonObject:
    return {
        "status": "alive",
        "service": runtime.settings.service_name,
        "revision": runtime.settings.revision,
    }


@router.get("/readyz")
def readyz(runtime: RuntimeDep) -> JSONResponse:
    database = runtime.check_database()
    cache = runtime.check_redis()
    ready = database and cache
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "database": database, "redis": cache},
    )


@router.get("/v1/capabilities")
def capabilities(runtime: RuntimeDep) -> JsonObject:
    return capability_document(runtime.settings)


@router.get("/metrics")
def metrics(runtime: RuntimeDep, current: CurrentActor) -> Response:
    authorize(runtime, current.actor, Action.OPS_METRICS_READ)
    with runtime.session_factory() as session:
        pending = session.execute(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.processed_at.is_(None), OutboxMessage.dead_lettered_at.is_(None))
        ).scalar_one()
        dead = session.execute(
            select(func.count()).select_from(OutboxMessage).where(OutboxMessage.dead_lettered_at.is_not(None))
        ).scalar_one()
    runtime.metrics["outbox_pending"].set(int(pending))
    runtime.metrics["outbox_dead"].set(int(dead))
    return Response(content=generate_latest(runtime.registry), media_type=CONTENT_TYPE_LATEST)


@router.post("/v1/ops/reconciliation", status_code=200)
def reconcile(runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    authorize(runtime, current.actor, Action.OPS_RECONCILE)
    with transaction(runtime.session_factory) as session:
        report = run_reconciliation(
            session,
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            export_secret=runtime.settings.export_signing_secret,
            transparency_secret=runtime.settings.transparency_signing_secret,
        )
        for kind, count in report.counts_by_kind().items():
            runtime.metrics["reconciliation_findings"].labels(kind=kind).set(count)
        return report.as_document()


@router.get("/v1/ops/audit-chain")
def audit_chain(runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    authorize(runtime, current.actor, Action.OPS_AUDIT_READ)
    with runtime.session_factory() as session:
        result = verify_audit_chain(session, secret=runtime.settings.audit_chain_secret)
        return {
            "intact": result.intact,
            "events_checked": result.events_checked,
            "first_bad_seq": result.first_bad_seq,
            "reason": result.reason,
        }

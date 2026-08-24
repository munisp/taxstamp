"""Offline revocation bundles and scan synchronisation endpoints."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from taxstamp.api.deps import CurrentActor, IdempotencyKey, RuntimeDep, authorize
from taxstamp.api.idempotent import run_idempotent
from taxstamp.api.schemas import OfflineSyncRequest
from taxstamp.authz.actions import Action
from taxstamp.db import transaction
from taxstamp.errors import NotFound
from taxstamp.jsontypes import JsonObject
from taxstamp.services import offline as offline_service

router = APIRouter(prefix="/v1/offline", tags=["offline"])


@router.post("/bundles", status_code=201)
def publish_bundle(
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        bundle = offline_service.build_bundle(
            session,
            actor=actor,
            now=runtime.clock.now(),
            signing_secret=runtime.settings.offline_signing_secret,
            filter_secret=runtime.settings.offline_filter_secret,
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
            ttl=dt.timedelta(hours=runtime.settings.offline_bundle_ttl_hours),
        )
        return offline_service.bundle_envelope(bundle)

    status, document = run_idempotent(
        runtime,
        scope="offline.bundle",
        key=key,
        actor=actor,
        payload={},
        status=201,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/bundles/latest")
def latest_bundle(runtime: RuntimeDep, current: CurrentActor) -> JsonObject:
    authorize(runtime, current.actor, Action.OFFLINE_BUNDLE_READ)
    with runtime.session_factory() as session:
        bundle = offline_service.latest_bundle(session)
        if bundle is None:
            raise NotFound("no offline revocation bundle has been published")
        return offline_service.bundle_envelope(bundle)


@router.post("/scans")
def sync_scans(
    body: OfflineSyncRequest,
    runtime: RuntimeDep,
    current: CurrentActor,
    key: IdempotencyKey,
) -> JSONResponse:
    actor = current.actor

    def work(session: Session) -> JsonObject:
        result = offline_service.sync_scans(
            session,
            actor=actor,
            command=offline_service.SyncCommand(
                device_id=body.device_id,
                batch_sequence=body.batch_sequence,
                scans=tuple(
                    offline_service.OfflineScan(
                        serial=scan.serial,
                        secure_code=scan.secure_code,
                        nonce=scan.nonce,
                        captured_at=scan.captured_at,
                        latitude_e7=scan.latitude_e7,
                        longitude_e7=scan.longitude_e7,
                    )
                    for scan in body.scans
                ),
            ),
            now=runtime.clock.now(),
            max_staleness=dt.timedelta(hours=runtime.settings.offline_sync_max_staleness_hours),
            secure_code_secret=runtime.settings.device_hmac_secret,
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
        for _, outcome in result.outcomes:
            runtime.metrics["verifications"].labels(outcome=outcome.value).inc()
        return offline_service.sync_result_document(result)

    status, document = run_idempotent(
        runtime,
        scope="offline.scans",
        key=key,
        actor=actor,
        payload=body.model_dump(mode="json"),
        status=200,
        work=work,
    )
    return JSONResponse(status_code=status, content=document)


@router.get("/scans/{device_id}")
def scan_history(
    device_id: str,
    runtime: RuntimeDep,
    current: CurrentActor,
    limit: int = Query(default=20, ge=1, le=100),
) -> JsonObject:
    authorize(runtime, current.actor, Action.OFFLINE_BUNDLE_READ)
    with transaction(runtime.session_factory) as session:
        return offline_service.batch_history(session, device_id=device_id, limit=limit)

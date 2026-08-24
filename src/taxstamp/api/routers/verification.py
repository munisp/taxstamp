"""Field verification endpoint.

Requests must be signed by the device credential and carry a single-use nonce. Replay
protection and rate limiting fail closed, so a Redis outage stops verification rather
than silently disabling the controls.
"""

from __future__ import annotations

import datetime as dt
import json

import structlog
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from taxstamp.api.deps import CurrentActor, RuntimeDep, rate_limit, utc
from taxstamp.api.schemas import VerifyRequest, parse_signed_body
from taxstamp.db import transaction
from taxstamp.enums import Role
from taxstamp.errors import Unauthenticated, ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.security import SignatureError, SignedRequest, verify_signed_request
from taxstamp.services import verification as verification_service

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["verification"])


@router.post("/verify")
async def verify(
    request: Request,
    runtime: RuntimeDep,
    current: CurrentActor,
    x_signature: str = Header(default=""),
    x_timestamp: str = Header(default=""),
) -> JSONResponse:
    actor = current.actor
    actor.require_role(Role.DEVICE, Role.OPERATOR, Role.ADMIN)
    rate_limit(runtime, actor, "verify", runtime.settings.rate_limit_verify)

    raw = await request.body()
    if len(raw) > 8 * 1024:
        raise ValidationFailed("payload is too large")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("body is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValidationFailed("body must be a JSON object")
    document: JsonObject = decoded

    try:
        timestamp = dt.datetime.fromtimestamp(int(x_timestamp), tz=dt.UTC)
    except (TypeError, ValueError) as exc:
        raise Unauthenticated("X-Timestamp header is missing or malformed") from exc
    # The signature covers exactly what the device sent, so server-side defaults can
    # never change the signed payload.
    try:
        verify_signed_request(
            SignedRequest(body=document, signature=x_signature, timestamp=timestamp),
            secret=runtime.settings.device_hmac_secret,
            now=runtime.clock.now(),
            max_skew_seconds=runtime.settings.signature_max_skew_seconds,
        )
    except SignatureError as exc:
        logger.info("verify_signature_rejected", reason=str(exc))
        raise Unauthenticated("request signature is invalid or stale") from exc

    body = parse_signed_body(VerifyRequest, document)

    if not runtime.replay_guard.claim(body.device_id, body.nonce):
        raise Unauthenticated("nonce has already been used")

    with transaction(runtime.session_factory) as session:
        result = verification_service.verify(
            session,
            actor=actor,
            request=verification_service.VerificationRequest(
                serial=body.serial,
                secure_code=body.secure_code,
                device_id=body.device_id,
                nonce=body.nonce,
                latitude_e7=body.latitude_e7,
                longitude_e7=body.longitude_e7,
            ),
            now=runtime.clock.now(),
            secure_code_secret=runtime.settings.device_hmac_secret,
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
    runtime.metrics["verifications"].labels(outcome=result.outcome.value).inc()
    return JSONResponse(
        status_code=200,
        content={
            "authentic": result.authentic,
            "outcome": result.outcome.value,
            "reason": result.reason,
            "serial": result.serial,
            "product_category": result.product_category,
            "expires_at": utc(result.expires_at) if result.expires_at else None,
        },
    )

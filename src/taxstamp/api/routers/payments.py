"""Settlement webhook.

The endpoint authenticates the *message*, not a user: the raw body is verified against
an HMAC signature and a bounded timestamp, and the delivery nonce is single-use.
"""

from __future__ import annotations

import datetime as dt
import json

import structlog
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from taxstamp.api.deps import RuntimeDep
from taxstamp.api.schemas import RemittanceRequest, parse_signed_body
from taxstamp.canonical import sha256_hex
from taxstamp.db import transaction
from taxstamp.errors import Unauthenticated, ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.security import SignatureError, SignedRequest, verify_signed_request
from taxstamp.services import payments as payment_service

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/payments", tags=["payments"])


@router.post("/remittances", status_code=202)
async def ingest_remittance(
    request: Request,
    runtime: RuntimeDep,
    x_signature: str = Header(default=""),
    x_timestamp: str = Header(default=""),
) -> JSONResponse:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise ValidationFailed("payload is too large")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("body is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValidationFailed("body must be a JSON object")

    try:
        timestamp = dt.datetime.fromtimestamp(int(x_timestamp), tz=dt.UTC)
    except (TypeError, ValueError) as exc:
        raise Unauthenticated("X-Timestamp header is missing or malformed") from exc

    body: JsonObject = decoded
    try:
        verify_signed_request(
            SignedRequest(body=body, signature=x_signature, timestamp=timestamp),
            secret=runtime.settings.payment_webhook_secret,
            now=runtime.clock.now(),
            max_skew_seconds=runtime.settings.signature_max_skew_seconds,
        )
    except SignatureError as exc:
        logger.warning("remittance_signature_rejected", reason=str(exc))
        raise Unauthenticated("remittance signature is invalid") from exc

    advice_model = parse_signed_body(RemittanceRequest, body)
    if not runtime.replay_guard.claim("payments", sha256_hex(raw)):
        return JSONResponse(
            status_code=202,
            content={"status": "duplicate_delivery", "external_reference": advice_model.external_reference},
        )

    with transaction(runtime.session_factory) as session:
        result = payment_service.ingest_remittance(
            session,
            advice=payment_service.RemittanceAdvice(
                external_reference=advice_model.external_reference,
                declared_reference=advice_model.payment_reference,
                amount_minor=advice_model.amount_minor,
                currency=advice_model.currency,
                value_date=advice_model.value_date,
                raw=body,
            ),
            now=runtime.clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
        )
    return JSONResponse(
        status_code=202,
        content={
            "receipt_id": str(result.receipt_id),
            "status": result.status.value,
            "order_id": str(result.order_id) if result.order_id else None,
            "duplicate": result.duplicate,
        },
    )

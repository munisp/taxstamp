"""Public stamp check.

This is the only unauthenticated write path in the platform, because a member of the
public cannot hold a credential. Three things keep it safe:

* the answer discloses only the product identity and whether the stamp is genuine, never
  the licence holder, the order, the batch or any movement;
* attempts are rate limited per caller address and recorded against a keyed fingerprint
  of it, so brute-forcing secure codes is bounded and visible without storing anything
  that identifies a person;
* the caller address is taken from the socket, not from a forwarded header, so a client
  cannot spoof its way past the limit. A deployment behind a proxy must therefore run
  with the proxy's real-IP handling configured at the ASGI layer.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from taxstamp.api.deps import RuntimeDep
from taxstamp.api.schemas import ConsumerVerifyRequest
from taxstamp.db import transaction
from taxstamp.services import verification as verification_service

router = APIRouter(prefix="/v1/public", tags=["consumer"])

UNKNOWN_CLIENT = "unknown"


@router.post("/verify")
def consumer_verify(body: ConsumerVerifyRequest, request: Request, runtime: RuntimeDep) -> JSONResponse:
    client_address = request.client.host if request.client is not None else UNKNOWN_CLIENT
    fingerprint = verification_service.client_fingerprint(
        client_address, secret=runtime.settings.consumer_fingerprint_secret
    )
    runtime.rate_limiter.enforce(fingerprint, "consumer_verify", runtime.settings.rate_limit_consumer_verify)
    with transaction(runtime.session_factory) as session:
        result = verification_service.verify_for_consumer(
            session,
            request=verification_service.ConsumerVerificationRequest(
                serial=body.serial,
                secure_code=body.secure_code,
                client_address=client_address,
                reported_state=body.reported_state,
            ),
            now=runtime.clock.now(),
            secure_code_secret=runtime.settings.device_hmac_secret,
            fingerprint_secret=runtime.settings.consumer_fingerprint_secret,
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
            "advice": result.advice,
            "serial": result.serial,
            "brand": result.brand,
            "product_category": result.product_category,
            "intended_market": result.intended_market,
        },
    )

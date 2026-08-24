"""Authentication, authorisation and per-request plumbing.

Authentication is deny-by-default: a route without an explicit principal dependency
cannot read or change tenant data, and every credential lookup is a constant-time
comparison against a stored keyed hash.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from taxstamp.canonical import canonical_hash
from taxstamp.enums import Role
from taxstamp.errors import Forbidden, Unauthenticated, ValidationFailed
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Credential
from taxstamp.runtime import Runtime
from taxstamp.security import hash_token
from taxstamp.services.context import Actor


def get_runtime(request: Request) -> Runtime:
    try:
        runtime = request.app.state.runtime
    except AttributeError as exc:  # pragma: no cover - app is always built with a runtime
        raise RuntimeError("application runtime is not initialised") from exc
    if not isinstance(runtime, Runtime):  # pragma: no cover - defensive
        raise TypeError("application runtime has an unexpected type")
    return runtime


def request_id(request: Request) -> str:
    value = request.headers.get("x-request-id")
    if value and len(value) <= 64 and value.isprintable():
        return value
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    actor: Actor
    credential_id: uuid.UUID


def _parse_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthenticated("a bearer credential is required")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise Unauthenticated("a bearer credential is required")
    return token


def authenticate(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedActor:
    runtime = get_runtime(request)
    token = _parse_bearer(authorization)
    token_hash = hash_token(token, secret=runtime.settings.api_token_secret)
    now = runtime.clock.now()
    with runtime.session_factory() as session:
        credential = session.execute(
            select(Credential).where(Credential.token_hash == token_hash)
        ).scalar_one_or_none()
        if credential is None:
            raise Unauthenticated("credential is not recognised")
        if credential.revoked_at is not None:
            raise Unauthenticated("credential has been revoked")
        if credential.expires_at is not None and credential.expires_at <= now:
            raise Unauthenticated("credential has expired")
        principal = credential.principal
        if not principal.active:
            raise Unauthenticated("principal is disabled")
        session.execute(update(Credential).where(Credential.id == credential.id).values(last_used_at=now))
        session.commit()
        actor = Actor(
            principal_id=principal.id,
            subject=principal.subject,
            role=Role(principal.role),
            company_id=principal.company_id,
            request_id=request_id(request),
        )
        return AuthenticatedActor(actor=actor, credential_id=credential.id)


CurrentActor = Annotated[AuthenticatedActor, Depends(authenticate)]
RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


def require_roles(*roles: Role) -> Callable[[AuthenticatedActor], Actor]:
    def dependency(current: CurrentActor) -> Actor:
        if current.actor.role not in roles:
            raise Forbidden(
                "this credential may not perform this action",
                detail={"required": ",".join(role.value for role in roles)},
            )
        return current.actor

    return dependency


def idempotency_key(
    idempotency_key_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key_header:
        raise ValidationFailed("the Idempotency-Key header is required for this operation")
    key = idempotency_key_header.strip()
    if not 8 <= len(key) <= 128 or not key.isprintable():
        raise ValidationFailed("Idempotency-Key must be 8-128 printable characters")
    return key


IdempotencyKey = Annotated[str, Depends(idempotency_key)]


def rate_limit(runtime: Runtime, actor: Actor, bucket: str, limit: int) -> None:
    runtime.rate_limiter.enforce(str(actor.principal_id), bucket, limit)


def request_fingerprint(payload: JsonObject) -> str:
    return canonical_hash(payload)


def session_scope(runtime: Runtime) -> Session:
    return runtime.session_factory()


def utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat()

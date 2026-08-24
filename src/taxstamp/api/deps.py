"""Authentication, authorisation and per-request plumbing.

Authentication is deny-by-default: a route without an explicit principal dependency
cannot read or change tenant data, and every credential lookup is a constant-time
comparison against a stored keyed hash.

Two credential families are accepted, and which one a request used is decided by the
shape of the bearer value, never by trying both stores:

* a JWS compact token is a federated session from the configured identity provider, and
  is accepted only when its verified subject is linked to an active principal;
* anything else is one of the platform's own opaque tokens, which is how devices and
  service accounts authenticate.

In both cases the *platform's* principal record supplies the role, the tenant and the
audit identity. The provider says who someone is; it never says what they may do.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from taxstamp.authz.actions import Action
from taxstamp.canonical import canonical_hash
from taxstamp.enums import Role
from taxstamp.errors import Unauthenticated, ValidationFailed
from taxstamp.identity.oidc import looks_like_jwt
from taxstamp.jsontypes import JsonObject
from taxstamp.models import Credential, Principal
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
    #: The platform credential used, or None for a federated session.
    credential_id: uuid.UUID | None
    #: True when the identity came from the external provider.
    federated: bool = False


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
    if looks_like_jwt(token):
        return _authenticate_federated(runtime, request, token)
    return _authenticate_platform(runtime, request, token)


def _authenticate_federated(
    runtime: Runtime,
    request: Request,
    token: str,
) -> AuthenticatedActor:
    """Accept a provider session for an explicitly linked, active principal."""
    metrics = runtime.metrics
    try:
        identity = runtime.oidc.verify(token)
    except Unauthenticated:
        metrics["oidc_authentications"].labels(outcome="rejected").inc()
        raise
    with runtime.session_factory() as session:
        principal = session.execute(
            select(Principal).where(Principal.oidc_subject == identity.subject)
        ).scalar_one_or_none()
        if principal is None:
            metrics["oidc_authentications"].labels(outcome="unlinked").inc()
            raise Unauthenticated("federated identity is not linked to a principal")
        if not principal.active:
            metrics["oidc_authentications"].labels(outcome="disabled").inc()
            raise Unauthenticated("principal is disabled")
        role = Role(principal.role)
        if role.value in runtime.settings.oidc_mfa_role_set and not identity.satisfies_multi_factor(
            required_methods=runtime.settings.oidc_mfa_method_set,
            required_acr=runtime.settings.oidc_mfa_acr,
        ):
            metrics["oidc_authentications"].labels(outcome="single_factor").inc()
            raise Unauthenticated(
                "this role requires a multi-factor session",
            )
        metrics["oidc_authentications"].labels(outcome="accepted").inc()
        actor = Actor(
            principal_id=principal.id,
            subject=principal.subject,
            role=role,
            company_id=principal.company_id,
            request_id=request_id(request),
        )
        return AuthenticatedActor(actor=actor, credential_id=None, federated=True)


def _authenticate_platform(
    runtime: Runtime,
    request: Request,
    token: str,
) -> AuthenticatedActor:
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


def authorize(runtime: Runtime, actor: Actor, action: Action) -> Actor:
    """Apply the authorisation table, and the policy engine when one is enforcing."""
    runtime.policy.authorize(
        action=action,
        role=actor.role,
        subject_id=actor.principal_id,
        company_id=actor.company_id,
    )
    return actor


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

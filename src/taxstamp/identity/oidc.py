"""OpenID Connect access-token verification for human principals.

Human staff (regulator, treasury, supervisory and manufacturer users) authenticate at an
external identity provider (Keycloak) and present its access token. Machines - field
devices, the payment provider, batch jobs - keep the platform's own HMAC-hashed bearer
credentials, because a device fleet has no interactive login and must not depend on the
identity provider being reachable to verify a stamp.

The verifier is deliberately strict:

* only asymmetric algorithms advertised by the provider's JWKS are accepted, so a token
  cannot be presented under ``none`` or under a symmetric algorithm keyed by material
  the provider publishes;
* issuer and audience must match exactly;
* expiry, not-before and issued-at are all checked, within a bounded clock skew;
* the signing key is selected by ``kid`` and, if unknown, the key set is refreshed at
  most once per interval, so an attacker cannot force unbounded fetches;
* a token proves who the person is, never what they may do. Authorisation always comes
  from the platform's own principal record, so revoking a principal locally is
  sufficient to stop access even while the provider keeps issuing tokens.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Final

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from taxstamp.errors import DependencyUnavailable, Unauthenticated

#: Asymmetric signature algorithms a provider may use. Symmetric algorithms are refused:
#: the platform holds no shared secret with the provider, so an ``HS*`` token could only
#: be verified with published key material.
ALLOWED_ALGORITHMS: Final[tuple[str, ...]] = ("RS256", "RS384", "RS512", "ES256", "ES384")

#: A JWT has three dot-separated segments. Opaque platform tokens never do, so this
#: distinguishes the two credential families without trying both stores on every request.
_JWT_SEGMENTS: Final[int] = 3


def looks_like_jwt(token: str) -> bool:
    """True when a bearer credential is shaped like a JWS compact serialisation."""
    parts = token.split(".")
    return len(parts) == _JWT_SEGMENTS and all(parts)


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    """A verified assertion about a human. Carries no platform privileges."""

    subject: str
    issuer: str
    #: Authentication methods the provider asserts (``amr``), lower-cased.
    methods: frozenset[str]
    #: Authentication context class the provider asserts (``acr``), or None.
    context_class: str | None
    expires_at: dt.datetime

    def satisfies_multi_factor(self, *, required_methods: frozenset[str], required_acr: str) -> bool:
        """Whether the provider asserted a strong authentication for this session.

        Either an accepted ``amr`` value or the required ``acr`` value is sufficient:
        providers differ in which they populate, and demanding both would deny sessions
        that were in fact multi-factor.
        """
        if required_methods & self.methods:
            return True
        return bool(required_acr) and self.context_class == required_acr


@dataclass(frozen=True, slots=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str
    leeway_seconds: int
    jwks_cache_seconds: int
    required_methods: frozenset[str]
    required_acr: str

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.audience and self.jwks_url)


@dataclass(slots=True)
class OidcVerifier:
    """Verifies provider access tokens against a cached, refreshable key set."""

    config: OidcConfig
    _client: PyJWKClient | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _keys(self) -> PyJWKClient:
        with self._lock:
            if self._client is None:
                self._client = PyJWKClient(
                    self.config.jwks_url,
                    cache_keys=True,
                    lifespan=self.config.jwks_cache_seconds,
                )
            return self._client

    def verify(self, token: str) -> OidcIdentity:
        """Verify a token's signature and registered claims.

        Raises:
            Unauthenticated: the token is absent, malformed, expired or not trusted.
            DependencyUnavailable: the provider's key set could not be fetched, so the
                token can be neither trusted nor safely refused as forged.
        """
        if not self.config.configured:
            raise Unauthenticated("federated identity is not configured")
        try:
            signing_key = self._keys().get_signing_key_from_jwt(token).key
        except (PyJWKClientConnectionError, httpx.HTTPError, OSError) as exc:
            # The key set could not be fetched, so this token can be neither trusted nor
            # honestly called forged. Callers must fail closed on the outage instead.
            raise DependencyUnavailable("identity provider key set is unavailable") from exc
        except PyJWKClientError as exc:
            # The key set was read and does not contain this token's key. That is a
            # property of the token, not of the provider, so it is an authentication
            # failure. Note the ordering: the connection error above is a subclass.
            raise Unauthenticated("token signing key is not trusted") from exc
        try:
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=list(ALLOWED_ALGORITHMS),
                issuer=self.config.issuer,
                audience=self.config.audience,
                leeway=self.config.leeway_seconds,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "verify_signature": True,
                },
            )
        except jwt.InvalidTokenError as exc:
            raise Unauthenticated(f"token is not acceptable: {exc}") from exc
        return _identity(claims)


def _identity(claims: dict[str, object]) -> OidcIdentity:
    subject = claims.get("sub")
    issuer = claims.get("iss")
    expires = claims.get("exp")
    if not isinstance(subject, str) or not subject:
        raise Unauthenticated("token has no usable subject")
    if not isinstance(issuer, str) or not issuer:
        raise Unauthenticated("token has no usable issuer")
    if not isinstance(expires, int):
        raise Unauthenticated("token has no usable expiry")
    context_class = claims.get("acr")
    return OidcIdentity(
        subject=subject,
        issuer=issuer,
        methods=_methods(claims.get("amr")),
        context_class=context_class if isinstance(context_class, str) else None,
        expires_at=dt.datetime.fromtimestamp(expires, tz=dt.UTC),
    )


def _methods(raw: object) -> frozenset[str]:
    if isinstance(raw, str):
        return frozenset({raw.lower()})
    if isinstance(raw, list):
        return frozenset(item.lower() for item in raw if isinstance(item, str))
    return frozenset()

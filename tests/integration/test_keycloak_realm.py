"""The realm this repository ships, exercised against a real Keycloak.

``tests/unit/test_oidc.py`` proves the verifier against a provider sandbox this repository
controls. What it cannot prove is that the shipped realm imports, that the issuer serves
the discovery document and key set the verifier expects to find, and that the realm
refuses the flows the platform relies on being refused. That needs the real product, so
this module is skipped unless one is reachable::

    docker compose --profile edge up -d keycloak
    TAXSTAMP_OIDC_ISSUER=http://127.0.0.1:8081/realms/taxstamp \\
        pytest tests/integration/test_keycloak_realm.py
"""

from __future__ import annotations

import datetime as dt
import os

import httpx
import pytest

from taxstamp.errors import Unauthenticated
from taxstamp.identity.oidc import ALLOWED_ALGORITHMS, OidcConfig, OidcVerifier
from tests.support.identity_server import IdentitySandbox

ISSUER = os.environ.get("TAXSTAMP_OIDC_ISSUER", "").rstrip("/")
AUDIENCE = "taxstamp-api"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ISSUER, reason="no identity provider configured"),
]


@pytest.fixture(scope="module")
def discovery() -> dict[str, object]:
    with httpx.Client(timeout=10.0) as client:
        response = client.get(f"{ISSUER}/.well-known/openid-configuration")
        response.raise_for_status()
        document = response.json()
    assert isinstance(document, dict)
    return document


def test_the_realm_imported_and_advertises_itself_correctly(discovery: dict[str, object]) -> None:
    assert discovery["issuer"] == ISSUER


def test_the_realm_supports_authorisation_code_with_pkce(discovery: dict[str, object]) -> None:
    """A public browser client without PKCE is an interceptable authorisation code."""
    grants = discovery["grant_types_supported"]
    methods = discovery["code_challenge_methods_supported"]
    assert isinstance(grants, list)
    assert isinstance(methods, list)
    assert "authorization_code" in grants
    assert "S256" in methods


def test_the_realm_signs_with_an_algorithm_the_platform_accepts(
    discovery: dict[str, object],
) -> None:
    """A realm signing only with algorithms the verifier refuses is a silent outage."""
    advertised = discovery["id_token_signing_alg_values_supported"]
    assert isinstance(advertised, list)
    assert set(ALLOWED_ALGORITHMS) & set(advertised)


def test_the_published_key_set_is_usable(discovery: dict[str, object]) -> None:
    jwks_url = discovery["jwks_uri"]
    assert isinstance(jwks_url, str)
    with httpx.Client(timeout=10.0) as client:
        keys = client.get(jwks_url).json()["keys"]
    assert keys, "the realm published no signing keys"
    assert all(key["kty"] in {"RSA", "EC"} for key in keys)


def test_a_token_from_another_issuer_is_refused(discovery: dict[str, object]) -> None:
    """The realm's key set must be the only key set that opens this API."""
    jwks_url = discovery["jwks_uri"]
    assert isinstance(jwks_url, str)
    verifier = OidcVerifier(
        OidcConfig(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url=jwks_url,
            leeway_seconds=30,
            jwks_cache_seconds=600,
            required_methods=frozenset({"otp"}),
            required_acr="mfa",
        )
    )
    now = dt.datetime.now(dt.UTC)
    impostor = IdentitySandbox()
    impostor.start()
    try:
        forged = impostor.token(
            subject="attacker",
            audience=AUDIENCE,
            issuer=ISSUER,
            issued_at=now,
            expires_at=now + dt.timedelta(minutes=5),
        )
    finally:
        impostor.stop()
    with pytest.raises(Unauthenticated):
        verifier.verify(forged)

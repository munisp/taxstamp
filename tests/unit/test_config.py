"""Configuration refuses unsafe deployments at startup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from taxstamp.config import Environment, Settings

pytestmark = pytest.mark.unit

BASE = {
    "database_url": "postgresql+psycopg://user:pw@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "api_token_secret": "a" * 48,
    "device_hmac_secret": "b" * 48,
    "payment_webhook_secret": "c" * 48,
    "audit_chain_secret": "d" * 48,
    "export_signing_secret": "e" * 48,
    "transparency_signing_secret": "f" * 48,
    "offline_signing_secret": "g" * 48,
    "offline_filter_secret": "i" * 48,
    "consumer_fingerprint_secret": "h" * 48,
}


def test_short_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "api_token_secret": "short"})


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "database_url": "sqlite:///local.db"})


def test_production_rejects_placeholder_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION, "api_token_secret": "change_me_" + "x" * 40})


def test_production_rejects_duplicate_secrets() -> None:
    duplicate = {
        key: "e" * 48
        for key in (
            "api_token_secret",
            "device_hmac_secret",
            "payment_webhook_secret",
            "audit_chain_secret",
            "export_signing_secret",
            "transparency_signing_secret",
            "offline_signing_secret",
            "offline_filter_secret",
            "consumer_fingerprint_secret",
        )
    }
    with pytest.raises(ValidationError):
        Settings(**{**BASE, **duplicate, "env": Environment.PRODUCTION})


def test_production_rejects_disabled_tls() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION, "require_tls": False})


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION, "cors_allowed_origins": "*"})


def test_production_rejects_unencrypted_database() -> None:
    with pytest.raises(ValidationError):
        Settings(
            **{
                **BASE,
                "env": Environment.PRODUCTION,
                "database_url": BASE["database_url"] + "?sslmode=disable",
            }
        )


def test_unknown_setting_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "unexpected_option": "1"})


def test_federation_is_off_by_default() -> None:
    """Absent configuration must mean "refuse provider tokens", not "trust them"."""
    settings = Settings(**BASE)
    assert settings.oidc_issuer == ""
    assert settings.effective_oidc_jwks_url == ""
    assert settings.authz_external_mode == "disabled"


def test_jwks_url_defaults_to_the_issuer_discovery_path() -> None:
    settings = Settings(**{**BASE, "oidc_issuer": "https://sso.example/realms/taxstamp/"})
    assert settings.effective_oidc_jwks_url == (
        "https://sso.example/realms/taxstamp/protocol/openid-connect/certs"
    )


def test_audience_without_an_issuer_is_rejected() -> None:
    """A half-configured verifier would accept audiences from any issuer."""
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "oidc_audience": "taxstamp-api"})


@pytest.mark.parametrize("mode", ["shadow", "enforcing"])
def test_external_authorisation_requires_an_engine(mode: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "authz_external_mode": mode})


def test_unknown_authorisation_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "authz_external_mode": "advisory"})


def test_identity_endpoints_must_be_urls() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "oidc_issuer": "sso.example"})


@pytest.mark.parametrize(
    "override",
    [
        {"oidc_issuer": "http://sso.internal/realms/taxstamp"},
        {
            "permify_base_url": "http://permify.internal:3476",
            "authz_external_mode": "enforcing",
        },
    ],
)
def test_production_rejects_plaintext_identity_endpoints(override: dict[str, str]) -> None:
    """Tokens and authorisation verdicts must not cross a network in the clear."""
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION, **override})


def test_multi_factor_configuration_is_parsed_into_sets() -> None:
    settings = Settings(
        **{
            **BASE,
            "oidc_mfa_methods": "OTP, hwk ,",
            "oidc_mfa_required_roles": "Admin,treasury",
        }
    )
    assert settings.oidc_mfa_method_set == {"otp", "hwk"}
    assert settings.oidc_mfa_role_set == {"admin", "treasury"}

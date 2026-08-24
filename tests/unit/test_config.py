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

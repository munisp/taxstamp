"""Configuration refuses unsafe deployments at startup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from taxstamp.config import Environment, KmsProvider, Settings

pytestmark = pytest.mark.unit

BASE = {
    "database_url": "postgresql+psycopg://user:pw@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "api_token_secret": "a" * 48,
    "device_hmac_secret": "b" * 48,
    "payment_webhook_secret": "c" * 48,
    "audit_chain_secret": "d" * 48,
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
        for key in ("api_token_secret", "device_hmac_secret", "payment_webhook_secret", "audit_chain_secret")
    }
    with pytest.raises(ValidationError):
        Settings(**{**BASE, **duplicate, "env": Environment.PRODUCTION})


def test_production_rejects_disabled_tls() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION, "require_tls": False})


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION, "cors_allowed_origins": "*"})


def test_production_requires_non_wildcard_trusted_hosts() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION})
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.PRODUCTION, "trusted_hosts": "*"})


def test_production_requires_proxy_cidrs_when_proxy_headers_are_trusted() -> None:
    with pytest.raises(ValidationError):
        Settings(
            **{
                **BASE,
                "env": Environment.PRODUCTION,
                "trusted_hosts": "api.taxstamp.ng",
                "trust_proxy_headers": True,
            }
        )
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "trusted_proxy_cidrs": "not-a-cidr"})


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


def test_integration_endpoint_must_be_http_url() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "keycloak_issuer_url": "keycloak.internal/realms/taxstamp"})


def test_production_rejects_unencrypted_integration_endpoint() -> None:
    with pytest.raises(ValidationError):
        Settings(
            **{
                **BASE,
                "env": Environment.PRODUCTION,
                "keycloak_issuer_url": "http://keycloak.internal/realms/taxstamp",
            }
        )


def test_production_requires_secure_kafka_protocol_when_configured() -> None:
    with pytest.raises(ValidationError):
        Settings(
            **{
                **BASE,
                "env": Environment.PRODUCTION,
                "kafka_bootstrap_servers": "kafka-1.internal:9092",
                "kafka_security_protocol": "PLAINTEXT",
            }
        )


def test_staging_requires_storage_encryption_contract() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "env": Environment.STAGING})


def test_production_accepts_complete_kms_hsm_storage_contract() -> None:
    settings = Settings(
        **{
            **BASE,
            "env": Environment.PRODUCTION,
            "storage_encryption_required": True,
            "kms_provider": KmsProvider.AWS_KMS,
            "kms_key_reference": (
                "arn:aws:kms:af-south-1:123456789012:key/" "01234567-89ab-cdef-0123-456789abcdef"
            ),
            "kms_hsm_backed": True,
            "storage_encryption_evidence_uri": "https://evidence.taxstamp.ng/changes/CHG-123",
            "postgres_storage_encryption_attested": True,
            "redis_storage_encryption_attested": True,
            "trusted_hosts": "api.taxstamp.ng",
            "trust_proxy_headers": True,
            "trusted_proxy_cidrs": "10.10.0.0/16",
        }
    )
    assert settings.kms_provider is KmsProvider.AWS_KMS

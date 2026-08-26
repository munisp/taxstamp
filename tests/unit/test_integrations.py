"""Integration readiness remains explicit until external proof exists."""

from __future__ import annotations

import pytest

from taxstamp.config import Settings
from taxstamp.integrations import IntegrationReadiness, integration_manifest

pytestmark = pytest.mark.unit

BASE = {
    "database_url": "postgresql+psycopg://user:pw@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "api_token_secret": "a" * 48,
    "device_hmac_secret": "b" * 48,
    "payment_webhook_secret": "c" * 48,
    "audit_chain_secret": "d" * 48,
}


def test_manifest_does_not_claim_unconfigured_services_are_connected() -> None:
    manifest = {entry.name: entry for entry in integration_manifest(Settings(**BASE))}
    assert manifest["kafka"].readiness is IntegrationReadiness.UNCONFIGURED
    assert manifest["tigerbeetle"].endpoint_configured is False


def test_manifest_marks_a_configured_endpoint_as_not_verified() -> None:
    manifest = {
        entry.name: entry
        for entry in integration_manifest(
            Settings(**{**BASE, "permify_base_url": "https://authz.example.ng"})
        )
    }
    assert manifest["permify"].readiness is IntegrationReadiness.CONFIGURED_NOT_VERIFIED
    assert "negative authorization proof" in manifest["permify"].required_evidence

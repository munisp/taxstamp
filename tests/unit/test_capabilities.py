"""Integration readiness is disclosed honestly rather than treated as live integration."""

from __future__ import annotations

import pytest

from taxstamp.capabilities import CapabilityState, capability_report
from taxstamp.config import Settings

pytestmark = pytest.mark.unit

BASE = {
    "database_url": "postgresql+psycopg://user:pw@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "api_token_secret": "a" * 48,
    "device_hmac_secret": "b" * 48,
    "payment_webhook_secret": "c" * 48,
    "audit_chain_secret": "d" * 48,
}


def test_unconfigured_target_integrations_are_not_reported_as_implemented() -> None:
    states = {capability.name: capability.state for capability in capability_report(Settings(**BASE))}
    assert states["tigerbeetle_subledger"] is CapabilityState.REQUIRES_CONFIGURATION
    assert states["keycloak_identity"] is CapabilityState.REQUIRES_CONFIGURATION
    assert states["kafka_event_transport"] is CapabilityState.REQUIRES_CONFIGURATION


def test_configured_target_integration_is_explicitly_not_verified() -> None:
    settings = Settings(**{**BASE, "keycloak_issuer_url": "https://id.example.ng/realms/taxstamp"})
    states = {capability.name: capability.state for capability in capability_report(settings)}
    assert states["keycloak_identity"] is CapabilityState.CONFIGURED_NOT_VERIFIED

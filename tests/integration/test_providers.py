"""External registry behaviour over a real socket."""

from __future__ import annotations

import pytest

from taxstamp.errors import CapabilityNotConfigured, DependencyUnavailable
from taxstamp.providers.base import ProviderClient, ProviderConfig
from taxstamp.providers.compliance import ComplianceService, Registry
from taxstamp.runtime import Runtime
from tests.support.registry_server import RegistrySandbox

pytestmark = pytest.mark.integration


def _client(base_url: str) -> ProviderClient:
    return ProviderClient(ProviderConfig(name="Test", base_url=base_url, api_key="k", timeout_seconds=2.0))


def test_unconfigured_provider_refuses_instead_of_succeeding() -> None:
    service = ComplianceService({registry: _client("") for registry in Registry})
    with pytest.raises(CapabilityNotConfigured):
        service.check(tin="TIN-1", product_category="alcohol", quantity=10)


def test_server_error_is_not_treated_as_compliant(runtime: Runtime, registry: RegistrySandbox) -> None:
    registry.script.status = 500
    with pytest.raises(DependencyUnavailable):
        runtime.compliance.check(tin="TIN-1", product_category="alcohol", quantity=10)


def test_malformed_payload_is_rejected(runtime: Runtime, registry: RegistrySandbox) -> None:
    registry.script.raw_body = "not json"
    with pytest.raises(DependencyUnavailable):
        runtime.compliance.check(tin="TIN-1", product_category="alcohol", quantity=10)


def test_missing_decision_field_is_rejected(runtime: Runtime, registry: RegistrySandbox) -> None:
    registry.script.raw_body = None
    registry.script.body = {"reference": "R-1", "checked_at": "2026-01-01T00:00:00+00:00"}
    with pytest.raises(ValueError, match="compliant"):
        runtime.compliance.check(tin="TIN-1", product_category="alcohol", quantity=10)


def test_timeout_is_surfaced_as_unavailable(registry: RegistrySandbox) -> None:
    registry.compliant()
    registry.script.delay_seconds = 1.0
    client = ProviderClient(
        ProviderConfig(name="Slow", base_url=registry.base_url, api_key="", timeout_seconds=0.2)
    )
    service = ComplianceService({registry_name: client for registry_name in Registry})
    try:
        with pytest.raises(DependencyUnavailable):
            service.check(tin="TIN-1", product_category="alcohol", quantity=1)
    finally:
        registry.script.delay_seconds = 0.0


def test_non_compliant_response_is_reported(runtime: Runtime, registry: RegistrySandbox) -> None:
    registry.non_compliant()
    outcome = runtime.compliance.check(tin="TIN-1", product_category="alcohol", quantity=10)
    assert not outcome.compliant
    assert all(not decision.compliant for decision in outcome.decisions)

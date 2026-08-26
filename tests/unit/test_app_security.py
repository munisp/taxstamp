"""Application-factory security defaults stay restrictive outside development."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest

from taxstamp.api.app import create_app
from taxstamp.api.middleware import RequestContextMiddleware
from taxstamp.config import Environment, Settings
from taxstamp.observability import build_metrics

pytestmark = pytest.mark.unit


def _settings(env: Environment) -> Settings:
    common = {
        "env": env,
        "database_url": "postgresql+psycopg://user:pw@localhost:5432/taxstamp",
        "redis_url": "redis://localhost:6379/0",
        "api_token_secret": "a" * 48,
        "device_hmac_secret": "b" * 48,
        "payment_webhook_secret": "c" * 48,
        "audit_chain_secret": "d" * 48,
    }
    if env is Environment.DEVELOPMENT:
        return Settings(**common)
    return Settings(
        **common,
        trusted_hosts="api.taxstamp.ng",
        storage_encryption_required=True,
        kms_provider="aws_kms",
        kms_key_reference="arn:aws:kms:af-south-1:123456789012:key/01234567-89ab-cdef-0123-456789abcdef",
        kms_hsm_backed=True,
        storage_encryption_evidence_uri="https://evidence.taxstamp.ng/changes/CHG-123",
        postgres_storage_encryption_attested=True,
        redis_storage_encryption_attested=True,
    )


def test_docs_are_available_only_in_development() -> None:
    development = create_app(_settings(Environment.DEVELOPMENT))
    staging = create_app(_settings(Environment.STAGING))
    production = create_app(_settings(Environment.PRODUCTION))

    assert development.docs_url == "/docs"
    assert staging.docs_url is None
    assert production.docs_url is None


def _middleware_client(
    *,
    require_tls: bool,
    trust_proxy_headers: bool,
    trusted_proxy_cidrs: list[str] | None = None,
    client_host: str = "testclient",
) -> tuple[TestClient, CollectorRegistry]:
    registry = CollectorRegistry()
    app = FastAPI()
    app.add_middleware(
        RequestContextMiddleware,
        metrics=build_metrics(registry),
        require_tls=require_tls,
        trust_proxy_headers=trust_proxy_headers,
        trusted_proxy_cidrs=trusted_proxy_cidrs or [],
    )

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app, client=(client_host, 50000)), registry


def test_untrusted_forwarded_proto_cannot_bypass_tls() -> None:
    client, _ = _middleware_client(require_tls=True, trust_proxy_headers=False)
    response = client.get("/probe", headers={"x-forwarded-proto": "https"})
    assert response.status_code == 400


def test_trusted_proxy_headers_can_signal_tls_termination() -> None:
    client, _ = _middleware_client(
        require_tls=True,
        trust_proxy_headers=True,
        trusted_proxy_cidrs=["10.20.0.0/16"],
        client_host="10.20.1.5",
    )
    response = client.get("/probe", headers={"x-forwarded-proto": "https"})
    assert response.status_code == 200


def test_unlisted_client_cannot_forge_a_trusted_proxy_header() -> None:
    client, _ = _middleware_client(
        require_tls=True,
        trust_proxy_headers=True,
        trusted_proxy_cidrs=["10.20.0.0/16"],
        client_host="10.30.1.5",
    )
    response = client.get("/probe", headers={"x-forwarded-proto": "https"})
    assert response.status_code == 400


def test_unmatched_paths_use_a_bounded_metric_label() -> None:
    client, registry = _middleware_client(require_tls=False, trust_proxy_headers=False)
    assert client.get("/probe").status_code == 200
    assert client.get("/attacker-generated-path-one").status_code == 404
    assert client.get("/attacker-generated-path-two").status_code == 404
    metrics = generate_latest(registry).decode("utf-8")
    assert 'route="/probe"' in metrics
    assert 'route="<unmatched>"' in metrics
    assert "attacker-generated-path-one" not in metrics
    assert "attacker-generated-path-two" not in metrics

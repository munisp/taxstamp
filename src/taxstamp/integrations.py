"""Explicit, non-secret declarations for integration-ready platform boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from taxstamp.config import KmsProvider, Settings


class IntegrationReadiness(StrEnum):
    UNCONFIGURED = "unconfigured"
    CONFIGURED_NOT_VERIFIED = "configured_not_verified"


@dataclass(frozen=True, slots=True)
class IntegrationManifest:
    name: str
    readiness: IntegrationReadiness
    endpoint_configured: bool
    purpose: str
    required_evidence: str


def integration_manifest(settings: Settings) -> tuple[IntegrationManifest, ...]:
    """Return an honest non-secret deployment manifest; no remote call occurs here."""

    boundaries = (
        (
            "kms_hsm",
            settings.kms_provider is not KmsProvider.UNCONFIGURED and bool(settings.kms_key_reference),
            "External key-management and hardware-security boundary for encrypted storage",
            "KMS/HSM policy, key rotation, encrypted storage/backup evidence and independent access review",
        ),
        (
            "tigerbeetle",
            bool(settings.tigerbeetle_addresses),
            "Transaction-plane double-entry subledger",
            "Account mapping, reconciliation, cluster proof and acceptance",
        ),
        (
            "mojaloop",
            bool(settings.mojaloop_base_url),
            "Payment-switch settlement adapter",
            "Scheme onboarding, signed credentials, sandbox conformance and settlement proof",
        ),
        (
            "kafka",
            bool(settings.kafka_bootstrap_servers),
            "Outbox event transport",
            "Topic ACLs, schema compatibility, delivery and replay evidence",
        ),
        (
            "apisix",
            bool(settings.apisix_admin_url),
            "Public gateway policy",
            "Declarative route review, TLS policy and gateway integration tests",
        ),
        (
            "keycloak",
            bool(settings.keycloak_issuer_url),
            "OIDC identity and client policy",
            "Realm export, PKCE policy, claims mapping and rotation evidence",
        ),
        (
            "openappsec",
            bool(settings.openappsec_management_url),
            "Ingress threat protection",
            "Detection baseline, enforced policy and attack-test evidence",
        ),
        (
            "permify",
            bool(settings.permify_base_url),
            "Fine-grained authorization decisions",
            "Model publication, tuple migration and negative authorization proof",
        ),
        (
            "opensearch",
            bool(settings.opensearch_url),
            "Read-only operational search projection",
            "Projection replay, retention and access-control evidence",
        ),
        (
            "fluvio",
            bool(settings.fluvio_cluster_url),
            "Optional edge streaming",
            "Use-case approval and duplicate-bus controls",
        ),
        (
            "dapr",
            bool(settings.dapr_http_url),
            "Cross-language service runtime",
            "Component manifests, mTLS and resiliency evidence",
        ),
        (
            "lakehouse",
            bool(settings.lakehouse_catalog_url),
            "Governed analytics projection",
            "Lineage, immutable-retention policy and reporting validation",
        ),
    )
    return tuple(
        IntegrationManifest(
            name=name,
            readiness=(
                IntegrationReadiness.CONFIGURED_NOT_VERIFIED
                if configured
                else IntegrationReadiness.UNCONFIGURED
            ),
            endpoint_configured=configured,
            purpose=purpose,
            required_evidence=evidence,
        )
        for name, configured, purpose, evidence in boundaries
    )

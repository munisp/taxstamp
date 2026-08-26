"""Capability registry: the platform's honest statement of what it does.

Every capability is either implemented in this codebase, dependent on an external
system of record that must be configured, or deliberately not implemented. Requests
against a capability that is not available are refused with an explicit error; nothing
returns a fabricated success.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from taxstamp.config import Settings
from taxstamp.jsontypes import JsonObject


class CapabilityState(StrEnum):
    IMPLEMENTED = "implemented"
    REQUIRES_CONFIGURATION = "requires_configuration"
    CONFIGURED_NOT_VERIFIED = "configured_not_verified"
    NOT_IMPLEMENTED = "not_implemented"


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    state: CapabilityState
    detail: str


_STATIC: tuple[Capability, ...] = (
    Capability("order_intake", CapabilityState.IMPLEMENTED, "Server-priced orders with audit trail"),
    Capability("maker_checker_approval", CapabilityState.IMPLEMENTED, "Segregated approval levels"),
    Capability(
        "payment_settlement_ingestion",
        CapabilityState.IMPLEMENTED,
        "Signed remittance ingestion, exact-amount matching, double-entry posting",
    ),
    Capability("stamp_issuance", CapabilityState.IMPLEMENTED, "Resumable serial allocation and issuance"),
    Capability("acceptance_sampling", CapabilityState.IMPLEMENTED, "ANSI/ASQ Z1.4-style batch inspection"),
    Capability("stamp_activation", CapabilityState.IMPLEMENTED, "Owner-scoped, idempotent activation"),
    Capability(
        "field_verification",
        CapabilityState.IMPLEMENTED,
        "Device-signed, replay-protected, deterministic serial and secure-code verification",
    ),
    Capability("audit_trail", CapabilityState.IMPLEMENTED, "Keyed hash-chained append-only audit log"),
    Capability(
        "reconciliation", CapabilityState.IMPLEMENTED, "Funds, issuance, outbox and audit reconciliation"
    ),
    Capability(
        "image_authenticity_ml",
        CapabilityState.NOT_IMPLEMENTED,
        "No trained, validated model is shipped; image-based authenticity scoring is refused "
        "rather than estimated by an unvalidated model",
    ),
    Capability(
        "printer_production_control",
        CapabilityState.NOT_IMPLEMENTED,
        "No vendor press integration is shipped; production control requires vendor "
        "credentials, protocol documentation and site acceptance testing",
    ),
    Capability(
        "holographic_feature_verification",
        CapabilityState.NOT_IMPLEMENTED,
        "Requires certified optical hardware; no software-only substitute is offered",
    ),
    Capability(
        "offline_verification_sync",
        CapabilityState.NOT_IMPLEMENTED,
        "Offline capture and reconciliation of field verifications is not implemented",
    ),
)


def capability_report(settings: Settings) -> list[Capability]:
    def integration(
        name: str,
        configured: bool,
        configured_detail: str,
        missing_detail: str,
    ) -> Capability:
        return Capability(
            name,
            CapabilityState.CONFIGURED_NOT_VERIFIED if configured else CapabilityState.REQUIRES_CONFIGURATION,
            configured_detail if configured else missing_detail,
        )

    dynamic = [
        Capability(
            "regulatory_compliance_check",
            CapabilityState.IMPLEMENTED if settings.firs_base_url else CapabilityState.REQUIRES_CONFIGURATION,
            "Live registry checks; unconfigured registries cause order submission to be refused",
        ),
        Capability(
            "batch_integrity_anchoring",
            CapabilityState.IMPLEMENTED
            if settings.ledger_anchor_base_url
            else CapabilityState.REQUIRES_CONFIGURATION,
            "Merkle root computed locally; external notarisation requires an anchoring service",
        ),
        integration(
            "tigerbeetle_subledger",
            bool(settings.tigerbeetle_addresses),
            "Addresses configured; account mapping, transfer reconciliation, credentials and "
            "production acceptance remain required",
            "TigerBeetle transaction-plane addresses are not configured",
        ),
        integration(
            "mojaloop_settlement_adapter",
            bool(settings.mojaloop_base_url),
            "Endpoint configured; scheme onboarding, signed credentials, sandbox conformance "
            "and settlement reconciliation remain required",
            "Mojaloop settlement endpoint is not configured",
        ),
        integration(
            "kafka_event_transport",
            bool(settings.kafka_bootstrap_servers),
            "Bootstrap servers configured; topic ACLs, schema compatibility, consumer evidence "
            "and production delivery validation remain required",
            "Kafka bootstrap servers are not configured",
        ),
        integration(
            "apisix_gateway",
            bool(settings.apisix_admin_url),
            "Gateway management endpoint configured; declarative routes, TLS, policy tests and "
            "change control remain required",
            "APISIX management endpoint is not configured",
        ),
        integration(
            "keycloak_identity",
            bool(settings.keycloak_issuer_url),
            "OIDC issuer configured; realm, client, PKCE, claims, key rotation and conformance "
            "evidence remain required",
            "Keycloak OIDC issuer is not configured",
        ),
        integration(
            "openappsec_ingress_protection",
            bool(settings.openappsec_management_url),
            "Management endpoint configured; detection-mode baselining, enforcement policy and "
            "attack-test evidence remain required",
            "openAppSec management endpoint is not configured",
        ),
        integration(
            "permify_authorization",
            bool(settings.permify_base_url),
            "Authorization endpoint configured; schema publication, tuple migration, decision "
            "logging and negative-access evidence remain required",
            "Permify authorization endpoint is not configured",
        ),
        integration(
            "opensearch_projection",
            bool(settings.opensearch_url),
            "Search endpoint configured; projection pipeline, retention policy, access controls "
            "and replay validation remain required",
            "OpenSearch endpoint is not configured",
        ),
        integration(
            "fluvio_edge_streaming",
            bool(settings.fluvio_cluster_url),
            "Cluster endpoint configured; edge use case, delivery semantics and duplicate-bus "
            "controls remain required",
            "Fluvio cluster endpoint is not configured",
        ),
        integration(
            "dapr_service_runtime",
            bool(settings.dapr_http_url),
            "Sidecar endpoint configured; component manifests, mTLS, resiliency policy and "
            "workflow evidence remain required",
            "Dapr sidecar endpoint is not configured",
        ),
        integration(
            "lakehouse_analytics_projection",
            bool(settings.lakehouse_catalog_url),
            "Catalog endpoint configured; immutable dataset policy, lineage, retention and "
            "regulated-reporting validation remain required",
            "Lakehouse catalog endpoint is not configured",
        ),
    ]
    return sorted([*_STATIC, *dynamic], key=lambda capability: capability.name)


def capability_document(settings: Settings) -> JsonObject:
    return {
        "capabilities": [
            {"name": c.name, "state": c.state.value, "detail": c.detail} for c in capability_report(settings)
        ]
    }

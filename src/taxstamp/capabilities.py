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
        CapabilityState.IMPLEMENTED,
        "Signed, sequenced revocation bundles for disconnected devices, and replay-protected "
        "batch synchronisation in which every captured scan is re-decided server-side; a "
        "bundle proves only that a serial is not revoked, never that it is genuine",
    ),
    Capability(
        "consumer_verification",
        CapabilityState.IMPLEMENTED,
        "Unauthenticated public stamp check, rate limited per caller address, disclosing "
        "only product identity and authenticity",
    ),
    Capability(
        "enforcement_case_management",
        CapabilityState.IMPLEMENTED,
        "Cases, evidence, seizures and hash-chained chain of custody, with closure reserved "
        "to a supervisor other than the officer who opened the case",
    ),
    Capability(
        "risk_scoring",
        CapabilityState.IMPLEMENTED,
        "Deterministic weighted counts of stored evidence with a per-factor explanation and "
        "a recorded model version; no learned or statistical model",
    ),
    Capability(
        "programme_reporting",
        CapabilityState.IMPLEMENTED,
        "KPI counters and itemised revenue-at-risk exposure over an explicit window; "
        "exposure is not an assessed liability and contains no extrapolation",
    ),
    Capability(
        "edge_gateway_protection",
        CapabilityState.REQUIRES_CONFIGURATION,
        "APISIX routes and an open-appsec policy are declared in deploy/edge, but TLS "
        "certificates, device client certificates and a deployed WAF are operational "
        "prerequisites this codebase cannot satisfy or evidence",
    ),
    Capability(
        "prosecution_case_filing",
        CapabilityState.NOT_IMPLEMENTED,
        "No court or prosecution system is integrated; referral records the platform "
        "holder's decision and never claims an external filing was accepted",
    ),
)


def capability_report(settings: Settings) -> list[Capability]:
    dynamic = [
        Capability(
            "federated_human_identity",
            CapabilityState.IMPLEMENTED if settings.oidc_issuer else CapabilityState.REQUIRES_CONFIGURATION,
            "Provider tokens are verified against the issuer's key set and accepted only for "
            "a principal an administrator linked; without an issuer they are refused, and "
            "devices and service accounts never depend on the provider being reachable",
        ),
        Capability(
            "external_authorisation_engine",
            CapabilityState.IMPLEMENTED
            if settings.permify_base_url and settings.authz_external_mode != "disabled"
            else CapabilityState.REQUIRES_CONFIGURATION,
            f"Mode {settings.authz_external_mode}: the local role table is the policy of record "
            "and the engine can only confirm it or add an explicitly modelled delegated read; "
            "an engine that cannot answer denies rather than admits",
        ),
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
    ]
    return sorted([*_STATIC, *dynamic], key=lambda capability: capability.name)


def capability_document(settings: Settings) -> JsonObject:
    return {
        "capabilities": [
            {"name": c.name, "state": c.state.value, "detail": c.detail} for c in capability_report(settings)
        ]
    }

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
        CapabilityState.NOT_IMPLEMENTED,
        "Offline capture and reconciliation of field verifications is not implemented",
    ),
)


def capability_report(settings: Settings) -> list[Capability]:
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
    ]
    return sorted([*_STATIC, *dynamic], key=lambda capability: capability.name)


def capability_document(settings: Settings) -> JsonObject:
    return {
        "capabilities": [
            {"name": c.name, "state": c.state.value, "detail": c.detail} for c in capability_report(settings)
        ]
    }

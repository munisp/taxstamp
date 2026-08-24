"""Regulatory compliance checks against external registries.

Each check calls the configured registry over HTTP and requires an explicit,
well-formed decision. An unreachable, unconfigured, or malformed registry response is
never treated as compliant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from taxstamp.jsontypes import JsonObject, optional_str, require_bool, require_str
from taxstamp.providers.base import ProviderClient


class Registry(StrEnum):
    FIRS = "firs"
    NAFDAC = "nafdac"
    SON = "son"
    CUSTOMS = "customs"


@dataclass(frozen=True, slots=True)
class RegistryDecision:
    registry: Registry
    compliant: bool
    reference: str
    reason: str | None
    checked_at: str


@dataclass(frozen=True, slots=True)
class ComplianceOutcome:
    compliant: bool
    decisions: tuple[RegistryDecision, ...]

    def as_evidence(self) -> JsonObject:
        return {
            "compliant": self.compliant,
            "decisions": [
                {
                    "registry": decision.registry.value,
                    "compliant": decision.compliant,
                    "reference": decision.reference,
                    "reason": decision.reason,
                    "checked_at": decision.checked_at,
                }
                for decision in self.decisions
            ],
        }


REQUIRED_REGISTRIES: dict[str, tuple[Registry, ...]] = {
    "tobacco": (Registry.FIRS, Registry.SON, Registry.CUSTOMS),
    "alcohol": (Registry.FIRS, Registry.NAFDAC, Registry.SON),
    "pharmaceuticals": (Registry.FIRS, Registry.NAFDAC),
    "beverages": (Registry.FIRS, Registry.NAFDAC),
}


class ComplianceService:
    def __init__(self, clients: dict[Registry, ProviderClient]) -> None:
        self._clients = clients

    def registries_for(self, product_category: str) -> tuple[Registry, ...]:
        return REQUIRED_REGISTRIES.get(product_category, (Registry.FIRS,))

    def unconfigured(self, product_category: str) -> tuple[Registry, ...]:
        return tuple(
            registry
            for registry in self.registries_for(product_category)
            if not self._clients[registry].configured
        )

    def check(self, *, tin: str, product_category: str, quantity: int) -> ComplianceOutcome:
        decisions: list[RegistryDecision] = []
        for registry in self.registries_for(product_category):
            client = self._clients[registry]
            client.require_configured()
            response = client.post_json(
                "/v1/compliance-checks",
                {"tin": tin, "product_category": product_category, "quantity": quantity},
            )
            decisions.append(
                RegistryDecision(
                    registry=registry,
                    compliant=require_bool(response, "compliant"),
                    reference=require_str(response, "reference"),
                    reason=optional_str(response, "reason"),
                    checked_at=require_str(response, "checked_at"),
                )
            )
        return ComplianceOutcome(
            compliant=all(decision.compliant for decision in decisions),
            decisions=tuple(decisions),
        )

"""Fail-closed reconciliation of local intents against external settlement snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from taxstamp.jsontypes import JsonObject, JsonValue


class SettlementProvider(StrEnum):
    TIGERBEETLE = "tigerbeetle"
    MOJALOOP = "mojaloop"


class SettlementState(StrEnum):
    SETTLED = "settled"
    PENDING = "pending"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExpectedSettlement:
    reference: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class ExternalSettlement:
    provider: SettlementProvider
    reference: str
    external_id: str
    amount_minor: int
    currency: str
    state: SettlementState

    def __post_init__(self) -> None:
        if not self.reference or not self.external_id:
            raise ValueError("external settlement requires reference and external_id")
        if self.amount_minor <= 0:
            raise ValueError("external settlement amount_minor must be positive")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("external settlement currency must be a three-letter code")


@dataclass(frozen=True, slots=True)
class ExternalFinding:
    kind: str
    count: int
    detail: JsonObject


def parse_snapshot(provider: SettlementProvider, document: JsonObject) -> tuple[ExternalSettlement, ...]:
    """Parse a reviewed sandbox export; credentials and live calls stay outside this code."""

    entries = document.get("settlements")
    if not isinstance(entries, list):
        raise TypeError("sandbox snapshot must contain a settlements list")
    result: list[ExternalSettlement] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("sandbox settlement must be an object")
        reference = entry.get("reference")
        external_id = entry.get("external_id")
        amount_minor = entry.get("amount_minor")
        currency = entry.get("currency")
        state = entry.get("state")
        if not isinstance(reference, str) or not isinstance(external_id, str):
            raise TypeError("sandbox settlement identifiers must be strings")
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
            raise TypeError("sandbox settlement amount_minor must be an integer")
        if not isinstance(currency, str) or not isinstance(state, str):
            raise TypeError("sandbox settlement currency and state must be strings")
        result.append(
            ExternalSettlement(
                provider=provider,
                reference=reference,
                external_id=external_id,
                amount_minor=amount_minor,
                currency=currency.upper(),
                state=SettlementState(state.lower()),
            )
        )
    return tuple(result)


def reconcile_external_settlements(
    expected: Iterable[ExpectedSettlement],
    observed: Iterable[ExternalSettlement],
    *,
    providers: Iterable[SettlementProvider] | None = None,
) -> tuple[ExternalFinding, ...]:
    """Compare settlement exports without auto-correcting either financial system."""

    expected_by_reference = {item.reference: item for item in expected}
    observed_by_provider: dict[SettlementProvider, list[ExternalSettlement]] = {}
    for settlement in observed:
        observed_by_provider.setdefault(settlement.provider, []).append(settlement)

    findings: list[ExternalFinding] = []
    providers_to_check = tuple(providers) if providers is not None else tuple(observed_by_provider)
    for provider in providers_to_check:
        settlements = observed_by_provider.get(provider, [])
        references: dict[str, list[ExternalSettlement]] = {}
        for settlement in settlements:
            references.setdefault(settlement.reference, []).append(settlement)

        duplicates = [reference for reference, rows in references.items() if len(rows) > 1]
        if duplicates:
            duplicate_values: list[JsonValue] = list(duplicates[:50])
            findings.append(
                ExternalFinding(
                    kind=f"{provider.value}_duplicate_reference",
                    count=len(duplicates),
                    detail={"references": duplicate_values},
                )
            )

        unknown: list[JsonValue] = []
        mismatches: list[JsonValue] = []
        non_settled: list[JsonValue] = []
        for reference, rows in references.items():
            settlement = rows[0]
            local = expected_by_reference.get(reference)
            if local is None:
                unknown.append({"reference": reference, "external_id": settlement.external_id})
                continue
            if local.amount_minor != settlement.amount_minor or local.currency != settlement.currency:
                mismatches.append(
                    {
                        "reference": reference,
                        "expected_minor": local.amount_minor,
                        "observed_minor": settlement.amount_minor,
                        "expected_currency": local.currency,
                        "observed_currency": settlement.currency,
                    }
                )
            if settlement.state is not SettlementState.SETTLED:
                non_settled.append({"reference": reference, "state": settlement.state.value})

        if unknown:
            findings.append(
                ExternalFinding(
                    kind=f"{provider.value}_unknown_reference",
                    count=len(unknown),
                    detail={"settlements": unknown[:50]},
                )
            )
        if mismatches:
            findings.append(
                ExternalFinding(
                    kind=f"{provider.value}_amount_or_currency_mismatch",
                    count=len(mismatches),
                    detail={"settlements": mismatches[:50]},
                )
            )
        if non_settled:
            findings.append(
                ExternalFinding(
                    kind=f"{provider.value}_state_mismatch",
                    count=len(non_settled),
                    detail={"settlements": non_settled[:50]},
                )
            )

        observed_references = set(references)
        missing = sorted(set(expected_by_reference) - observed_references)
        if missing:
            missing_values: list[JsonValue] = list(missing[:50])
            findings.append(
                ExternalFinding(
                    kind=f"{provider.value}_missing_external_settlement",
                    count=len(missing),
                    detail={"references": missing_values},
                )
            )
    return tuple(findings)

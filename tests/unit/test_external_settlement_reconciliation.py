"""Financial sandbox comparison rejects ambiguity and never silently repairs it."""

from __future__ import annotations

import pytest

from taxstamp.services.external_settlement_reconciliation import (
    ExpectedSettlement,
    ExternalSettlement,
    SettlementProvider,
    SettlementState,
    parse_snapshot,
    reconcile_external_settlements,
)

pytestmark = pytest.mark.unit


def _expected() -> tuple[ExpectedSettlement, ...]:
    return (ExpectedSettlement(reference="PAY-001", amount_minor=12_500, currency="NGN"),)


def _observed(**overrides: object) -> ExternalSettlement:
    values: dict[str, object] = {
        "provider": SettlementProvider.TIGERBEETLE,
        "reference": "PAY-001",
        "external_id": "transfer-001",
        "amount_minor": 12_500,
        "currency": "NGN",
        "state": SettlementState.SETTLED,
    }
    values.update(overrides)
    return ExternalSettlement(**values)  # type: ignore[arg-type]


def test_matching_external_settlement_is_clean() -> None:
    assert reconcile_external_settlements(_expected(), (_observed(),)) == ()


def test_external_amount_and_state_mismatch_are_findings() -> None:
    findings = reconcile_external_settlements(
        _expected(), (_observed(amount_minor=10_000, state=SettlementState.PENDING),)
    )
    assert {finding.kind for finding in findings} == {
        "tigerbeetle_amount_or_currency_mismatch",
        "tigerbeetle_state_mismatch",
    }


def test_missing_or_unknown_external_settlements_are_findings() -> None:
    missing = reconcile_external_settlements(_expected(), (), providers=(SettlementProvider.TIGERBEETLE,))
    assert [finding.kind for finding in missing] == ["tigerbeetle_missing_external_settlement"]
    unknown = reconcile_external_settlements(
        _expected(), (_observed(reference="UNKNOWN", external_id="transfer-unknown"),)
    )
    assert {finding.kind for finding in unknown} == {
        "tigerbeetle_unknown_reference",
        "tigerbeetle_missing_external_settlement",
    }


def test_snapshot_parser_rejects_bad_money() -> None:
    with pytest.raises(TypeError, match="amount_minor"):
        parse_snapshot(
            SettlementProvider.MOJALOOP,
            {
                "settlements": [
                    {
                        "reference": "PAY-001",
                        "external_id": "transfer-001",
                        "amount_minor": "12500",
                        "currency": "NGN",
                        "state": "settled",
                    }
                ]
            },
        )

"""Money is exact: no float ever reaches a stored amount."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from taxstamp.money import Money, MoneyError, price_order

pytestmark = pytest.mark.unit


def test_from_major_is_exact_for_repeating_decimals() -> None:
    assert Money.from_major(Decimal("0.01")).minor == 1
    assert Money.from_major(Decimal("12345.67")).minor == 1_234_567


def test_from_major_rejects_sub_minor_precision() -> None:
    with pytest.raises(MoneyError):
        Money.from_major(Decimal("1.005"))


def test_float_input_is_rejected() -> None:
    with pytest.raises(MoneyError):
        Money.from_major(0.1)  # type: ignore[arg-type]


def test_currency_mismatch_is_rejected() -> None:
    with pytest.raises(MoneyError):
        _ = Money(100, "NGN") + Money(100, "USD")


def test_vat_uses_half_up_rounding_on_exact_integers() -> None:
    # 7.5% of 5 kobo is 0.375 kobo, which must round to 0 rather than truncate silently.
    assert Money(5, "NGN").apply_bps(750).minor == 0
    assert Money(7, "NGN").apply_bps(750).minor == 1


@given(
    unit=st.integers(min_value=1, max_value=10**9),
    quantity=st.integers(min_value=1, max_value=10**6),
    vat_bps=st.integers(min_value=0, max_value=5_000),
)
def test_price_breakdown_is_internally_consistent(unit: int, quantity: int, vat_bps: int) -> None:
    breakdown = price_order(quantity, Money(unit, "NGN"), vat_bps)
    assert breakdown.subtotal.minor == unit * quantity
    assert breakdown.total.minor == breakdown.subtotal.minor + breakdown.vat.minor
    assert breakdown.vat.minor >= 0


def test_decimal_presentation_round_trips() -> None:
    amount = Money(1_234_567, "NGN")
    assert amount.major == Decimal("12345.67")
    assert Money.from_major(amount.major) == amount

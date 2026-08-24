"""Exact monetary arithmetic.

Currency amounts are integers in minor units (kobo for NGN). Binary floating point
is never used for money. Percentage rates are expressed in basis points and applied
with decimal arithmetic and ROUND_HALF_UP, matching Nigerian VAT invoicing practice.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CURRENCY_EXPONENTS: dict[str, int] = {"NGN": 2, "USD": 2}
DEFAULT_CURRENCY = "NGN"


class MoneyError(ValueError):
    """Raised for unsupported currencies or invalid monetary operations."""


def exponent_for(currency: str) -> int:
    try:
        return CURRENCY_EXPONENTS[currency]
    except KeyError as exc:
        raise MoneyError(f"unsupported currency: {currency!r}") from exc


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount of money in minor units."""

    minor: int
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        exponent_for(self.currency)
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise MoneyError("minor units must be an int")

    @classmethod
    def zero(cls, currency: str = DEFAULT_CURRENCY) -> Money:
        return cls(0, currency)

    @classmethod
    def from_major(cls, major: str | int | Decimal, currency: str = DEFAULT_CURRENCY) -> Money:
        exponent = exponent_for(currency)
        if isinstance(major, float):
            raise MoneyError("binary floating point is not accepted for money")
        if not isinstance(major, str | int | Decimal) or isinstance(major, bool):
            raise MoneyError(f"unsupported monetary input type: {type(major).__name__}")
        value = Decimal(str(major)).scaleb(exponent)
        if value != value.to_integral_value():
            raise MoneyError(f"{major} has more precision than {currency} supports")
        return cls(int(value), currency)

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise MoneyError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor - other.minor, self.currency)

    def times(self, quantity: int) -> Money:
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise MoneyError("quantity must be an int")
        if quantity < 0:
            raise MoneyError("quantity must not be negative")
        return Money(self.minor * quantity, self.currency)

    def apply_bps(self, basis_points: int) -> Money:
        """Return the portion of this amount given by ``basis_points`` (ROUND_HALF_UP)."""
        if basis_points < 0:
            raise MoneyError("basis points must not be negative")
        portion = (Decimal(self.minor) * Decimal(basis_points) / Decimal(10_000)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return Money(int(portion), self.currency)

    @property
    def major(self) -> Decimal:
        return (Decimal(self.minor) / Decimal(10) ** exponent_for(self.currency)).quantize(
            Decimal(1).scaleb(-exponent_for(self.currency))
        )

    def __str__(self) -> str:
        return f"{self.major} {self.currency}"


@dataclass(frozen=True, slots=True)
class PriceBreakdown:
    """Server-computed price for an order. Client-supplied amounts are never trusted."""

    quantity: int
    unit_price: Money
    subtotal: Money
    vat_bps: int
    vat: Money
    total: Money

    def assert_consistent(self) -> None:
        expected_subtotal = self.unit_price.times(self.quantity)
        if expected_subtotal != self.subtotal:
            raise MoneyError("subtotal does not equal unit price x quantity")
        if self.subtotal.apply_bps(self.vat_bps) != self.vat:
            raise MoneyError("VAT does not match the stated basis points")
        if self.subtotal + self.vat != self.total:
            raise MoneyError("total does not equal subtotal + VAT")


def price_order(quantity: int, unit_price: Money, vat_bps: int) -> PriceBreakdown:
    if quantity <= 0:
        raise MoneyError("quantity must be positive")
    if unit_price.minor <= 0:
        raise MoneyError("unit price must be positive")
    subtotal = unit_price.times(quantity)
    vat = subtotal.apply_bps(vat_bps)
    breakdown = PriceBreakdown(
        quantity=quantity,
        unit_price=unit_price,
        subtotal=subtotal,
        vat_bps=vat_bps,
        vat=vat,
        total=subtotal + vat,
    )
    breakdown.assert_consistent()
    return breakdown

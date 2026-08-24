"""State machines reject illegal moves."""

from __future__ import annotations

import pytest

from taxstamp.enums import (
    OrderStatus,
    StampStatus,
    TransitionError,
    assert_order_transition,
    assert_stamp_transition,
)

pytestmark = pytest.mark.unit


def test_paid_order_cannot_go_back_to_awaiting_payment() -> None:
    with pytest.raises(TransitionError):
        assert_order_transition(OrderStatus.PAID, OrderStatus.AWAITING_PAYMENT)


def test_terminal_states_are_terminal() -> None:
    for target in OrderStatus:
        with pytest.raises(TransitionError):
            assert_order_transition(OrderStatus.CANCELLED, target)


def test_void_stamp_cannot_be_activated() -> None:
    with pytest.raises(TransitionError):
        assert_stamp_transition(StampStatus.VOID, StampStatus.ACTIVE)


def test_expired_stamp_cannot_be_activated() -> None:
    with pytest.raises(TransitionError):
        assert_stamp_transition(StampStatus.EXPIRED, StampStatus.ACTIVE)

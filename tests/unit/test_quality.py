"""Acceptance sampling is a real plan, not a pass-through."""

from __future__ import annotations

import pytest

from taxstamp.quality import SamplingError, sampling_plan

pytestmark = pytest.mark.unit


def test_sample_size_grows_with_lot_size() -> None:
    sizes = [sampling_plan(lot).sample_size for lot in (10, 100, 1_000, 10_000, 100_000)]
    assert sizes == sorted(sizes)
    assert sizes[0] < sizes[-1]


def test_reject_number_fails_the_lot() -> None:
    plan = sampling_plan(5_000)
    assert plan.evaluate(plan.accept_number)
    assert not plan.evaluate(plan.reject_number)


def test_defects_cannot_exceed_sample_size() -> None:
    plan = sampling_plan(100)
    with pytest.raises(SamplingError):
        plan.evaluate(plan.sample_size + 1)


def test_non_positive_lot_is_rejected() -> None:
    with pytest.raises(SamplingError):
        sampling_plan(0)

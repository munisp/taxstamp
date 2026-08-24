"""Acceptance sampling for issued batches.

Implements a single-sampling plan in the style of ANSI/ASQ Z1.4 general inspection
level II at AQL 0.65%. The plan is data, not a hardcoded pass: an inspection whose
observed defects reach the rejection number fails the batch and blocks activation.
"""

from __future__ import annotations

from dataclasses import dataclass

# (inclusive lot-size upper bound, sample size, accept number, reject number)
_PLAN: tuple[tuple[int, int, int, int], ...] = (
    (8, 2, 0, 1),
    (15, 3, 0, 1),
    (25, 5, 0, 1),
    (50, 8, 0, 1),
    (90, 13, 0, 1),
    (150, 20, 0, 1),
    (280, 32, 0, 1),
    (500, 50, 1, 2),
    (1_200, 80, 1, 2),
    (3_200, 125, 2, 3),
    (10_000, 200, 3, 4),
    (35_000, 315, 5, 6),
    (150_000, 500, 7, 8),
    (500_000, 800, 10, 11),
    (10_000_000_000, 1_250, 14, 15),
)


class SamplingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    lot_size: int
    sample_size: int
    accept_number: int
    reject_number: int

    def evaluate(self, defects_found: int) -> bool:
        if defects_found < 0:
            raise SamplingError("defects found must not be negative")
        if defects_found > self.sample_size:
            raise SamplingError("defects found exceeds the sample size")
        return defects_found <= self.accept_number


def sampling_plan(lot_size: int) -> SamplingPlan:
    if lot_size <= 0:
        raise SamplingError("lot size must be positive")
    for upper_bound, sample_size, accept, reject in _PLAN:
        if lot_size <= upper_bound:
            return SamplingPlan(
                lot_size=lot_size,
                sample_size=min(sample_size, lot_size),
                accept_number=accept,
                reject_number=reject,
            )
    raise SamplingError("lot size exceeds the tabulated plan")

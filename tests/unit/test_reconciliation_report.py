"""Reconciliation report shaping."""

from __future__ import annotations

import pathlib

import pytest

from taxstamp.services.reconciliation import FINDING_KINDS, Finding, ReconciliationReport

pytestmark = pytest.mark.unit


def test_counts_by_kind_reports_zero_for_absent_kinds() -> None:
    report = ReconciliationReport(
        findings=(Finding(kind="duplicate_serial", count=3, detail={}),), checks_run=8
    )
    counts = report.counts_by_kind()
    assert set(counts) == set(FINDING_KINDS)
    assert counts["duplicate_serial"] == 3
    # A kind this run did not report must be published as zero, otherwise a gauge keeps
    # its last non-zero value and alerts on a finding that has already been resolved.
    assert counts["audit_chain_broken"] == 0
    assert sum(counts.values()) == 3


def test_every_finding_kind_produced_by_a_check_is_declared() -> None:
    """FINDING_KINDS must stay in step with the kinds the checks emit."""
    module = pathlib.Path(__file__).resolve().parents[2] / "src/taxstamp/services/reconciliation.py"
    source = module.read_text()
    emitted = {
        line.split('kind="', 1)[1].split('"', 1)[0]
        for line in source.splitlines()
        if 'kind="' in line and "FINDING_KINDS" not in line
    }
    emitted.discard("full")
    assert emitted <= set(FINDING_KINDS)

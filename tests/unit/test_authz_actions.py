"""Invariants of the authorisation table itself.

The table is reviewed as policy, so the properties a reviewer would check by eye are
asserted here instead: no action silently unreachable, no privilege on a role that must
not hold it, and separation of duties preserved for the actions that carry money or
liberty.
"""

from __future__ import annotations

import pytest

from taxstamp.authz.actions import (
    DELEGABLE_ACTIONS,
    LOCAL_RULES,
    Action,
    locally_permitted,
)
from taxstamp.enums import Role

pytestmark = pytest.mark.unit

#: Actions a handheld in the field must be able to perform without an interactive login.
DEVICE_ACTIONS = frozenset(
    {
        Action.VERIFY_FIELD,
        Action.OFFLINE_BUNDLE_READ,
        Action.OFFLINE_SCAN_SUBMIT,
    }
)

#: Every action that changes state, derived by subtracting the reads so that a new
#: action added to the table is treated as a write until it is classified.
WRITES = frozenset(LOCAL_RULES) - frozenset(
    {
        Action.STAMP_READ,
        Action.BATCH_READ,
        Action.TREASURY_RECEIPTS_READ,
        Action.REPORT_PROGRAMME,
        Action.REPORT_RISK,
        # An export reads records and writes only a copy of them; the auditor exclusion
        # below is about changing the programme, not about reading it out of it.
        Action.EXPORT_REGULATOR,
        Action.OFFLINE_BUNDLE_READ,
        Action.OPS_METRICS_READ,
        Action.OPS_AUDIT_READ,
        # Reconciliation compares the ledger with the business records and changes
        # neither, so an auditor may run it.
        Action.OPS_RECONCILE,
    }
)


def test_every_action_is_reachable_by_some_role() -> None:
    """An action no role holds is a dead route, not a security control."""
    missing = [action for action in Action if not LOCAL_RULES.get(action)]
    assert missing == []


def test_devices_hold_only_field_capabilities() -> None:
    """A stolen handheld must not be able to order stamps or approve anything."""
    granted = {action for action in Action if locally_permitted(action, Role.DEVICE)}
    assert granted == DEVICE_ACTIONS


def test_requesters_cannot_approve_or_settle_their_own_orders() -> None:
    """Maker-checker is a table property before it is a service property."""
    for action in (
        Action.ORDER_APPROVE,
        Action.PAYMENT_INGEST,
        Action.TREASURY_APPLY,
        Action.TREASURY_REFUND,
        Action.STAMP_ISSUE,
    ):
        assert not locally_permitted(action, Role.REQUESTER), action


def test_auditors_hold_no_write_capability() -> None:
    """An auditor reads the programme; an auditor who can change it cannot audit it."""
    for action in WRITES:
        assert not locally_permitted(action, Role.AUDITOR), action


def test_manufacturer_roles_hold_no_enforcement_capability() -> None:
    """The regulated party must not be able to open, decide or close a case about itself."""
    for role in (Role.REQUESTER, Role.OPERATOR, Role.DEVICE):
        for action in (
            Action.CASE_OPEN,
            Action.CASE_DECIDE,
            Action.SEIZURE_RECORD,
            Action.CUSTODY_TRANSFER,
            Action.CUSTOMS_RELEASE,
            Action.LICENCE_MANAGE,
            Action.EXPORT_REGULATOR,
        ):
            assert not locally_permitted(action, role), (role, action)


def test_delegable_actions_are_reads() -> None:
    """Delegation may widen sight across a tenant boundary, never authority."""
    assert frozenset(LOCAL_RULES) >= DELEGABLE_ACTIONS
    assert not DELEGABLE_ACTIONS & WRITES


def test_an_unmapped_action_is_denied() -> None:
    """Absence from the table denies; it does not fall through to a default allow."""
    assert not locally_permitted(Action.ORDER_APPROVE, Role.DEVICE)

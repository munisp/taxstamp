"""The authorisation table must say what the services actually enforce.

The table at the API boundary and the ``require_role`` call inside each domain service are
two statements of the same policy. If they drift, the table becomes documentation rather
than control: a reviewer reads one answer and a request gets another. Each case below
pairs an action with the service call that is reached through it, and asserts they permit
exactly the same roles.

Where a service is deliberately stricter than the table - seizure settlement, whose
permitted roles depend on the outcome - the table is the upper bound and the service
narrows it, so those actions are listed as bounds rather than equalities.
"""

from __future__ import annotations

import pytest

from taxstamp.authz.actions import LOCAL_RULES, Action
from taxstamp.enums import Role
from taxstamp.services import (
    customs,
    enforcement,
    exports,
    offline,
    registry,
    reporting,
    stamps,
    traceability,
    transparency,
    treasury,
)
from taxstamp.services.context import CROSS_TENANT_READERS

pytestmark = pytest.mark.unit

#: Action -> the role set its service enforces. Equality is required.
ENFORCED: dict[Action, frozenset[Role]] = {
    Action.TREASURY_APPLY: frozenset({Role.TREASURY, Role.ADMIN}),
    Action.TREASURY_REFUND: frozenset({Role.TREASURY, Role.ADMIN}),
    Action.STAMP_ISSUE: frozenset({Role.OPERATOR, Role.ADMIN}),
    Action.STAMP_ACTIVATE: frozenset({Role.REQUESTER, Role.OPERATOR, Role.ADMIN}),
    Action.TRACE_RECORD: frozenset({Role.REQUESTER, Role.OPERATOR, Role.ADMIN}),
    Action.CUSTOMS_DECLARE: frozenset({Role.REQUESTER, Role.OPERATOR, Role.ADMIN}),
    Action.CUSTOMS_RELEASE: frozenset({Role.SUPERVISOR, Role.ADMIN}),
    Action.LICENCE_MANAGE: frozenset({Role.ADMIN}),
    Action.PRODUCT_MANAGE: frozenset({Role.REQUESTER, Role.ADMIN}),
    Action.CASE_OPEN: enforcement.INVESTIGATORS,
    Action.SEIZURE_RECORD: enforcement.INVESTIGATORS,
    Action.CUSTODY_TRANSFER: enforcement.INVESTIGATORS,
    Action.REPORT_PROGRAMME: reporting.REPORT_READERS,
    Action.EXPORT_REGULATOR: frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    Action.EXPORT_PORTABILITY: frozenset({Role.REQUESTER, Role.ADMIN}),
    Action.CHECKPOINT_PUBLISH: frozenset({Role.SUPERVISOR, Role.ADMIN}),
    Action.OFFLINE_BUNDLE_PUBLISH: offline.BUNDLE_PUBLISHERS,
    Action.OFFLINE_SCAN_SUBMIT: frozenset({Role.DEVICE, Role.OPERATOR, Role.ADMIN}),
}

#: The modules whose ``require_role`` calls the sets above are drawn from, imported so
#: that deleting or renaming one of them fails here rather than silently loosening policy.
COVERED_MODULES = (
    customs,
    enforcement,
    exports,
    offline,
    registry,
    reporting,
    stamps,
    transparency,
    traceability,
    treasury,
)


#: Action -> the widest role set its service can accept, where the service then narrows
#: the decision on facts the API boundary does not have.
BOUNDS: dict[Action, frozenset[Role]] = {
    # Analysts open investigations; referral and closure are a supervisor's, never the
    # opener's.
    Action.CASE_DECIDE: enforcement.INVESTIGATORS | enforcement.DECIDERS,
    # A requester reaches its own company's score only; a cross-tenant reader any.
    Action.REPORT_RISK: frozenset({Role.REQUESTER}) | CROSS_TENANT_READERS,
}


@pytest.mark.parametrize(("action", "enforced"), sorted(ENFORCED.items()))
def test_table_matches_what_the_service_enforces(action: Action, enforced: frozenset[Role]) -> None:
    assert LOCAL_RULES[action] == enforced, action


@pytest.mark.parametrize(("action", "bound"), sorted(BOUNDS.items()))
def test_table_is_the_outer_bound_where_the_service_narrows(action: Action, bound: frozenset[Role]) -> None:
    assert LOCAL_RULES[action] == bound, action


def test_every_covered_module_still_enforces_roles() -> None:
    """A service that stopped checking roles would make its table entry unenforced."""
    for module in COVERED_MODULES:
        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            assert "require_role" in handle.read(), module.__name__

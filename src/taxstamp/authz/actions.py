"""The authorisation table of record.

Every privileged API operation names an action here, and the roles listed against that
action are the platform's own answer to "may this credential do this?". The table lives
in one place so it can be reviewed as policy rather than discovered by reading routers.

An external policy engine may *narrow* these decisions (see :mod:`taxstamp.authz.policy`)
and may grant explicitly delegated cross-tenant reads, but it can never widen a role's
capability: an action the table denies is denied whatever the engine says. That ordering
is what keeps an engine outage - or a misconfigured engine - from escalating privilege.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from taxstamp.enums import Role


class Action(StrEnum):
    """A privileged operation at the API boundary."""

    ORDER_CREATE = "order.create"
    ORDER_APPROVE = "order.approve"
    ORDER_CANCEL = "order.cancel"
    PAYMENT_INGEST = "payment.ingest"
    TREASURY_APPLY = "treasury.apply"
    TREASURY_REFUND = "treasury.refund"
    TREASURY_RECEIPTS_READ = "treasury.receipts.read"
    STAMP_ISSUE = "stamp.issue"
    STAMP_INSPECT = "stamp.inspect"
    STAMP_ACTIVATE = "stamp.activate"
    STAMP_READ = "stamp.read"
    BATCH_READ = "batch.read"
    VERIFY_FIELD = "verify.field"
    TRACE_RECORD = "trace.record"
    CUSTOMS_DECLARE = "customs.declare"
    CUSTOMS_RELEASE = "customs.release"
    LICENCE_MANAGE = "licence.manage"
    PRODUCT_MANAGE = "product.manage"
    CASE_OPEN = "case.open"
    CASE_DECIDE = "case.decide"
    SEIZURE_RECORD = "seizure.record"
    CUSTODY_TRANSFER = "custody.transfer"
    REPORT_PROGRAMME = "report.programme"
    REPORT_RISK = "report.risk"
    EXPORT_REGULATOR = "export.regulator"
    EXPORT_PORTABILITY = "export.portability"
    CHECKPOINT_PUBLISH = "checkpoint.publish"
    OFFLINE_BUNDLE_PUBLISH = "offline.bundle.publish"
    OFFLINE_BUNDLE_READ = "offline.bundle.read"
    OFFLINE_SCAN_SUBMIT = "offline.scan.submit"
    OPS_METRICS_READ = "ops.metrics.read"
    OPS_RECONCILE = "ops.reconcile"
    OPS_AUDIT_READ = "ops.audit.read"


_ALL_STAFF: Final[frozenset[Role]] = frozenset(
    {Role.ANALYST, Role.SUPERVISOR, Role.ADMIN},
)

#: Action -> roles permitted to attempt it. Absence from this table is a denial.
LOCAL_RULES: Final[dict[Action, frozenset[Role]]] = {
    Action.ORDER_CREATE: frozenset({Role.REQUESTER, Role.ADMIN}),
    Action.ORDER_APPROVE: frozenset({Role.SUPERVISOR, Role.ADMIN}),
    Action.ORDER_CANCEL: frozenset({Role.REQUESTER, Role.SUPERVISOR, Role.ADMIN}),
    Action.PAYMENT_INGEST: frozenset({Role.TREASURY, Role.ADMIN}),
    Action.TREASURY_APPLY: frozenset({Role.TREASURY, Role.ADMIN}),
    Action.TREASURY_REFUND: frozenset({Role.TREASURY, Role.ADMIN}),
    Action.TREASURY_RECEIPTS_READ: frozenset({Role.TREASURY, Role.AUDITOR, Role.ADMIN}),
    Action.STAMP_ISSUE: frozenset({Role.OPERATOR, Role.ADMIN}),
    Action.STAMP_INSPECT: frozenset({Role.OPERATOR, Role.ADMIN}),
    Action.STAMP_ACTIVATE: frozenset({Role.REQUESTER, Role.OPERATOR, Role.ADMIN}),
    Action.STAMP_READ: frozenset({Role.REQUESTER, Role.OPERATOR, Role.AUDITOR, *_ALL_STAFF}),
    Action.BATCH_READ: frozenset({Role.REQUESTER, Role.OPERATOR, Role.AUDITOR, Role.ADMIN}),
    Action.VERIFY_FIELD: frozenset({Role.DEVICE, Role.OPERATOR, Role.ADMIN}),
    Action.TRACE_RECORD: frozenset({Role.REQUESTER, Role.OPERATOR, Role.ADMIN}),
    Action.CUSTOMS_DECLARE: frozenset({Role.REQUESTER, Role.OPERATOR, Role.ADMIN}),
    Action.CUSTOMS_RELEASE: frozenset({Role.SUPERVISOR, Role.ADMIN}),
    Action.LICENCE_MANAGE: frozenset({Role.ADMIN}),
    Action.PRODUCT_MANAGE: frozenset({Role.REQUESTER, Role.ADMIN}),
    Action.CASE_OPEN: frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    # An analyst may move a case into investigation; only a supervisor who did not open
    # it may refer or close it. The narrower half of that rule depends on the target
    # status and on who opened the case, so it stays in the enforcement service and this
    # entry is the outer bound.
    Action.CASE_DECIDE: frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    Action.SEIZURE_RECORD: frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    Action.CUSTODY_TRANSFER: frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    Action.REPORT_PROGRAMME: frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    # A manufacturer may see the score held against it; the risk service confines a
    # requester to its own company.
    Action.REPORT_RISK: frozenset({Role.REQUESTER, Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    Action.EXPORT_REGULATOR: frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN}),
    Action.EXPORT_PORTABILITY: frozenset({Role.REQUESTER, Role.ADMIN}),
    Action.CHECKPOINT_PUBLISH: frozenset({Role.SUPERVISOR, Role.ADMIN}),
    Action.OFFLINE_BUNDLE_PUBLISH: frozenset({Role.SUPERVISOR, Role.OPERATOR, Role.ADMIN}),
    Action.OFFLINE_BUNDLE_READ: frozenset({Role.DEVICE, Role.OPERATOR, *_ALL_STAFF}),
    Action.OFFLINE_SCAN_SUBMIT: frozenset({Role.DEVICE, Role.OPERATOR, Role.ADMIN}),
    Action.OPS_METRICS_READ: frozenset({Role.ADMIN, Role.AUDITOR}),
    Action.OPS_RECONCILE: frozenset({Role.ADMIN, Role.AUDITOR}),
    Action.OPS_AUDIT_READ: frozenset({Role.ADMIN, Role.AUDITOR}),
}

#: Actions a delegated, cross-tenant reader may be granted by the external engine. Only
#: reads: delegation never confers the ability to change another tenant's records.
DELEGABLE_ACTIONS: Final[frozenset[Action]] = frozenset(
    {
        Action.STAMP_READ,
        Action.BATCH_READ,
        Action.REPORT_RISK,
        Action.EXPORT_REGULATOR,
    }
)


def locally_permitted(action: Action, role: Role) -> bool:
    return role in LOCAL_RULES.get(action, frozenset())

"""Policy decisions: the local table first, an external engine second.

Ordering is the whole design:

1. The local role table (:mod:`taxstamp.authz.actions`) decides first. A role that the
   table does not permit is refused without consulting anything external, so no engine
   configuration can escalate a privilege.
2. When the engine is *enforcing*, a locally-permitted action must also be confirmed by
   the engine, and an engine that cannot answer refuses the request. That is what makes
   the enforcing mode fail closed: an outage denies, it never admits.
3. When the engine is in *shadow* mode the local decision stands and disagreements are
   counted, which is how a policy change is proven safe before it is enforced.
4. Delegated cross-tenant reads are the one case where the engine may permit what the
   local table alone would refuse, and only for read actions on an explicit relationship.

Only actions this module maps to a question in ``deploy/identity/permify-schema.perm``
are put to the engine, and only when the question is answerable for this subject. An
unmapped action - or a company-scoped question asked by a principal with no company - is
decided locally even in enforcing mode: asking a schema a question it does not define
yields "unknown", and treating that as a denial would take the whole API down the moment
the engine was enforced, which is a worse failure than the one it was meant to prevent.
An "unknown" from a question the engine *should* have answered is a different thing, and
that one does deny.

Every decision records which of these paths produced it, so an access review can tell a
role-based grant from a delegated one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from taxstamp.authz.actions import DELEGABLE_ACTIONS, Action, locally_permitted
from taxstamp.authz.permify import CheckOutcome, CheckRequest, PermifyClient
from taxstamp.enums import Role
from taxstamp.errors import DependencyUnavailable, Forbidden
from taxstamp.observability import Metrics

_ENTITY_COMPANY = "company"
_ENTITY_PROGRAMME = "programme"
_SUBJECT_USER = "user"
#: The single programme-wide entity instance; the schema models one programme.
_PROGRAMME_ID = "programme"

#: Actions the schema answers about a specific company, and the permission to ask for.
_COMPANY_PERMISSIONS: dict[Action, str] = {
    Action.STAMP_READ: "read_stamps",
    Action.BATCH_READ: "read_batches",
    Action.REPORT_RISK: "read_risk",
    Action.EXPORT_REGULATOR: "export",
}

#: Actions the schema answers about the programme as a whole.
_PROGRAMME_PERMISSIONS: dict[Action, str] = {
    Action.REPORT_PROGRAMME: "report",
}


def company_permissions() -> dict[Action, str]:
    """The company-scoped questions this platform puts to the policy engine."""
    return dict(_COMPANY_PERMISSIONS)


def programme_permissions() -> dict[Action, str]:
    """The programme-scoped questions this platform puts to the policy engine."""
    return dict(_PROGRAMME_PERMISSIONS)


class ExternalMode(StrEnum):
    """How much authority the external engine holds."""

    #: The engine is not consulted at all.
    DISABLED = "disabled"
    #: The engine is consulted and disagreements recorded; the local table decides.
    SHADOW = "shadow"
    #: The engine must confirm every locally-permitted action, and may grant delegation.
    ENFORCING = "enforcing"


class DecisionSource(StrEnum):
    LOCAL_ROLE = "local_role"
    LOCAL_ROLE_CONFIRMED = "local_role_confirmed"
    DELEGATED_RELATIONSHIP = "delegated_relationship"


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    source: DecisionSource | None
    reason: str


@dataclass(slots=True)
class PolicyEngine:
    client: PermifyClient
    mode: ExternalMode
    metrics: Metrics

    @property
    def external_active(self) -> bool:
        return self.mode is not ExternalMode.DISABLED and self.client.config.configured

    def decide(
        self,
        *,
        action: Action,
        role: Role,
        subject_id: uuid.UUID,
        company_id: uuid.UUID | None,
    ) -> Decision:
        """Decide a single action. Pure of side effects other than metrics."""
        local = locally_permitted(action, role)
        if not self.external_active:
            return _local_decision(local, action, role)
        question = _question(action, subject_id, company_id)
        if question is None:
            return _local_decision(local, action, role)

        if local:
            return self._confirm(question, action=action, role=role)

        if self.mode is ExternalMode.ENFORCING and action in DELEGABLE_ACTIONS:
            delegated = self._delegated(subject_id, company_id)
            if delegated is CheckOutcome.ALLOWED:
                self.metrics["authz_delegated_grants"].labels(action=action.value).inc()
                return Decision(True, DecisionSource.DELEGATED_RELATIONSHIP, "delegated read access")
        return _local_decision(local, action, role)

    def _confirm(self, question: CheckRequest, *, action: Action, role: Role) -> Decision:
        """Put a locally-permitted action to the engine, in the mode's terms."""
        outcome = self.client.check(question)
        if self.mode is ExternalMode.SHADOW:
            if outcome is CheckOutcome.DENIED:
                self.metrics["authz_shadow_disagreements"].labels(action=action.value).inc()
            return _local_decision(True, action, role)
        if outcome is CheckOutcome.ALLOWED:
            return Decision(True, DecisionSource.LOCAL_ROLE_CONFIRMED, "confirmed by policy engine")
        if outcome is CheckOutcome.DENIED:
            self.metrics["authz_external_denials"].labels(action=action.value).inc()
            return Decision(False, None, "refused by policy engine")
        self.metrics["authz_engine_unavailable"].labels(action=action.value).inc()
        raise DependencyUnavailable("authorisation policy engine is unavailable")

    def authorize(
        self,
        *,
        action: Action,
        role: Role,
        subject_id: uuid.UUID,
        company_id: uuid.UUID | None,
    ) -> Decision:
        """Decide, and refuse the request when the decision is a denial."""
        decision = self.decide(
            action=action,
            role=role,
            subject_id=subject_id,
            company_id=company_id,
        )
        if not decision.allowed:
            raise Forbidden(
                "this credential may not perform this action",
                detail={"action": action.value, "reason": decision.reason},
            )
        return decision

    def _delegated(self, subject_id: uuid.UUID, company_id: uuid.UUID | None) -> CheckOutcome:
        """Whether the engine grants this subject a delegated read on this company.

        A delegation is always about a named company: there is no programme-wide
        delegation, because that would be a role, and roles live in the local table.
        """
        if company_id is None:
            return CheckOutcome.DENIED
        request = _question(action=Action.STAMP_READ, subject_id=subject_id, company_id=company_id)
        if request is None:  # pragma: no cover - STAMP_READ is always mapped
            return CheckOutcome.DENIED
        return self.client.check(request)


def _question(
    action: Action,
    subject_id: uuid.UUID,
    company_id: uuid.UUID | None,
) -> CheckRequest | None:
    """The engine question for an action, or None when the schema models none.

    A company-scoped action asked by a principal with no company is asked of the
    programme instead when the schema models it there, and is otherwise unanswerable.
    """
    company_permission = _COMPANY_PERMISSIONS.get(action)
    if company_permission is not None and company_id is not None:
        return CheckRequest(
            entity_type=_ENTITY_COMPANY,
            entity_id=str(company_id),
            permission=company_permission,
            subject_type=_SUBJECT_USER,
            subject_id=str(subject_id),
        )
    programme_permission = _PROGRAMME_PERMISSIONS.get(action)
    if programme_permission is not None:
        return CheckRequest(
            entity_type=_ENTITY_PROGRAMME,
            entity_id=_PROGRAMME_ID,
            permission=programme_permission,
            subject_type=_SUBJECT_USER,
            subject_id=str(subject_id),
        )
    return None


def _local_decision(local: bool, action: Action, role: Role) -> Decision:
    if local:
        return Decision(True, DecisionSource.LOCAL_ROLE, "permitted by role")
    return Decision(
        False,
        None,
        f"role {role.value} is not permitted to {action.value}",
    )

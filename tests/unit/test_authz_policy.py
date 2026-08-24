"""Authorisation: the local table is the ceiling, the engine is never a bypass.

These tests are written against a real policy engine over a real socket, and each one
names the escalation or outage behaviour it forbids.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from prometheus_client import CollectorRegistry

from taxstamp.authz.actions import Action
from taxstamp.authz.permify import CheckOutcome, CheckRequest, PermifyClient, PermifyConfig
from taxstamp.authz.policy import DecisionSource, ExternalMode, PolicyEngine
from taxstamp.enums import Role
from taxstamp.errors import DependencyUnavailable, Forbidden
from taxstamp.observability import Metrics, build_metrics
from tests.support.permify_server import PermifySandbox

COMPANY = uuid.uuid4()
SUBJECT = uuid.uuid4()


@pytest.fixture
def engine() -> Iterator[PermifySandbox]:
    sandbox = PermifySandbox()
    sandbox.start()
    try:
        yield sandbox
    finally:
        sandbox.stop()


@pytest.fixture
def metrics() -> Metrics:
    return build_metrics(CollectorRegistry())


def _client(base_url: str, *, tenant: str = "taxstamp") -> PermifyClient:
    return PermifyClient(
        PermifyConfig(
            base_url=base_url,
            tenant_id=tenant,
            api_key="engine-key",
            timeout_seconds=2.0,
            schema_version="",
        )
    )


def _policy(sandbox: PermifySandbox, mode: ExternalMode, metrics: Metrics) -> PolicyEngine:
    return PolicyEngine(client=_client(sandbox.base_url), mode=mode, metrics=metrics)


def test_disabled_mode_never_calls_the_engine(engine: PermifySandbox, metrics: Metrics) -> None:
    policy = _policy(engine, ExternalMode.DISABLED, metrics)
    decision = policy.decide(
        action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY
    )
    assert decision.allowed
    assert decision.source is DecisionSource.LOCAL_ROLE
    assert engine.script.questions == []


def test_local_denial_cannot_be_elevated_by_the_engine(engine: PermifySandbox, metrics: Metrics) -> None:
    """A write the role table forbids stays forbidden however the engine answers."""
    engine.script.default_verdict = "RESULT_ALLOWED"
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    with pytest.raises(Forbidden):
        policy.authorize(
            action=Action.ORDER_APPROVE, role=Role.REQUESTER, subject_id=SUBJECT, company_id=COMPANY
        )


def test_shadow_mode_keeps_the_local_decision_and_counts_disagreement(
    engine: PermifySandbox, metrics: Metrics
) -> None:
    """Shadow mode is for proving a policy safe, so it must change no outcome."""
    engine.script.default_verdict = "RESULT_DENIED"
    policy = _policy(engine, ExternalMode.SHADOW, metrics)
    decision = policy.decide(
        action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY
    )
    assert decision.allowed
    assert decision.source is DecisionSource.LOCAL_ROLE
    assert engine.script.questions, "shadow mode must still consult the engine"
    assert _counter(metrics, "authz_shadow_disagreements", Action.STAMP_READ) == 1


def test_enforcing_mode_requires_confirmation(engine: PermifySandbox, metrics: Metrics) -> None:
    engine.allow(
        entity_type="company",
        entity_id=str(COMPANY),
        permission="read_stamps",
        subject_id=str(SUBJECT),
    )
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    decision = policy.decide(
        action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY
    )
    assert decision.allowed
    assert decision.source is DecisionSource.LOCAL_ROLE_CONFIRMED


def test_enforcing_mode_honours_an_engine_denial(engine: PermifySandbox, metrics: Metrics) -> None:
    """The engine may narrow a role, which is the point of externalising policy."""
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    with pytest.raises(Forbidden):
        policy.authorize(action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY)
    assert _counter(metrics, "authz_external_denials", Action.STAMP_READ) == 1


@pytest.mark.parametrize(
    ("status", "raw_body"),
    [
        (503, None),
        (500, None),
        (200, "not json at all"),
        (200, "[]"),
        (200, '{"can": 1}'),
        (200, '{"can": "RESULT_MAYBE"}'),
        (200, "{}"),
    ],
)
def test_enforcing_mode_fails_closed_on_any_unusable_answer(
    engine: PermifySandbox, metrics: Metrics, status: int, raw_body: str | None
) -> None:
    """An engine that cannot answer must deny, not admit.

    Every one of these is a way a real engine can misbehave - an outage, a proxy error
    page, a schema drift producing an unknown enum. None of them may be read as an allow.
    """
    engine.script.status = status
    engine.script.raw_body = raw_body
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    with pytest.raises(DependencyUnavailable):
        policy.authorize(action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY)
    assert _counter(metrics, "authz_engine_unavailable", Action.STAMP_READ) == 1


def test_unreachable_engine_fails_closed(metrics: Metrics) -> None:
    policy = PolicyEngine(client=_client("http://127.0.0.1:1"), mode=ExternalMode.ENFORCING, metrics=metrics)
    with pytest.raises(DependencyUnavailable):
        policy.authorize(action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY)


def test_unreachable_engine_does_not_break_shadow_mode(metrics: Metrics) -> None:
    """Shadow mode is meant to be safe to switch on, including when the engine is down."""
    policy = PolicyEngine(client=_client("http://127.0.0.1:1"), mode=ExternalMode.SHADOW, metrics=metrics)
    decision = policy.decide(
        action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY
    )
    assert decision.allowed


def test_delegated_read_is_granted_only_by_an_explicit_relationship(
    engine: PermifySandbox, metrics: Metrics
) -> None:
    """A role with no local read may still read where a delegation is modelled."""
    other_company = uuid.uuid4()
    engine.allow(
        entity_type="company",
        entity_id=str(other_company),
        permission="read_stamps",
        subject_id=str(SUBJECT),
    )
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    granted = policy.decide(
        action=Action.BATCH_READ,
        role=Role.SUPERVISOR,
        subject_id=SUBJECT,
        company_id=other_company,
    )
    assert granted.allowed
    assert granted.source is DecisionSource.DELEGATED_RELATIONSHIP
    assert _counter(metrics, "authz_delegated_grants", Action.BATCH_READ) == 1

    refused = policy.decide(
        action=Action.BATCH_READ,
        role=Role.SUPERVISOR,
        subject_id=SUBJECT,
        company_id=uuid.uuid4(),
    )
    assert not refused.allowed


def test_delegation_never_grants_a_write(engine: PermifySandbox, metrics: Metrics) -> None:
    """Delegation is read-only; no engine relationship may confer a write."""
    engine.script.default_verdict = "RESULT_ALLOWED"
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    for action in (Action.ORDER_APPROVE, Action.CUSTOMS_RELEASE, Action.TREASURY_REFUND):
        with pytest.raises(Forbidden):
            policy.authorize(action=action, role=Role.REQUESTER, subject_id=SUBJECT, company_id=COMPANY)


def test_unmodelled_actions_stay_local_even_when_enforcing(engine: PermifySandbox, metrics: Metrics) -> None:
    """Enforcing a schema that says nothing about an action must not deny that action.

    Otherwise switching the engine on would take down every route the schema does not
    describe, which is a self-inflicted outage rather than a security control.
    """
    engine.script.status = 503
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    decision = policy.decide(
        action=Action.ORDER_CREATE, role=Role.REQUESTER, subject_id=SUBJECT, company_id=COMPANY
    )
    assert decision.allowed
    assert decision.source is DecisionSource.LOCAL_ROLE
    assert engine.script.questions == []


def test_company_scoped_question_from_a_companyless_principal_stays_local(
    engine: PermifySandbox, metrics: Metrics
) -> None:
    """A regulator has no company, so a company-scoped check is unanswerable, not denied."""
    engine.script.status = 503
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    decision = policy.decide(action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=None)
    assert decision.allowed
    assert engine.script.questions == []


def test_programme_questions_are_asked_of_the_programme_entity(
    engine: PermifySandbox, metrics: Metrics
) -> None:
    engine.allow(
        entity_type="programme",
        entity_id="programme",
        permission="report",
        subject_id=str(SUBJECT),
    )
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    decision = policy.decide(
        action=Action.REPORT_PROGRAMME, role=Role.SUPERVISOR, subject_id=SUBJECT, company_id=None
    )
    assert decision.allowed
    assert decision.source is DecisionSource.LOCAL_ROLE_CONFIRMED


def test_engine_is_asked_with_its_credential_and_only_once(engine: PermifySandbox, metrics: Metrics) -> None:
    """One check is one request: an authorisation path must not retry a failing engine."""
    engine.script.status = 503
    policy = _policy(engine, ExternalMode.ENFORCING, metrics)
    with pytest.raises(DependencyUnavailable):
        policy.authorize(action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY)
    assert len(engine.script.questions) == 1
    assert engine.script.authorizations == ["Bearer engine-key"]


def test_unconfigured_engine_is_inert(metrics: Metrics) -> None:
    """A half-configured engine must neither be consulted nor break the platform."""
    client = PermifyClient(
        PermifyConfig(base_url="", tenant_id="", api_key="", timeout_seconds=1.0, schema_version="")
    )
    policy = PolicyEngine(client=client, mode=ExternalMode.ENFORCING, metrics=metrics)
    assert not policy.external_active
    decision = policy.decide(
        action=Action.STAMP_READ, role=Role.AUDITOR, subject_id=SUBJECT, company_id=COMPANY
    )
    assert decision.allowed
    assert (
        client.check(
            CheckRequest(
                entity_type="company",
                entity_id=str(COMPANY),
                permission="read_stamps",
                subject_type="user",
                subject_id=str(SUBJECT),
            )
        )
        is CheckOutcome.UNKNOWN
    )


def _counter(metrics: Metrics, name: str, action: Action) -> float:
    value = metrics[name].labels(action=action.value)._value.get()  # noqa: SLF001
    return float(value)

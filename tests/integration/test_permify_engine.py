"""The policy engine integration, exercised against a real Permify server.

The unit tests in ``tests/unit/test_authz_policy.py`` drive a scripted HTTP sandbox and
prove the decision order. What they cannot prove is that the schema this repository ships
is accepted by Permify, that the check payload matches the real API, and that a
relationship tuple written to the engine actually produces the delegated grant the
platform expects. That needs the real engine, so this module is skipped unless one is
reachable::

    docker compose --profile edge up -d permify
    TAXSTAMP_PERMIFY_BASE_URL=http://127.0.0.1:3476 pytest tests/integration/test_permify_engine.py
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import httpx
import pytest
from prometheus_client import CollectorRegistry

from taxstamp.authz.actions import Action
from taxstamp.authz.permify import PermifyClient, PermifyConfig
from taxstamp.authz.policy import DecisionSource, ExternalMode, PolicyEngine
from taxstamp.enums import Role
from taxstamp.observability import build_metrics

BASE_URL = os.environ.get("TAXSTAMP_PERMIFY_BASE_URL", "")
SCHEMA = Path(__file__).resolve().parents[2] / "deploy" / "identity" / "permify-schema.perm"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not BASE_URL, reason="no Permify engine configured"),
]


@pytest.fixture(scope="module")
def tenant() -> str:
    """A fresh tenant carrying this repository's schema."""
    name = f"test-{uuid.uuid4().hex[:12]}"
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        created = client.post("/v1/tenants/create", json={"id": name, "name": name})
        created.raise_for_status()
        written = client.post(
            f"/v1/tenants/{name}/schemas/write",
            json={"schema": SCHEMA.read_text()},
        )
        assert written.status_code == 200, written.text
        assert written.json()["schema_version"], "the shipped schema was not accepted"
    return name


def _engine(tenant: str, mode: ExternalMode) -> PolicyEngine:
    client = PermifyClient(
        PermifyConfig(
            base_url=BASE_URL,
            tenant_id=tenant,
            api_key="",
            timeout_seconds=5.0,
            schema_version="",
        )
    )
    return PolicyEngine(client=client, mode=mode, metrics=build_metrics(CollectorRegistry()))


def _grant(tenant: str, *, company_id: uuid.UUID, relation: str, user_id: uuid.UUID) -> None:
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        response = client.post(
            f"/v1/tenants/{tenant}/data/write",
            json={
                "metadata": {"schema_version": ""},
                "tuples": [
                    {
                        "entity": {"type": "company", "id": str(company_id)},
                        "relation": relation,
                        "subject": {"type": "user", "id": str(user_id)},
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text


def test_a_delegated_reader_may_read_another_tenants_stamps(tenant: str) -> None:
    """The one case where the engine may grant what the local role table would not."""
    engine = _engine(tenant, ExternalMode.ENFORCING)
    auditor, company = uuid.uuid4(), uuid.uuid4()
    _grant(tenant, company_id=company, relation="delegated_reader", user_id=auditor)

    decision = engine.decide(
        action=Action.STAMP_READ,
        role=Role.AUDITOR,
        subject_id=auditor,
        company_id=company,
    )
    assert decision.allowed
    assert decision.source is DecisionSource.LOCAL_ROLE_CONFIRMED


def test_delegation_is_scoped_to_the_company_it_was_granted_on(tenant: str) -> None:
    engine = _engine(tenant, ExternalMode.ENFORCING)
    auditor = uuid.uuid4()
    _grant(tenant, company_id=uuid.uuid4(), relation="delegated_reader", user_id=auditor)

    decision = engine.decide(
        action=Action.STAMP_READ,
        role=Role.AUDITOR,
        subject_id=auditor,
        company_id=uuid.uuid4(),
    )
    assert not decision.allowed


def test_delegation_never_confers_a_write(tenant: str) -> None:
    """A delegated reader of a company may not order stamps for it."""
    engine = _engine(tenant, ExternalMode.ENFORCING)
    reader, company = uuid.uuid4(), uuid.uuid4()
    _grant(tenant, company_id=company, relation="delegated_reader", user_id=reader)

    decision = engine.decide(
        action=Action.ORDER_CREATE,
        role=Role.AUDITOR,
        subject_id=reader,
        company_id=company,
    )
    assert not decision.allowed


def test_an_ungranted_subject_is_refused_even_with_a_permitted_role(tenant: str) -> None:
    """Enforcing mode narrows: the role is necessary, the relationship is also necessary."""
    engine = _engine(tenant, ExternalMode.ENFORCING)
    decision = engine.decide(
        action=Action.STAMP_READ,
        role=Role.AUDITOR,
        subject_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
    )
    assert not decision.allowed
    assert decision.reason == "refused by policy engine"


def test_shadow_mode_keeps_the_local_decision(tenant: str) -> None:
    """A real engine disagreeing must not change behaviour until it is enforced."""
    engine = _engine(tenant, ExternalMode.SHADOW)
    decision = engine.decide(
        action=Action.STAMP_READ,
        role=Role.AUDITOR,
        subject_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
    )
    assert decision.allowed
    assert decision.source is DecisionSource.LOCAL_ROLE

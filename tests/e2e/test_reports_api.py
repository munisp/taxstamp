"""KPIs, revenue at risk and risk scores: counted from records, never extrapolated."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.runtime import Runtime
from taxstamp.services.risk import HIGH_FROM, MEDIUM_FROM, MODEL_VERSION, WEIGHTS
from tests.support.api import auth, new_key
from tests.support.factories import create_company
from tests.support.issuance import issue_stamps
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e


def _window(clock: FixedClock, *, days: int = 30) -> dict[str, str]:
    return {
        "start": (clock.now() - dt.timedelta(days=days)).isoformat(),
        "end": (clock.now() + dt.timedelta(minutes=1)).isoformat(),
    }


def test_kpis_count_issuance_activation_and_collection(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    serials = issue_stamps(
        client,
        runtime=runtime,
        settings=settings,
        clock=clock,
        tenant=tenant,
        session_factory=session_factory,
        quantity=5,
    )
    activated = client.post(
        "/v1/stamps/activate",
        json={"serials": serials[:3]},
        headers=auth(tenant.operator.token, new_key("activate")),
    )
    assert activated.status_code == 200, activated.text
    voided = client.post(
        "/v1/stamps/void",
        json={"serials": [serials[4]], "reason": "Destroyed by a printing fault on the line"},
        headers=auth(tenant.operator.token, new_key("void")),
    )
    assert voided.status_code == 200, voided.text

    response = client.get("/v1/reports/kpis", params=_window(clock), headers=auth(tenant.supervisor.token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stamps"] == {"issued": 5, "activated": 3, "voided": 1}
    net = 5 * tenant.unit_price_minor
    vat = (net * tenant.vat_bps + 5_000) // 10_000
    assert body["revenue"]["collected_minor"] == net + vat
    assert body["revenue"]["orders_paid_or_issued"] == 1
    assert "matched payment receipts" in body["revenue"]["basis"]


def test_a_window_excludes_its_end_instant(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    """Half-open windows: the same instant cannot be counted by two adjacent reports."""
    issue_stamps(
        client,
        runtime=runtime,
        settings=settings,
        clock=clock,
        tenant=tenant,
        session_factory=session_factory,
        quantity=2,
    )
    ending_now = client.get(
        "/v1/reports/kpis",
        params={"start": (clock.now() - dt.timedelta(days=1)).isoformat(), "end": clock.now().isoformat()},
        headers=auth(tenant.supervisor.token),
    )
    starting_now = client.get(
        "/v1/reports/kpis",
        params={"start": clock.now().isoformat(), "end": (clock.now() + dt.timedelta(days=1)).isoformat()},
        headers=auth(tenant.supervisor.token),
    )
    assert ending_now.status_code == 200, ending_now.text
    assert starting_now.status_code == 200, starting_now.text
    assert ending_now.json()["stamps"]["issued"] == 0
    assert starting_now.json()["stamps"]["issued"] == 2


def test_an_inverted_window_is_refused(client: TestClient, clock: FixedClock, tenant: Tenant) -> None:
    response = client.get(
        "/v1/reports/kpis",
        params={"start": clock.now().isoformat(), "end": (clock.now() - dt.timedelta(days=1)).isoformat()},
        headers=auth(tenant.supervisor.token),
    )
    assert response.status_code == 422, response.text


def test_an_excessive_window_is_refused(client: TestClient, clock: FixedClock, tenant: Tenant) -> None:
    response = client.get(
        "/v1/reports/kpis",
        params={
            "start": (clock.now() - dt.timedelta(days=500)).isoformat(),
            "end": clock.now().isoformat(),
        },
        headers=auth(tenant.supervisor.token),
    )
    assert response.status_code == 422, response.text


def test_an_industry_user_cannot_read_programme_reports(client: TestClient, tenant: Tenant) -> None:
    for path in ("/v1/reports/kpis", "/v1/reports/revenue-at-risk"):
        response = client.get(path, headers=auth(tenant.requester.token))
        assert response.status_code == 403, response.text


def test_revenue_at_risk_is_itemised_and_caveated(
    client: TestClient, clock: FixedClock, tenant: Tenant
) -> None:
    opened = client.post(
        "/v1/cases",
        json={
            "case_ref": "CASE-RAR",
            "kind": "unstamped_goods",
            "severity": "high",
            "summary": "Unstamped cartons found on sale in a licensed outlet",
            "company_id": str(tenant.company.id),
            "product_category": "alcohol",
        },
        headers=auth(tenant.analyst.token, new_key("case")),
    )
    assert opened.status_code == 201, opened.text
    seized = client.post(
        "/v1/cases/CASE-RAR/seizures",
        json={
            "seizure_ref": "SEIZ-RAR",
            "location": "Alaba International Market, Lagos",
            "description": "Two hundred unstamped cartons taken into custody",
            "product_category": "alcohol",
            "seized_quantity": 200,
            "custodian": "Inspector A. Bello",
            "seized_at": clock.now().isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("seizure")),
    )
    assert seized.status_code == 201, seized.text

    response = client.get(
        "/v1/reports/revenue-at-risk", params=_window(clock), headers=auth(tenant.supervisor.token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    components = {component["source"]: component for component in body["components"]}
    exposure = 200 * tenant.unit_price_minor
    assert components["goods_in_custody"]["amount_minor"] == exposure
    assert components["goods_in_custody"]["observations"] == 1
    assert components["open_enforcement_cases"]["amount_minor"] == exposure
    assert "total_minor" not in body
    assert "no extrapolation" in body["caveat"]
    assert "not netted" in body["caveat"]


def test_released_goods_leave_the_exposure(client: TestClient, clock: FixedClock, tenant: Tenant) -> None:
    client.post(
        "/v1/cases",
        json={
            "case_ref": "CASE-REL",
            "kind": "quantity_discrepancy",
            "severity": "medium",
            "summary": "A quantity discrepancy later explained by the importer's records",
            "company_id": str(tenant.company.id),
            "product_category": "alcohol",
        },
        headers=auth(tenant.analyst.token, new_key("case")),
    )
    client.post(
        "/v1/cases/CASE-REL/seizures",
        json={
            "seizure_ref": "SEIZ-REL",
            "location": "Apapa Port, Lagos",
            "description": "Cartons detained pending production of duty documents",
            "product_category": "alcohol",
            "seized_quantity": 50,
            "custodian": "Inspector A. Bello",
            "seized_at": clock.now().isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("seizure")),
    )
    client.post(
        "/v1/seizures/SEIZ-REL/settlement",
        json={"status": "released", "reason": "Duty documents produced and verified"},
        headers=auth(tenant.supervisor.token, new_key("settle")),
    )
    response = client.get(
        "/v1/reports/revenue-at-risk", params=_window(clock), headers=auth(tenant.supervisor.token)
    )
    assert response.status_code == 200, response.text
    components = {item["source"]: item for item in response.json()["components"]}
    assert components["goods_in_custody"]["amount_minor"] == 0


def test_a_clean_company_scores_low_and_explains_every_factor(client: TestClient, tenant: Tenant) -> None:
    response = client.get(f"/v1/reports/risk/{tenant.company.id}", headers=auth(tenant.supervisor.token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] == 0
    assert body["tier"] == "low"
    assert body["model_version"] == MODEL_VERSION
    assert {item["factor"] for item in body["contributions"]} == set(WEIGHTS)
    assert all(item["explanation"] for item in body["contributions"])
    assert "no learned parameters" in body["method"]


def test_a_suspended_licence_raises_the_score_by_its_stated_weight(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    suspend = client.post(
        f"/v1/licences/{tenant.licence.id}/status",
        json={"status": "suspended", "reason": "Outstanding excise returns for two periods"},
        headers=auth(tenant.admin.token, new_key("suspend")),
    )
    assert suspend.status_code == 200, suspend.text
    body = client.get(f"/v1/reports/risk/{tenant.company.id}", headers=auth(tenant.supervisor.token)).json()
    weight, _cap = WEIGHTS["licence_not_active"]
    assert body["score"] == weight
    licence_factor = next(item for item in body["contributions"] if item["factor"] == "licence_not_active")
    assert licence_factor["observations"] == 1
    assert licence_factor["points"] == weight
    assert body["tier"] == ("medium" if MEDIUM_FROM <= weight < HIGH_FROM else body["tier"])


def test_the_same_evidence_always_produces_the_same_score(client: TestClient, tenant: Tenant) -> None:
    first = client.get(f"/v1/reports/risk/{tenant.company.id}", headers=auth(tenant.supervisor.token)).json()
    second = client.get(f"/v1/reports/risk/{tenant.company.id}", headers=auth(tenant.supervisor.token)).json()
    assert first == second


def test_a_company_may_not_read_another_company_risk_score(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        other = create_company(session)
        session.commit()
        other_id = other.id
    own = client.get(f"/v1/reports/risk/{tenant.company.id}", headers=auth(tenant.requester.token))
    assert own.status_code == 200, own.text
    across = client.get(f"/v1/reports/risk/{other_id}", headers=auth(tenant.requester.token))
    assert across.status_code == 403, across.text

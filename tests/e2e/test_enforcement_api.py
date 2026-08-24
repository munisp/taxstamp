"""Cases, seizures and chain of custody: authority is split and evidence is chained."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import CustodyTransfer, Seizure
from taxstamp.runtime import Runtime
from taxstamp.services import enforcement as enforcement_service
from tests.support.api import auth, new_key
from tests.support.issuance import issue_stamps
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e


def _open_case(client: TestClient, tenant: Tenant, *, case_ref: str = "CASE-0001") -> dict[str, object]:
    response = client.post(
        "/v1/cases",
        json={
            "case_ref": case_ref,
            "kind": "counterfeit",
            "severity": "high",
            "summary": "Counterfeit stamps recovered during a market inspection in Lagos",
            "company_id": str(tenant.company.id),
            "product_category": "alcohol",
        },
        headers=auth(tenant.analyst.token, new_key("case")),
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def _record_seizure(
    client: TestClient,
    tenant: Tenant,
    clock: FixedClock,
    *,
    case_ref: str = "CASE-0001",
    seizure_ref: str = "SEIZ-0001",
    quantity: int = 400,
) -> dict[str, object]:
    response = client.post(
        f"/v1/cases/{case_ref}/seizures",
        json={
            "seizure_ref": seizure_ref,
            "location": "Alaba International Market, Lagos",
            "description": "Cartons bearing duplicated stamp serials taken into custody",
            "product_category": "alcohol",
            "seized_quantity": quantity,
            "custodian": "Inspector A. Bello",
            "seized_at": (clock.now() - dt.timedelta(hours=2)).isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("seizure")),
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_a_case_opens_and_reads_back(client: TestClient, tenant: Tenant) -> None:
    opened = _open_case(client, tenant)
    assert opened["status"] == "open"
    read = client.get("/v1/cases/CASE-0001", headers=auth(tenant.analyst.token))
    assert read.status_code == 200, read.text
    assert read.json()["case_ref"] == "CASE-0001"


def test_a_duplicate_case_reference_is_refused(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases",
        json={
            "case_ref": "CASE-0001",
            "kind": "diversion",
            "severity": "low",
            "summary": "A second case claiming the same reference as an existing one",
        },
        headers=auth(tenant.analyst.token, new_key("case")),
    )
    assert response.status_code == 409, response.text


def test_a_requester_may_not_open_a_case(client: TestClient, tenant: Tenant) -> None:
    response = client.post(
        "/v1/cases",
        json={
            "case_ref": "CASE-0002",
            "kind": "diversion",
            "severity": "low",
            "summary": "An industry user attempting to raise an enforcement case",
        },
        headers=auth(tenant.requester.token, new_key("case")),
    )
    assert response.status_code == 403, response.text


def test_evidence_must_point_at_a_real_record(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases/CASE-0001/evidence",
        json={"kind": "stamp", "reference": "NG-ALC-2026-00000000"},
        headers=auth(tenant.analyst.token, new_key("evidence")),
    )
    assert response.status_code == 404, response.text


def test_a_witness_statement_needs_no_matching_record(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases/CASE-0001/evidence",
        json={
            "kind": "statement",
            "reference": "STMT-1",
            "detail": {"witness": "shopkeeper", "note": "bought the cartons from a van"},
        },
        headers=auth(tenant.analyst.token, new_key("evidence")),
    )
    assert response.status_code == 201, response.text
    assert response.json()["evidence"][0]["kind"] == "statement"


def test_a_real_stamp_can_be_attached_as_evidence(
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
        quantity=1,
    )
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases/CASE-0001/evidence",
        json={"kind": "stamp", "reference": serials[0]},
        headers=auth(tenant.analyst.token, new_key("evidence")),
    )
    assert response.status_code == 201, response.text


def test_the_officer_who_opened_a_case_may_not_close_it(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    supervisor_case = client.post(
        "/v1/cases",
        json={
            "case_ref": "CASE-SUP",
            "kind": "diversion",
            "severity": "medium",
            "summary": "A case opened by the supervisor who would then also close it",
        },
        headers=auth(tenant.supervisor.token, new_key("case")),
    )
    assert supervisor_case.status_code == 201, supervisor_case.text
    response = client.post(
        "/v1/cases/CASE-SUP/decision",
        json={"status": "closed_unsubstantiated", "reason": "No evidence of any breach was found"},
        headers=auth(tenant.supervisor.token, new_key("decide")),
    )
    assert response.status_code == 403, response.text


def test_an_analyst_may_not_close_a_case(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "closed_substantiated", "reason": "Counterfeiting confirmed by inspection"},
        headers=auth(tenant.analyst.token, new_key("decide")),
    )
    assert response.status_code == 403, response.text


def test_a_supervisor_closes_a_case_another_officer_opened(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "closed_substantiated", "reason": "Counterfeiting confirmed by laboratory"},
        headers=auth(tenant.supervisor.token, new_key("decide")),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed_substantiated"


def test_a_closed_case_cannot_be_reopened_or_amended(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "closed_unsubstantiated", "reason": "The complaint could not be substantiated"},
        headers=auth(tenant.supervisor.token, new_key("decide")),
    )
    reopen = client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "under_investigation", "reason": "An officer attempting to reopen the case"},
        headers=auth(tenant.analyst.token, new_key("decide")),
    )
    assert reopen.status_code == 409, reopen.text
    evidence = client.post(
        "/v1/cases/CASE-0001/evidence",
        json={"kind": "statement", "reference": "STMT-LATE"},
        headers=auth(tenant.analyst.token, new_key("evidence")),
    )
    assert evidence.status_code == 409, evidence.text


def test_referral_requires_an_investigation_first(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    straight_to_referral = client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "referred_for_prosecution", "reason": "Referred without any investigation"},
        headers=auth(tenant.supervisor.token, new_key("decide")),
    )
    assert straight_to_referral.status_code == 409, straight_to_referral.text
    client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "under_investigation", "reason": "Assigned to the enforcement unit"},
        headers=auth(tenant.analyst.token, new_key("decide")),
    )
    referred = client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "referred_for_prosecution", "reason": "Evidence supports prosecution"},
        headers=auth(tenant.supervisor.token, new_key("decide")),
    )
    assert referred.status_code == 200, referred.text


def test_a_seizure_prices_duty_from_the_tariff_and_starts_a_custody_chain(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    document = _record_seizure(client, tenant, clock, quantity=400)
    seizure = document["seizures"][0]  # type: ignore[index]
    assert seizure["estimated_duty_minor"] == tenant.unit_price_minor * 400
    assert seizure["status"] == "held"
    assert seizure["custody"][0]["to_custodian"] == "Inspector A. Bello"
    assert seizure["custody_chain_intact"] is True
    assert document["revenue_at_risk_minor"] == tenant.unit_price_minor * 400


def test_a_seizure_cannot_predate_nothing_or_postdate_now(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases/CASE-0001/seizures",
        json={
            "seizure_ref": "SEIZ-FUTURE",
            "location": "Apapa Port, Lagos",
            "description": "A seizure recorded as happening tomorrow",
            "product_category": "alcohol",
            "seized_quantity": 10,
            "custodian": "Inspector A. Bello",
            "seized_at": (clock.now() + dt.timedelta(days=1)).isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("seizure")),
    )
    assert response.status_code == 422, response.text


def test_an_unknown_facility_is_refused(client: TestClient, tenant: Tenant, clock: FixedClock) -> None:
    _open_case(client, tenant)
    response = client.post(
        "/v1/cases/CASE-0001/seizures",
        json={
            "seizure_ref": "SEIZ-NOWHERE",
            "location": "Unknown yard",
            "description": "A seizure held at a facility the registry does not know",
            "product_category": "alcohol",
            "seized_quantity": 10,
            "custodian": "Inspector A. Bello",
            "seized_at": clock.now().isoformat(),
            "facility_code": "FAC-DOES-NOT-EXIST",
        },
        headers=auth(tenant.analyst.token, new_key("seizure")),
    )
    assert response.status_code == 404, response.text


def test_custody_passes_from_the_current_custodian_only(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    wrong_source = client.post(
        "/v1/seizures/SEIZ-0001/custody",
        json={
            "from_custodian": "Someone Else",
            "to_custodian": "Evidence Store, Ikeja",
            "location": "Ikeja",
            "reason": "transfer to the evidence store",
            "evidence_reference": "HANDOVER-1",
            "occurred_at": clock.now().isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("custody")),
    )
    assert wrong_source.status_code == 409, wrong_source.text
    accepted = client.post(
        "/v1/seizures/SEIZ-0001/custody",
        json={
            "from_custodian": "Inspector A. Bello",
            "to_custodian": "Evidence Store, Ikeja",
            "location": "Ikeja",
            "reason": "transfer to the evidence store",
            "evidence_reference": "HANDOVER-1",
            "occurred_at": clock.now().isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("custody")),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["sequence"] == 2


def test_a_handover_cannot_precede_the_previous_one(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    response = client.post(
        "/v1/seizures/SEIZ-0001/custody",
        json={
            "from_custodian": "Inspector A. Bello",
            "to_custodian": "Evidence Store, Ikeja",
            "location": "Ikeja",
            "reason": "backdated handover",
            "evidence_reference": "HANDOVER-BACK",
            "occurred_at": (clock.now() - dt.timedelta(days=2)).isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("custody")),
    )
    assert response.status_code == 422, response.text


def test_custody_stops_once_goods_leave_custody(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    released = client.post(
        "/v1/seizures/SEIZ-0001/settlement",
        json={"status": "released", "reason": "Documentation produced showing duty was paid"},
        headers=auth(tenant.supervisor.token, new_key("settle")),
    )
    assert released.status_code == 200, released.text
    response = client.post(
        "/v1/seizures/SEIZ-0001/custody",
        json={
            "from_custodian": "Inspector A. Bello",
            "to_custodian": "Evidence Store, Ikeja",
            "location": "Ikeja",
            "reason": "handover after release",
            "evidence_reference": "HANDOVER-2",
            "occurred_at": clock.now().isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("custody")),
    )
    assert response.status_code == 409, response.text


def test_released_goods_cannot_then_be_destroyed(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    client.post(
        "/v1/seizures/SEIZ-0001/settlement",
        json={"status": "released", "reason": "Documentation produced showing duty was paid"},
        headers=auth(tenant.supervisor.token, new_key("settle")),
    )
    response = client.post(
        "/v1/seizures/SEIZ-0001/settlement",
        json={"status": "destroyed", "reason": "Attempting to destroy goods already returned"},
        headers=auth(tenant.supervisor.token, new_key("settle")),
    )
    assert response.status_code == 409, response.text


def test_an_analyst_may_not_settle_a_seizure(client: TestClient, tenant: Tenant, clock: FixedClock) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    response = client.post(
        "/v1/seizures/SEIZ-0001/settlement",
        json={"status": "forfeited", "reason": "An investigator attempting to forfeit goods"},
        headers=auth(tenant.analyst.token, new_key("settle")),
    )
    assert response.status_code == 403, response.text


def test_a_case_cannot_close_while_goods_are_held(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    response = client.post(
        "/v1/cases/CASE-0001/decision",
        json={"status": "closed_unsubstantiated", "reason": "Closing while goods remain in custody"},
        headers=auth(tenant.supervisor.token, new_key("decide")),
    )
    assert response.status_code == 409, response.text


def test_a_tampered_custody_record_is_detected(
    client: TestClient,
    tenant: Tenant,
    clock: FixedClock,
    runtime: Runtime,
    session_factory: sessionmaker[Session],
) -> None:
    """The database refuses the edit; if one were forced, the chain would not verify."""
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    with session_factory() as session:
        with pytest.raises(Exception, match="append-only|immutable|reject"):
            session.execute(text("UPDATE custody_transfers SET to_custodian = 'Someone Else'"))
            session.commit()
        session.rollback()

    # Bypass the trigger the way only a database owner could, then re-verify the chain.
    with runtime.engine.begin() as connection:
        connection.execute(text("ALTER TABLE custody_transfers DISABLE TRIGGER USER"))
        connection.execute(text("UPDATE custody_transfers SET to_custodian = 'Someone Else'"))
        connection.execute(text("ALTER TABLE custody_transfers ENABLE TRIGGER USER"))
    with session_factory() as session:
        seizure = session.execute(select(Seizure)).scalars().one()
        intact, broken_at = enforcement_service.custody_chain_intact(
            session, seizure=seizure, secret=runtime.settings.audit_chain_secret
        )
        first = session.execute(select(CustodyTransfer.sequence).order_by(CustodyTransfer.sequence))
    assert intact is False
    assert broken_at == first.scalars().first()


def test_releasing_goods_removes_them_from_the_case_exposure(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock, quantity=400)
    _record_seizure(client, tenant, clock, seizure_ref="SEIZ-0002", quantity=100)
    released = client.post(
        "/v1/seizures/SEIZ-0001/settlement",
        json={"status": "released", "reason": "Documentation produced showing duty was paid"},
        headers=auth(tenant.supervisor.token, new_key("settle")),
    )
    assert released.status_code == 200, released.text
    case = client.get("/v1/cases/CASE-0001", headers=auth(tenant.analyst.token))
    assert case.json()["revenue_at_risk_minor"] == tenant.unit_price_minor * 100

    window = {
        "start": (clock.now() - dt.timedelta(days=1)).isoformat(),
        "end": (clock.now() + dt.timedelta(days=1)).isoformat(),
    }
    report = client.get("/v1/reports/revenue-at-risk", params=window, headers=auth(tenant.supervisor.token))
    assert report.status_code == 200, report.text
    components = {item["source"]: item["amount_minor"] for item in report.json()["components"]}
    assert components["open_enforcement_cases"] == tenant.unit_price_minor * 100
    assert components["goods_in_custody"] == tenant.unit_price_minor * 100


def test_forfeited_goods_remain_in_the_case_exposure(
    client: TestClient, tenant: Tenant, clock: FixedClock
) -> None:
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock, quantity=400)
    forfeited = client.post(
        "/v1/seizures/SEIZ-0001/settlement",
        json={"status": "forfeited", "reason": "Forfeited to the state after an unanswered notice"},
        headers=auth(tenant.supervisor.token, new_key("settle")),
    )
    assert forfeited.status_code == 200, forfeited.text
    case = client.get("/v1/cases/CASE-0001", headers=auth(tenant.analyst.token))
    assert case.json()["revenue_at_risk_minor"] == tenant.unit_price_minor * 400


def test_a_custody_chain_recorded_with_an_offset_still_verifies(
    client: TestClient,
    tenant: Tenant,
    clock: FixedClock,
    runtime: Runtime,
    session_factory: sessionmaker[Session],
) -> None:
    """Handovers are hashed in UTC: the database returns UTC whatever offset was sent."""
    _open_case(client, tenant)
    _record_seizure(client, tenant, clock)
    lagos = dt.timezone(dt.timedelta(hours=1))
    response = client.post(
        "/v1/seizures/SEIZ-0001/custody",
        json={
            "from_custodian": "Inspector A. Bello",
            "to_custodian": "Evidence Store, Ikeja",
            "location": "Ikeja",
            "reason": "Moved to the evidence store",
            "evidence_reference": "HANDOVER-TZ",
            "occurred_at": clock.now().astimezone(lagos).isoformat(),
        },
        headers=auth(tenant.analyst.token, new_key("custody")),
    )
    assert response.status_code == 201, response.text
    with session_factory() as session:
        seizure = session.execute(select(Seizure)).scalars().one()
        intact, broken_at = enforcement_service.custody_chain_intact(
            session, seizure=seizure, secret=runtime.settings.audit_chain_secret
        )
    assert (intact, broken_at) == (True, None)


def test_cases_can_be_listed_by_status(client: TestClient, tenant: Tenant) -> None:
    _open_case(client, tenant)
    _open_case(client, tenant, case_ref="CASE-0002")
    listed = client.get("/v1/cases?status=open", headers=auth(tenant.supervisor.token))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 2
    assert {case["case_ref"] for case in body["cases"]} == {"CASE-0001", "CASE-0002"}

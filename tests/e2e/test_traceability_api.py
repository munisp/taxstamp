"""Aggregation, movement, detection and disclosure over HTTP."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.enums import StampStatus
from taxstamp.models import Stamp
from taxstamp.runtime import Runtime
from taxstamp.security import derive_secure_code
from tests.support.api import auth, new_key, signed_headers
from tests.support.issuance import issue_stamps
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e

QUANTITY = 12
LAGOS = (6_524_379, 3_379_206)
KANO = (12_002_179, 8_591_956)


def _facility(
    client: TestClient,
    tenant: Tenant,
    *,
    code: str,
    kind: str,
    coordinates: tuple[int, int],
    company_scoped: bool = True,
) -> None:
    body = {
        "facility_code": code,
        "name": f"{code} site",
        "kind": kind,
        "country": "NG",
        "state": "Lagos",
        "address": "12 Marina Road, Lagos Island, Lagos",
        "latitude_e7": coordinates[0],
        "longitude_e7": coordinates[1],
        "company_id": str(tenant.company.id) if company_scoped else None,
    }
    response = client.post("/v1/facilities", json=body, headers=auth(tenant.admin.token, new_key("facility")))
    assert response.status_code == 201, response.text


def _case(client: TestClient, tenant: Tenant, *, code: str, serials: list[str]) -> dict[str, object]:
    response = client.post(
        "/v1/units",
        json={
            "unit_code": code,
            "level": "case",
            "facility_code": "FAC-FACTORY",
            "serials": serials,
            "product_id": str(tenant.product.id),
        },
        headers=auth(tenant.requester.token, new_key("case")),
    )
    assert response.status_code == 201, response.text
    document: dict[str, object] = response.json()
    return document


def _dispatch(
    client: TestClient,
    tenant: Tenant,
    *,
    ref: str,
    unit_code: str,
    count: int,
    occurred_at: dt.datetime,
    origin: str = "FAC-FACTORY",
    destination: str | None = "FAC-DC",
) -> dict[str, object]:
    response = client.post(
        "/v1/trace-events",
        json={
            "event_ref": ref,
            "event_type": "dispatch",
            "unit_code": unit_code,
            "origin_facility_code": origin,
            "destination_facility_code": destination,
            "observed_stamp_count": count,
            "transport_reference": "TRK-1",
            "occurred_at": occurred_at.isoformat(),
        },
        headers=auth(tenant.requester.token, new_key("dispatch")),
    )
    return {"status_code": response.status_code, "body": response.json(), "text": response.text}


@pytest.fixture
def serials(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> list[str]:
    return issue_stamps(
        client,
        runtime=runtime,
        settings=settings,
        clock=clock,
        tenant=tenant,
        session_factory=session_factory,
        quantity=QUANTITY,
    )


def test_aggregation_then_dispatch_is_traceable_from_the_serial(
    client: TestClient, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    _facility(client, tenant, code="FAC-FACTORY", kind="factory", coordinates=LAGOS)
    _facility(client, tenant, code="FAC-DC", kind="distribution_centre", coordinates=KANO)
    case = _case(client, tenant, code="CASE-0001", serials=serials[:6])
    assert case["stamp_count"] == 6

    pallet = client.post(
        "/v1/units",
        json={
            "unit_code": "PAL-0001",
            "level": "pallet",
            "facility_code": "FAC-FACTORY",
            "child_unit_codes": ["CASE-0001"],
        },
        headers=auth(tenant.requester.token, new_key("pallet")),
    )
    assert pallet.status_code == 201, pallet.text
    assert pallet.json()["stamp_count"] == 6

    # The movement must be recorded against the outermost unit, not the packed case.
    inner = _dispatch(
        client, tenant, ref="EVT-INNER-1", unit_code="CASE-0001", count=6, occurred_at=clock.now()
    )
    assert inner["status_code"] == 409, inner["text"]

    dispatched = _dispatch(
        client, tenant, ref="EVT-0001", unit_code="PAL-0001", count=6, occurred_at=clock.now()
    )
    assert dispatched["status_code"] == 201, dispatched["text"]
    body = dispatched["body"]
    assert isinstance(body, dict)
    assert body["unit_status"] == "in_transit"

    trace = client.get(f"/v1/stamps/{serials[0]}/trace", headers=auth(tenant.requester.token))
    assert trace.status_code == 200, trace.text
    document = trace.json()
    assert document["aggregation_path"] == ["CASE-0001", "PAL-0001"]
    assert [event["event_type"] for event in document["movements"]] == ["dispatch"]
    assert document["product"]["sku"] == tenant.product.sku


def test_a_dispatch_must_declare_the_quantity_the_unit_contains(
    client: TestClient, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    _facility(client, tenant, code="FAC-FACTORY", kind="factory", coordinates=LAGOS)
    _facility(client, tenant, code="FAC-DC", kind="distribution_centre", coordinates=KANO)
    _case(client, tenant, code="CASE-0002", serials=serials[:4])

    understated = _dispatch(
        client, tenant, ref="EVT-0002", unit_code="CASE-0002", count=3, occurred_at=clock.now()
    )
    assert understated["status_code"] == 422, understated["text"]


def test_a_stamp_cannot_be_packed_into_two_cases(
    client: TestClient, tenant: Tenant, serials: list[str]
) -> None:
    _facility(client, tenant, code="FAC-FACTORY", kind="factory", coordinates=LAGOS)
    _case(client, tenant, code="CASE-0003", serials=serials[:3])
    second = client.post(
        "/v1/units",
        json={
            "unit_code": "CASE-0004",
            "level": "case",
            "facility_code": "FAC-FACTORY",
            "serials": [serials[2]],
        },
        headers=auth(tenant.requester.token, new_key("case")),
    )
    assert second.status_code == 409, second.text


def test_destruction_voids_the_stamps_it_covers(
    client: TestClient,
    clock: FixedClock,
    settings: Settings,
    tenant: Tenant,
    serials: list[str],
    session_factory: sessionmaker[Session],
) -> None:
    _facility(client, tenant, code="FAC-FACTORY", kind="factory", coordinates=LAGOS)
    _facility(client, tenant, code="FAC-BURN", kind="destruction_site", coordinates=LAGOS)
    _case(client, tenant, code="CASE-0005", serials=serials[:5])

    destroyed = client.post(
        "/v1/trace-events",
        json={
            "event_ref": "EVT-DESTROY-1",
            "event_type": "destruction",
            "unit_code": "CASE-0005",
            "origin_facility_code": "FAC-BURN",
            "destination_facility_code": None,
            "observed_stamp_count": 5,
            "transport_reference": "",
            "occurred_at": clock.now().isoformat(),
        },
        headers=auth(tenant.requester.token, new_key("destroy")),
    )
    assert destroyed.status_code == 201, destroyed.text

    with session_factory() as session:
        statuses = list(
            session.execute(select(Stamp.status).where(Stamp.serial.in_(serials[:5]))).scalars().all()
        )
    assert set(statuses) == {StampStatus.VOID.value}

    body = {
        "serial": serials[0],
        "secure_code": derive_secure_code(serials[0], secret=settings.device_hmac_secret),
        "device_id": "field-device-trace",
        "nonce": f"nonce-{serials[0]}",
    }
    verification = client.post(
        "/v1/verify",
        json=body,
        headers={
            **auth(tenant.device.token),
            **signed_headers(body, secret=settings.device_hmac_secret, now=clock.now()),
        },
    )
    assert verification.status_code == 200, verification.text
    assert verification.json()["authentic"] is False
    assert verification.json()["outcome"] != "valid"


def test_impossible_travel_between_two_dispatches_is_reported(
    client: TestClient, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    _facility(client, tenant, code="FAC-FACTORY", kind="factory", coordinates=LAGOS)
    _facility(client, tenant, code="FAC-DC", kind="distribution_centre", coordinates=KANO)
    _case(client, tenant, code="CASE-0006", serials=serials[:6])

    first = _dispatch(
        client,
        tenant,
        ref="EVT-TRAVEL-1",
        unit_code="CASE-0006",
        count=6,
        occurred_at=clock.now() - dt.timedelta(minutes=2),
    )
    assert first["status_code"] == 201, first["text"]

    arrival = client.post(
        "/v1/trace-events",
        json={
            "event_ref": "EVT-TRAVEL-2",
            "event_type": "arrival",
            "unit_code": "CASE-0006",
            # Received back in Lagos two minutes after leaving for Kano: the unit
            # cannot have covered the distance in either direction.
            "origin_facility_code": "FAC-FACTORY",
            "destination_facility_code": None,
            "observed_stamp_count": 6,
            "transport_reference": "TRK-1",
            "occurred_at": clock.now().isoformat(),
        },
        headers=auth(tenant.requester.token, new_key("arrive")),
    )
    assert arrival.status_code == 201, arrival.text
    kinds = [finding["kind"] for finding in arrival.json()["anomalies"]]
    assert "impossible_travel" in kinds

    listed = client.get("/v1/anomalies", headers=auth(tenant.analyst.token))
    assert listed.status_code == 200, listed.text
    assert [finding["kind"] for finding in listed.json()["anomalies"]] == ["impossible_travel"]


def test_a_device_credential_may_not_read_the_movement_repository(client: TestClient, tenant: Tenant) -> None:
    response = client.get("/v1/movements", headers=auth(tenant.device.token))
    assert response.status_code == 403, response.text

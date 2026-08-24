"""The public stamp check answers honestly and discloses nothing confidential."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import ConsumerVerification
from taxstamp.runtime import Runtime
from taxstamp.security import derive_secure_code
from tests.support.api import auth, new_key
from tests.support.issuance import issue_stamps
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e

ENDPOINT = "/v1/public/verify"

#: Fields a consumer must never be able to learn from a stamp.
CONFIDENTIAL_FIELDS = (
    "company_id",
    "order_id",
    "licence_number",
    "batch_id",
    "facility_code",
    "consignment_ref",
    "unit_price_minor",
    "total_minor",
)


@pytest.fixture
def active_serial(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> str:
    serials = issue_stamps(
        client,
        runtime=runtime,
        settings=settings,
        clock=clock,
        tenant=tenant,
        session_factory=session_factory,
        quantity=2,
    )
    activation = client.post(
        "/v1/stamps/activate",
        json={"serials": serials},
        headers=auth(tenant.operator.token, new_key("activate")),
    )
    assert activation.status_code == 200, activation.text
    return serials[0]


def _check(client: TestClient, serial: str, code: str) -> dict[str, object]:
    response = client.post(ENDPOINT, json={"serial": serial, "secure_code": code})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_a_genuine_active_stamp_is_confirmed(
    client: TestClient, settings: Settings, active_serial: str
) -> None:
    body = _check(
        client, active_serial, derive_secure_code(active_serial, secret=settings.device_hmac_secret)
    )
    assert body["authentic"] is True
    assert body["outcome"] == "valid"
    assert body["product_category"] == "alcohol"


def test_the_answer_carries_no_confidential_fields(
    client: TestClient, settings: Settings, active_serial: str
) -> None:
    body = _check(
        client, active_serial, derive_secure_code(active_serial, secret=settings.device_hmac_secret)
    )
    assert [field for field in CONFIDENTIAL_FIELDS if field in body] == []


def test_a_wrong_secure_code_is_not_authentic_and_reveals_no_product(
    client: TestClient, active_serial: str
) -> None:
    body = _check(client, active_serial, "000000")
    assert body["authentic"] is False
    assert body["outcome"] == "secure_code_mismatch"
    assert body["brand"] is None
    assert body["product_category"] is None


def test_an_invented_serial_is_not_authentic(client: TestClient) -> None:
    body = _check(client, "NG-ALC-2026-99999999", "123456")
    assert body["authentic"] is False
    assert body["outcome"] == "unknown_serial"
    assert "Do not buy" in str(body["advice"])


def test_an_issued_but_unactivated_stamp_is_not_authentic(
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
    body = _check(client, serials[0], derive_secure_code(serials[0], secret=settings.device_hmac_secret))
    assert body["authentic"] is False
    assert body["outcome"] == "not_active"


def test_every_attempt_is_recorded_without_the_caller_address(
    client: TestClient, settings: Settings, active_serial: str, session_factory: sessionmaker[Session]
) -> None:
    _check(client, active_serial, derive_secure_code(active_serial, secret=settings.device_hmac_secret))
    _check(client, active_serial, "000000")
    with session_factory() as session:
        records = list(
            session.execute(select(ConsumerVerification).order_by(ConsumerVerification.occurred_at))
            .scalars()
            .all()
        )
    assert [record.outcome for record in records] == ["valid", "secure_code_mismatch"]
    fingerprints = {record.client_fingerprint for record in records}
    assert len(fingerprints) == 1
    # The fingerprint is a keyed hash: the raw address is nowhere in the record.
    assert "testclient" not in fingerprints.pop()


def test_the_public_endpoint_is_rate_limited(
    client: TestClient, settings: Settings, active_serial: str
) -> None:
    code = derive_secure_code(active_serial, secret=settings.device_hmac_secret)
    statuses = [
        client.post(ENDPOINT, json={"serial": active_serial, "secure_code": code}).status_code
        for _ in range(settings.rate_limit_consumer_verify + 2)
    ]
    assert 429 in statuses


def test_a_consumer_cannot_reach_an_authenticated_endpoint(client: TestClient) -> None:
    response = client.get("/v1/reports/kpis")
    assert response.status_code == 401

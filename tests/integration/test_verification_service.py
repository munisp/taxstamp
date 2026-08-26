"""Verification never defaults to authentic."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import Stamp, Verification
from taxstamp.runtime import Runtime
from taxstamp.security import derive_secure_code
from taxstamp.worker.relay import relay_once
from tests.support.api import Remittance, auth, new_key, post_remittance, signed_headers
from tests.support.tenant import Tenant

pytestmark = pytest.mark.integration


@pytest.fixture
def active_serial(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> str:
    order = client.post(
        "/v1/orders",
        json={
            "company_id": str(tenant.company.id),
            "product_category": "alcohol",
            "quantity": 3,
            "delivery_state": "Lagos",
            "delivery_address": "12 Marina Road, Lagos Island, Lagos",
        },
        headers=auth(tenant.requester.token, new_key("order")),
    ).json()
    client.post(
        f"/v1/orders/{order['id']}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "documents verified"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    detail = client.get(f"/v1/orders/{order['id']}", headers=auth(tenant.requester.token)).json()
    post_remittance(
        client,
        Remittance(
            external_reference=f"BANK-{uuid.uuid4().hex[:8]}",
            payment_reference=detail["payment"]["reference"],
            amount_minor=int(order["total_minor"]),
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    relay_once(runtime, worker_id="w")
    with session_factory() as session:
        serial = session.execute(select(Stamp.serial).order_by(Stamp.serial)).scalars().first()
    assert serial is not None
    client.post(
        "/v1/stamps/activate",
        json={"serials": [serial]},
        headers=auth(tenant.operator.token, new_key("activate")),
    )
    return serial


def _verify(
    client: TestClient,
    tenant: Tenant,
    settings: Settings,
    clock: FixedClock,
    *,
    serial: str,
    code: str,
    nonce: str,
    device_id: str | None = None,
) -> dict[str, object]:
    body = {
        "serial": serial,
        "secure_code": code,
        "device_id": device_id or tenant.device.subject,
        "nonce": nonce,
    }
    response = client.post(
        "/v1/verify",
        json=body,
        headers={
            **auth(tenant.device.token),
            **signed_headers(body, secret=settings.device_hmac_secret, now=clock.now()),
        },
    )
    assert response.status_code == 200, response.text
    result: dict[str, object] = response.json()
    return result


def test_correct_code_is_authentic(
    client: TestClient, tenant: Tenant, settings: Settings, clock: FixedClock, active_serial: str
) -> None:
    result = _verify(
        client,
        tenant,
        settings,
        clock,
        serial=active_serial,
        code=derive_secure_code(active_serial, secret=settings.device_hmac_secret),
        nonce="nonce-ok-1",
    )
    assert result["authentic"] is True


def test_wrong_code_is_not_authentic(
    client: TestClient, tenant: Tenant, settings: Settings, clock: FixedClock, active_serial: str
) -> None:
    result = _verify(
        client,
        tenant,
        settings,
        clock,
        serial=active_serial,
        code="AAAAAAAAAAAA",
        nonce="nonce-bad-1",
    )
    assert result["authentic"] is False
    assert result["outcome"] == "secure_code_mismatch"


def test_unknown_serial_is_not_authentic(
    client: TestClient, tenant: Tenant, settings: Settings, clock: FixedClock, active_serial: str
) -> None:
    result = _verify(
        client,
        tenant,
        settings,
        clock,
        serial="NG-ALC-2026-999999-A",
        code="AAAAAAAAAAAA",
        nonce="nonce-unknown-1",
    )
    assert result["authentic"] is False
    assert result["outcome"] in ("unknown_serial", "secure_code_mismatch")


def test_voided_stamp_is_not_authentic(
    client: TestClient,
    tenant: Tenant,
    settings: Settings,
    clock: FixedClock,
    active_serial: str,
) -> None:
    client.post(
        "/v1/stamps/void",
        json={"serials": [active_serial], "reason": "seized in the field"},
        headers=auth(tenant.operator.token, new_key("void")),
    )
    result = _verify(
        client,
        tenant,
        settings,
        clock,
        serial=active_serial,
        code=derive_secure_code(active_serial, secret=settings.device_hmac_secret),
        nonce="nonce-void-1",
    )
    assert result["authentic"] is False
    assert result["outcome"] == "void"


def test_every_attempt_is_recorded(
    client: TestClient,
    tenant: Tenant,
    settings: Settings,
    clock: FixedClock,
    active_serial: str,
    session_factory: sessionmaker[Session],
) -> None:
    _verify(
        client,
        tenant,
        settings,
        clock,
        serial=active_serial,
        code="AAAAAAAAAAAA",
        nonce="nonce-audit-1",
    )
    with session_factory() as session:
        attempts = session.execute(select(Verification)).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].outcome == "secure_code_mismatch"
    assert attempts[0].device_id == tenant.device.subject

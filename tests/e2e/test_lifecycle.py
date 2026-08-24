"""The golden path, end to end over HTTP, against real PostgreSQL and Redis."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import LedgerEntry, Stamp
from taxstamp.runtime import Runtime
from taxstamp.security import derive_secure_code
from taxstamp.worker.relay import relay_once
from tests.support.api import Remittance, auth, new_key, post_remittance, signed_headers
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e
QUANTITY = 250


def _create_order(client: TestClient, tenant: Tenant, quantity: int = QUANTITY) -> dict[str, object]:
    response = client.post(
        "/v1/orders",
        json={
            "company_id": str(tenant.company.id),
            "product_category": "alcohol",
            "quantity": quantity,
            "delivery_state": "Lagos",
            "delivery_address": "12 Marina Road, Lagos Island, Lagos",
        },
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client: TestClient, tenant: Tenant, order_id: str) -> dict[str, object]:
    response = client.post(
        f"/v1/orders/{order_id}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "documents verified"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_full_lifecycle_order_to_verification(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    order = _create_order(client, tenant)
    assert order["status"] == "awaiting_approval"
    expected_subtotal = tenant.unit_price_minor * QUANTITY
    assert order["subtotal_minor"] == expected_subtotal
    assert order["total_minor"] == expected_subtotal + round(expected_subtotal * tenant.vat_bps / 10_000)

    approved = _approve(client, tenant, str(order["id"]))
    assert approved["status"] == "awaiting_payment"

    detail = client.get(f"/v1/orders/{order['id']}", headers=auth(tenant.requester.token)).json()
    payment_reference = detail["payment"]["reference"]

    response = post_remittance(
        client,
        Remittance(
            external_reference=f"BANK-{payment_reference}",
            payment_reference=payment_reference,
            amount_minor=int(order["total_minor"]),
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    assert response.status_code == 202, response.text  # type: ignore[attr-defined]
    assert response.json()["status"] == "matched"  # type: ignore[attr-defined]

    with session_factory() as session:
        entries = session.execute(select(LedgerEntry)).scalars().all()
        debits = sum(entry.amount_minor for entry in entries if entry.direction == "debit")
        credits = sum(entry.amount_minor for entry in entries if entry.direction == "credit")
    assert debits == credits == int(order["total_minor"])

    stats = relay_once(runtime, worker_id="test-worker")
    assert stats.delivered >= 1
    assert stats.dead_lettered == 0
    # The issuance event enqueues an anchoring event; drain it too.
    relay_once(runtime, worker_id="test-worker")

    issued = client.get(f"/v1/orders/{order['id']}", headers=auth(tenant.requester.token)).json()
    assert issued["status"] == "issued"

    with session_factory() as session:
        stamps = session.execute(select(Stamp).order_by(Stamp.serial)).scalars().all()
        serials = [stamp.serial for stamp in stamps]
        batch_id = str(stamps[0].batch_id)
    assert len(serials) == QUANTITY
    assert len(set(serials)) == QUANTITY

    inspection = client.post(
        f"/v1/batches/{batch_id}/inspections",
        json={"defects_found": 0, "defective_serials": []},
        headers=auth(tenant.operator.token, new_key("inspect")),
    )
    assert inspection.status_code == 201, inspection.text
    assert inspection.json()["accepted"] is True

    activation = client.post(
        "/v1/stamps/activate",
        json={"serials": serials[:10]},
        headers=auth(tenant.operator.token, new_key("activate")),
    )
    assert activation.status_code == 200, activation.text
    assert activation.json()["changed"] == 10

    serial = serials[0]
    body = {
        "serial": serial,
        "secure_code": derive_secure_code(serial, secret=settings.device_hmac_secret),
        "device_id": "field-device-1",
        "nonce": "nonce-" + serial,
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
    assert verification.json()["authentic"] is True
    assert verification.json()["outcome"] == "valid"

    reconciliation = client.post("/v1/ops/reconciliation", headers=auth(tenant.admin.token)).json()
    assert reconciliation["clean"] is True, reconciliation

    chain = client.get("/v1/ops/audit-chain", headers=auth(tenant.admin.token)).json()
    assert chain["intact"] is True
    assert chain["events_checked"] > 0


def test_replayed_verification_nonce_is_rejected(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, runtime: Runtime
) -> None:
    body = {
        "serial": "NG-ALC-2026-000001-X",
        "secure_code": "ABCDEFGHJKLM",
        "device_id": "field-device-2",
        "nonce": "reused-nonce-1",
    }
    headers = {
        **auth(tenant.device.token),
        **signed_headers(body, secret=settings.device_hmac_secret, now=clock.now()),
    }
    first = client.post("/v1/verify", json=body, headers=headers)
    second = client.post("/v1/verify", json=body, headers=headers)
    assert first.status_code in (200, 422)
    assert second.status_code == 401


def test_expiry_job_expires_stamps_after_validity(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    order = _create_order(client, tenant, quantity=5)
    _approve(client, tenant, str(order["id"]))
    detail = client.get(f"/v1/orders/{order['id']}", headers=auth(tenant.requester.token)).json()
    post_remittance(
        client,
        Remittance(
            external_reference=f"BANK-{detail['payment']['reference']}",
            payment_reference=detail["payment"]["reference"],
            amount_minor=int(order["total_minor"]),
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    relay_once(runtime, worker_id="test-worker")

    from taxstamp.worker.relay import expire_stamps_once

    assert expire_stamps_once(runtime) == 0
    clock.advance(dt.timedelta(days=800).total_seconds())
    expired = expire_stamps_once(runtime)
    assert expired == 5
    with session_factory() as session:
        statuses = {stamp.status for stamp in session.execute(select(Stamp)).scalars()}
    assert statuses == {"expired"}

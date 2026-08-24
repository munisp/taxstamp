"""Settlement ingestion: signatures, exact amounts, replays and mismatches."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import LedgerEntry, Order
from tests.support.api import Remittance, auth, new_key, post_remittance, signed_headers
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e


@pytest.fixture
def awaiting_payment(client: TestClient, tenant: Tenant) -> dict[str, object]:
    created = client.post(
        "/v1/orders",
        json={
            "company_id": str(tenant.company.id),
            "product_category": "alcohol",
            "quantity": 20,
            "delivery_state": "Lagos",
            "delivery_address": "12 Marina Road, Lagos Island, Lagos",
        },
        headers=auth(tenant.requester.token, new_key("order")),
    )
    order = created.json()
    client.post(
        f"/v1/orders/{order['id']}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "documents verified"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    return client.get(f"/v1/orders/{order['id']}", headers=auth(tenant.requester.token)).json()


def test_unsigned_remittance_is_rejected(client: TestClient, awaiting_payment: dict[str, object]) -> None:
    payment = awaiting_payment["payment"]
    assert isinstance(payment, dict)
    body = {
        "external_reference": "BANK-1",
        "payment_reference": payment["reference"],
        "amount_minor": payment["amount_minor"],
        "currency": "NGN",
        "value_date": "2026-03-01T12:00:00+00:00",
    }
    response = client.post("/v1/payments/remittances", content=json.dumps(body))
    assert response.status_code == 401


def test_tampered_amount_invalidates_the_signature(
    client: TestClient, settings: Settings, clock: FixedClock, awaiting_payment: dict[str, object]
) -> None:
    payment = awaiting_payment["payment"]
    assert isinstance(payment, dict)
    body = {
        "external_reference": "BANK-2",
        "payment_reference": payment["reference"],
        "amount_minor": payment["amount_minor"],
        "currency": "NGN",
        "value_date": clock.now().isoformat(),
    }
    headers = signed_headers(body, secret=settings.payment_webhook_secret, now=clock.now())
    tampered = {**body, "amount_minor": 1}
    response = client.post("/v1/payments/remittances", content=json.dumps(tampered), headers=headers)
    assert response.status_code == 401


def test_underpayment_is_quarantined_and_order_stays_unpaid(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    awaiting_payment: dict[str, object],
    session_factory: sessionmaker[Session],
) -> None:
    payment = awaiting_payment["payment"]
    assert isinstance(payment, dict)
    response = post_remittance(
        client,
        Remittance(
            external_reference="BANK-SHORT-1",
            payment_reference=str(payment["reference"]),
            amount_minor=int(payment["amount_minor"]) - 1,
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    assert response.status_code == 202  # type: ignore[attr-defined]
    assert response.json()["status"] == "amount_mismatch"  # type: ignore[attr-defined]
    with session_factory() as session:
        order = session.execute(select(Order)).scalars().one()
        entries = session.execute(select(LedgerEntry)).scalars().all()
    assert order.status == "awaiting_payment"
    assert sum(e.amount_minor for e in entries if e.direction == "debit") == sum(
        e.amount_minor for e in entries if e.direction == "credit"
    )
    assert any(entry.account == "liability:unapplied_receipts" for entry in entries)


def test_unknown_reference_is_recorded_without_touching_an_order(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant
) -> None:
    response = post_remittance(
        client,
        Remittance(
            external_reference="BANK-ORPHAN-1",
            payment_reference="PAY-2026-UNKNOWN1",
            amount_minor=5_000,
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    assert response.json()["status"] == "unknown_reference"  # type: ignore[attr-defined]
    assert response.json()["order_id"] is None  # type: ignore[attr-defined]


def test_duplicate_delivery_settles_once(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    awaiting_payment: dict[str, object],
    session_factory: sessionmaker[Session],
) -> None:
    payment = awaiting_payment["payment"]
    assert isinstance(payment, dict)
    remittance = Remittance(
        external_reference="BANK-DUP-1",
        payment_reference=str(payment["reference"]),
        amount_minor=int(payment["amount_minor"]),
        currency="NGN",
        value_date=clock.now(),
    )
    first = post_remittance(client, remittance, secret=settings.payment_webhook_secret, now=clock.now())
    assert first.json()["status"] == "matched"  # type: ignore[attr-defined]
    # A second, byte-identical delivery is stopped by the replay guard.
    replay = post_remittance(client, remittance, secret=settings.payment_webhook_secret, now=clock.now())
    assert replay.json()["status"] == "duplicate_delivery"  # type: ignore[attr-defined]
    # A re-delivery whose bytes differ passes the replay guard, and the unique external
    # reference must still stop a second settlement.
    clock.advance(1)
    again = post_remittance(
        client,
        Remittance(
            external_reference=remittance.external_reference,
            payment_reference=remittance.payment_reference,
            amount_minor=remittance.amount_minor,
            currency=remittance.currency,
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    assert again.json()["duplicate"] is True  # type: ignore[attr-defined]
    with session_factory() as session:
        entries = session.execute(select(LedgerEntry)).scalars().all()
    assert sum(e.amount_minor for e in entries if e.direction == "debit") == int(payment["amount_minor"])

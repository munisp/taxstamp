"""Concurrency: real threads, real connections, real row locks."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import LedgerEntry, PaymentReceipt, Stamp
from taxstamp.runtime import Runtime
from taxstamp.services.issuance import allocate_serial_block
from taxstamp.worker.relay import relay_once
from tests.support.api import Remittance, auth, new_key, post_remittance
from tests.support.tenant import Tenant

pytestmark = pytest.mark.concurrency
ORDER_BODY = {
    "product_category": "alcohol",
    "quantity": 40,
    "delivery_state": "Lagos",
    "delivery_address": "12 Marina Road, Lagos Island, Lagos",
}


def test_same_idempotency_key_creates_one_order(client: TestClient, tenant: Tenant) -> None:
    key = new_key("race")
    body = {"company_id": str(tenant.company.id), **ORDER_BODY}

    def submit() -> int:
        return client.post("/v1/orders", json=body, headers=auth(tenant.requester.token, key)).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: submit(), range(8)))

    assert statuses.count(201) == 1
    assert all(status in (201, 409) for status in statuses), statuses
    listing = client.get("/v1/orders", headers=auth(tenant.requester.token)).json()
    assert len(listing["orders"]) == 1


def test_serial_allocation_never_hands_out_the_same_block(
    session_factory: sessionmaker[Session],
) -> None:
    def allocate() -> tuple[int, int]:
        with session_factory() as session:
            start = allocate_serial_block(session, product_category="alcohol", year=2026, count=100)
            session.commit()
            return start, start + 99

    with ThreadPoolExecutor(max_workers=8) as pool:
        blocks = list(pool.map(lambda _: allocate(), range(8)))

    claimed: set[int] = set()
    for start, end in blocks:
        span = set(range(start, end + 1))
        assert not span & claimed
        claimed |= span
    assert len(claimed) == 800


def test_concurrent_activation_is_counted_once(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    created = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    ).json()
    client.post(
        f"/v1/orders/{created['id']}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "documents verified"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    detail = client.get(f"/v1/orders/{created['id']}", headers=auth(tenant.requester.token)).json()
    post_remittance(
        client,
        Remittance(
            external_reference=f"BANK-{uuid.uuid4().hex[:8]}",
            payment_reference=detail["payment"]["reference"],
            amount_minor=int(created["total_minor"]),
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    relay_once(runtime, worker_id="w")
    with session_factory() as session:
        serials = [stamp.serial for stamp in session.execute(select(Stamp).order_by(Stamp.serial)).scalars()]
    assert len(serials) == ORDER_BODY["quantity"]

    def activate() -> int:
        response = client.post(
            "/v1/stamps/activate",
            json={"serials": serials},
            headers=auth(tenant.operator.token, new_key("activate")),
        )
        return response.json().get("changed", 0) if response.status_code == 200 else 0

    with ThreadPoolExecutor(max_workers=6) as pool:
        changed = list(pool.map(lambda _: activate(), range(6)))

    assert sum(changed) == len(serials)
    with session_factory() as session:
        active = session.execute(
            select(func.count()).select_from(Stamp).where(Stamp.status == "active")
        ).scalar_one()
    assert active == len(serials)


def _race_cancel_against_settlement(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    round_index: int,
) -> tuple[int, int]:
    created = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    ).json()
    client.post(
        f"/v1/orders/{created['id']}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "documents verified"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    detail = client.get(f"/v1/orders/{created['id']}", headers=auth(tenant.requester.token)).json()

    def cancel() -> int:
        return client.post(
            f"/v1/orders/{created['id']}/cancel",
            json={"reason": "withdrawn by the requester"},
            headers=auth(tenant.requester.token, new_key("cancel")),
        ).status_code

    def settle() -> int:
        return post_remittance(
            client,
            Remittance(
                external_reference=f"BANK-RACE-{round_index}",
                payment_reference=detail["payment"]["reference"],
                amount_minor=int(created["total_minor"]),
                currency="NGN",
                value_date=clock.now(),
            ),
            secret=settings.payment_webhook_secret,
            now=clock.now(),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelling = pool.submit(cancel)
        settling = pool.submit(settle)
        return cancelling.result(), settling.result()


def test_cancel_and_settlement_race_never_deadlocks(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    """Both paths lock the order before its intents, so the race resolves either way."""
    outcomes = [_race_cancel_against_settlement(client, settings, clock, tenant, index) for index in range(4)]

    # A deadlock or an illegal transition would surface as 5xx; every attempt must be decided.
    for cancel_status, settle_status in outcomes:
        assert cancel_status in (200, 409), outcomes
        assert settle_status == 202, outcomes

    with session_factory() as session:
        receipts = session.execute(select(PaymentReceipt)).scalars().all()
        entries = session.execute(select(LedgerEntry)).scalars().all()
    assert len(receipts) == 4
    assert {receipt.status for receipt in receipts} <= {"matched", "order_not_payable"}
    assert sum(e.amount_minor for e in entries if e.direction == "debit") == sum(
        e.amount_minor for e in entries if e.direction == "credit"
    )

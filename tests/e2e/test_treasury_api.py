"""Quarantined receipts leave the unapplied account exactly once."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.jsontypes import JsonObject
from taxstamp.ledger import Account, account_balance
from taxstamp.models import OutboxMessage, Stamp
from taxstamp.runtime import Runtime
from taxstamp.worker.relay import relay_once
from tests.support.api import Remittance, auth, new_key, post_remittance
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e
QUANTITY = 12


def _payable_order(client: TestClient, tenant: Tenant) -> JsonObject:
    order = client.post(
        "/v1/orders",
        json={
            "company_id": str(tenant.company.id),
            "product_id": str(tenant.product.id),
            "quantity": QUANTITY,
            "delivery_state": "Lagos",
            "delivery_address": "12 Marina Road, Lagos Island, Lagos",
        },
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert order.status_code == 201, order.text
    document: JsonObject = order.json()
    approval = client.post(
        f"/v1/orders/{document['id']}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "documents verified"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    assert approval.status_code == 201, approval.text
    detail = client.get(f"/v1/orders/{document['id']}", headers=auth(tenant.requester.token)).json()
    document["payment_reference"] = detail["payment"]["reference"]
    return document


def _quarantined_receipt(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    *,
    reference: str,
    amount_minor: int,
) -> JsonObject:
    """A remittance for an amount that does not match: held, not applied."""
    response = post_remittance(
        client,
        Remittance(
            external_reference=f"BANK-{reference}-Q",
            payment_reference=reference,
            amount_minor=amount_minor,
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    assert response.status_code == 202, response.text  # type: ignore[attr-defined]
    document: JsonObject = response.json()  # type: ignore[attr-defined]
    return document


def test_unknown_reference_receipt_is_held_and_refunded(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    receipt = _quarantined_receipt(
        client, settings, clock, reference="PAY-DOES-NOT-EXIST", amount_minor=5_000
    )
    assert receipt["status"] == "unknown_reference"

    with session_factory() as session:
        held = account_balance(session, Account.UNAPPLIED_RECEIPTS, "NGN").minor
    assert held == -5_000  # a liability: the platform owes this money back

    listed = client.get("/v1/treasury/unapplied-receipts", headers=auth(tenant.treasury.token)).json()[
        "receipts"
    ]
    assert [row["id"] for row in listed] == [receipt["receipt_id"]]

    refund = client.post(
        f"/v1/treasury/unapplied-receipts/{receipt['receipt_id']}/refund",
        json={"beneficiary_reference": "NUBAN-0123456789", "reason": "payer error"},
        headers=auth(tenant.treasury.token, new_key("refund")),
    )
    assert refund.status_code == 200, refund.text
    assert refund.json()["kind"] == "refunded"

    with session_factory() as session:
        assert account_balance(session, Account.UNAPPLIED_RECEIPTS, "NGN").minor == 0
        assert account_balance(session, Account.BANK_COLLECTIONS, "NGN").minor == 0

    again = client.post(
        f"/v1/treasury/unapplied-receipts/{receipt['receipt_id']}/refund",
        json={"beneficiary_reference": "NUBAN-0123456789", "reason": "second attempt"},
        headers=auth(tenant.treasury.token, new_key("refund-2")),
    )
    assert again.status_code == 409, again.text
    assert (
        client.get("/v1/treasury/unapplied-receipts", headers=auth(tenant.treasury.token)).json()["receipts"]
        == []
    )

    reconciliation = client.post("/v1/ops/reconciliation", headers=auth(tenant.admin.token)).json()
    assert reconciliation["clean"] is True, reconciliation


def test_held_receipt_applied_to_its_order_issues_stamps(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    order = _payable_order(client, tenant)
    reference = str(order["payment_reference"])
    total = int(order["total_minor"])
    # A payer who quotes a valid reference but the wrong amount: money is held.
    receipt = _quarantined_receipt(client, settings, clock, reference=reference, amount_minor=total + 1)
    assert receipt["status"] == "amount_mismatch"

    partial = client.post(
        f"/v1/treasury/unapplied-receipts/{receipt['receipt_id']}/application",
        json={"order_id": str(order["id"]), "reason": "payer confirmed intent"},
        headers=auth(tenant.treasury.token, new_key("apply")),
    )
    assert partial.status_code == 422, partial.text

    # The exact amount arrives under a second reference and is applied.
    exact = _quarantined_receipt(client, settings, clock, reference="PAY-UNMATCHED-EXACT", amount_minor=total)
    assert exact["status"] == "unknown_reference"
    applied = client.post(
        f"/v1/treasury/unapplied-receipts/{exact['receipt_id']}/application",
        json={"order_id": str(order["id"]), "reason": "reference quoted incorrectly by the payer"},
        headers=auth(tenant.treasury.token, new_key("apply-2")),
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["order_id"] == str(order["id"])

    paid = client.get(f"/v1/orders/{order['id']}", headers=auth(tenant.requester.token)).json()
    assert paid["status"] == "paid"

    relay_once(runtime, worker_id="test-worker")
    relay_once(runtime, worker_id="test-worker")
    issued = client.get(f"/v1/orders/{order['id']}", headers=auth(tenant.requester.token)).json()
    assert issued["status"] == "issued"
    with session_factory() as session:
        assert len(session.execute(select(Stamp)).scalars().all()) == QUANTITY
        # The review event for a receipt with no known intent carries no expected amount;
        # its delivery must still succeed rather than retry forever.
        reviews = list(
            session.execute(
                select(OutboxMessage)
                .where(OutboxMessage.event_type == "payment.mismatch_requires_review")
                .order_by(OutboxMessage.created_at)
            )
            .scalars()
            .all()
        )
        assert reviews, "an unmatched remittance must raise a review event"
        assert [message.payload["expected_minor"] for message in reviews] == [total, None]
        assert all(message.last_error is None for message in reviews)
        assert all(message.processed_at is not None for message in reviews)

    # Applying the same receipt to the order a second time is refused.
    repeat = client.post(
        f"/v1/treasury/unapplied-receipts/{exact['receipt_id']}/application",
        json={"order_id": str(order["id"]), "reason": "duplicate attempt"},
        headers=auth(tenant.treasury.token, new_key("apply-3")),
    )
    assert repeat.status_code == 409, repeat.text

    refund = client.post(
        f"/v1/treasury/unapplied-receipts/{receipt['receipt_id']}/refund",
        json={"beneficiary_reference": "NUBAN-0123456789", "reason": "overpayment returned"},
        headers=auth(tenant.treasury.token, new_key("refund")),
    )
    assert refund.status_code == 200, refund.text

    reconciliation = client.post("/v1/ops/reconciliation", headers=auth(tenant.admin.token)).json()
    assert reconciliation["clean"] is True, reconciliation


def test_a_matched_receipt_cannot_be_reapplied_or_refunded(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant
) -> None:
    order = _payable_order(client, tenant)
    reference = str(order["payment_reference"])
    settled = post_remittance(
        client,
        Remittance(
            external_reference=f"BANK-{reference}",
            payment_reference=reference,
            amount_minor=int(order["total_minor"]),
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    assert settled.status_code == 202, settled.text  # type: ignore[attr-defined]
    receipt_id = settled.json()["receipt_id"]  # type: ignore[attr-defined]

    refund = client.post(
        f"/v1/treasury/unapplied-receipts/{receipt_id}/refund",
        json={"beneficiary_reference": "NUBAN-0123456789", "reason": "not permitted"},
        headers=auth(tenant.treasury.token, new_key("refund")),
    )
    assert refund.status_code == 409, refund.text


def test_treasury_endpoints_require_the_treasury_role(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant
) -> None:
    receipt = _quarantined_receipt(client, settings, clock, reference="PAY-ROLE-CHECK", amount_minor=1_000)
    listed = client.get("/v1/treasury/unapplied-receipts", headers=auth(tenant.requester.token))
    assert listed.status_code == 403, listed.text
    refund = client.post(
        f"/v1/treasury/unapplied-receipts/{receipt['receipt_id']}/refund",
        json={"beneficiary_reference": "NUBAN-0123456789", "reason": "not permitted"},
        headers=auth(tenant.operator.token, new_key("refund")),
    )
    assert refund.status_code == 403, refund.text
    missing_beneficiary = client.post(
        f"/v1/treasury/unapplied-receipts/{receipt['receipt_id']}/refund",
        json={"beneficiary_reference": "   ", "reason": "blank beneficiary"},
        headers=auth(tenant.treasury.token, new_key("refund-2")),
    )
    assert missing_beneficiary.status_code == 422, missing_beneficiary.text

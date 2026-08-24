"""Fault injection: dependency failures, crashes and partial work."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import Order, OutboxMessage, Stamp, StampBatch
from taxstamp.runtime import Runtime
from taxstamp.services.issuance import issue_chunk, issue_order
from taxstamp.worker.relay import relay_once
from tests.support.api import Remittance, auth, new_key, post_remittance
from tests.support.registry_server import RegistrySandbox
from tests.support.tenant import Tenant

pytestmark = pytest.mark.fault
ORDER_BODY = {
    "product_category": "alcohol",
    "quantity": 30,
    "delivery_state": "Lagos",
    "delivery_address": "12 Marina Road, Lagos Island, Lagos",
}


def _order(client: TestClient, tenant: Tenant, quantity: int = 30) -> dict[str, object]:
    return client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **{**ORDER_BODY, "quantity": quantity}},
        headers=auth(tenant.requester.token, new_key("order")),
    ).json()


def _pay(
    client: TestClient, tenant: Tenant, settings: Settings, clock: FixedClock, order: dict[str, object]
) -> None:
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


def test_registry_outage_blocks_order_creation(
    client: TestClient, tenant: Tenant, registry: RegistrySandbox
) -> None:
    registry.script.status = 503
    response = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert response.status_code == 503, response.text
    assert "unreachable" in response.text or "returned 503" in response.text


def test_non_compliant_registry_response_rejects_the_order(
    client: TestClient, tenant: Tenant, registry: RegistrySandbox
) -> None:
    registry.non_compliant()
    response = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "compliance_rejected"


def test_interrupted_issuance_resumes_without_duplicates(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    order = _order(client, tenant, quantity=30)
    _pay(client, tenant, settings, clock, order)
    order_id = uuid.UUID(str(order["id"]))

    # Simulate a process that dies after committing the first chunk.
    with session_factory() as session:
        progress = issue_chunk(
            session,
            order_id=order_id,
            chunk_size=10,
            secure_code_secret=settings.device_hmac_secret,
            now=clock.now(),
        )
        session.commit()
    assert progress.issued == 10
    assert not progress.completed

    final = issue_order(
        session_factory,
        order_id=order_id,
        chunk_size=7,
        secure_code_secret=settings.device_hmac_secret,
        audit_secret=settings.audit_chain_secret,
        revision=settings.revision,
        clock=clock,
    )
    assert final.completed
    with session_factory() as session:
        serials = [stamp.serial for stamp in session.execute(select(Stamp)).scalars()]
        refreshed = session.get_one(Order, order_id)
    assert len(serials) == 30
    assert len(set(serials)) == 30
    assert refreshed.status == "issued"


def test_anchor_outage_keeps_the_message_pending_then_delivers(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    registry: RegistrySandbox,
    session_factory: sessionmaker[Session],
) -> None:
    order = _order(client, tenant, quantity=5)
    _pay(client, tenant, settings, clock, order)
    relay_once(runtime, worker_id="w")  # issuance succeeds and enqueues the anchor request

    registry.anchor_response({"reference": "missing-root"})
    failed = relay_once(runtime, worker_id="w")
    assert failed.failed == 1
    assert failed.dead_lettered == 0
    with session_factory() as session:
        pending = session.execute(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.processed_at.is_(None), OutboxMessage.dead_lettered_at.is_(None))
        ).scalar_one()
    assert pending == 1

    registry.anchor_response(None)
    clock.advance(600)
    recovered = relay_once(runtime, worker_id="w")
    assert recovered.delivered == 1
    with session_factory() as session:
        remaining = session.execute(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.processed_at.is_(None), OutboxMessage.dead_lettered_at.is_(None))
        ).scalar_one()
    assert remaining == 0


def test_failed_inspection_blocks_activation(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    order = _order(client, tenant, quantity=200)
    _pay(client, tenant, settings, clock, order)
    relay_once(runtime, worker_id="w")
    with session_factory() as session:
        stamps = session.execute(select(Stamp).order_by(Stamp.serial)).scalars().all()
        batch_id = str(stamps[0].batch_id)
        serials = [stamp.serial for stamp in stamps]

    plan = client.get(f"/v1/batches/{batch_id}", headers=auth(tenant.operator.token)).json()
    assert plan["status"] == "issued"
    inspection = client.post(
        f"/v1/batches/{batch_id}/inspections",
        json={"defects_found": 9, "defective_serials": serials[:9]},
        headers=auth(tenant.operator.token, new_key("inspect")),
    )
    assert inspection.status_code == 201, inspection.text
    assert inspection.json()["accepted"] is False
    assert inspection.json()["voided_serials"] == 9

    activation = client.post(
        "/v1/stamps/activate",
        json={"serials": serials[10:20]},
        headers=auth(tenant.operator.token, new_key("activate")),
    )
    assert activation.status_code == 200, activation.text
    document = activation.json()
    assert document["changed"] == 0
    assert all(entry["reason"] == "batch failed quality inspection" for entry in document["results"])
    with session_factory() as session:
        active = session.execute(
            select(func.count()).select_from(Stamp).where(Stamp.status == "active")
        ).scalar_one()
        batch = session.get_one(StampBatch, uuid.UUID(batch_id))
    assert active == 0
    assert batch.status == "inspection_failed"


def test_reconciliation_detects_an_injected_defect(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    order = _order(client, tenant, quantity=5)
    _pay(client, tenant, settings, clock, order)
    assert client.post("/v1/ops/reconciliation", headers=auth(tenant.admin.token)).json()["clean"]

    # The ledger is append-only, so a defect can only be injected by a privileged
    # operator disabling the guard. Do exactly that, to prove reconciliation reports the
    # damage rather than silently repairing or ignoring it.
    from sqlalchemy import text

    with session_factory() as session:
        session.execute(text("ALTER TABLE ledger_entries DISABLE TRIGGER USER"))
        session.execute(
            text("UPDATE ledger_entries SET amount_minor = amount_minor + 1000 " "WHERE direction = 'debit'")
        )
        session.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER USER"))
        session.commit()
    report = client.post("/v1/ops/reconciliation", headers=auth(tenant.admin.token)).json()
    assert report["clean"] is False
    kinds = {finding["kind"] for finding in report["findings"]}
    assert kinds & {"unbalanced_journal", "funds_not_conserved"}, report

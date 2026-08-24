"""Stamp accountability: a declared disposition must actually void the stamps."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.enums import StampStatus
from taxstamp.models import Stamp
from taxstamp.runtime import Runtime
from taxstamp.worker.relay import relay_once
from tests.support.api import Remittance, auth, new_key, post_remittance
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e
QUANTITY = 20


def _issued_batch(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> tuple[str, list[str]]:
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
    document = order.json()
    approval = client.post(
        f"/v1/orders/{document['id']}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "documents verified"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    assert approval.status_code == 201, approval.text
    detail = client.get(f"/v1/orders/{document['id']}", headers=auth(tenant.requester.token)).json()
    reference = detail["payment"]["reference"]
    settled = post_remittance(
        client,
        Remittance(
            external_reference=f"BANK-{reference}",
            payment_reference=reference,
            amount_minor=int(document["total_minor"]),
            currency="NGN",
            value_date=clock.now(),
        ),
        secret=settings.payment_webhook_secret,
        now=clock.now(),
    )
    assert settled.status_code == 202, settled.text  # type: ignore[attr-defined]
    relay_once(runtime, worker_id="test-worker")
    relay_once(runtime, worker_id="test-worker")
    with session_factory() as session:
        stamps = list(session.execute(select(Stamp).order_by(Stamp.serial)).scalars().all())
    assert len(stamps) == QUANTITY
    return str(stamps[0].batch_id), [stamp.serial for stamp in stamps]


def test_declared_spoilage_voids_the_stamps_and_balances_the_batch(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    batch_id, serials = _issued_batch(client, runtime, settings, clock, tenant, session_factory)
    response = client.post(
        f"/v1/batches/{batch_id}/dispositions",
        json={
            "kind": "spoiled",
            "serials": serials[:3],
            "reason": "misprinted during application",
            "evidence_reference": "DEST-CERT-0001",
        },
        headers=auth(tenant.operator.token, new_key("dispose")),
    )
    assert response.status_code == 201, response.text
    assert response.json()["stamp_count"] == 3

    with session_factory() as session:
        statuses = {
            row[0]: row[1]
            for row in session.execute(
                select(Stamp.serial, Stamp.status).where(Stamp.serial.in_(serials[:3]))
            ).all()
        }
    assert set(statuses.values()) == {StampStatus.VOID.value}

    account = client.get(f"/v1/batches/{batch_id}/account", headers=auth(tenant.admin.token)).json()
    assert account["issued_count"] == QUANTITY
    assert account["void"] == 3
    assert account["declared_disposed"] == 3
    assert account["balances"] is True

    reconciliation = client.post("/v1/ops/reconciliation", headers=auth(tenant.admin.token)).json()
    assert reconciliation["clean"] is True, reconciliation


def test_disposition_rejects_duplicates_active_and_foreign_serials(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    batch_id, serials = _issued_batch(client, runtime, settings, clock, tenant, session_factory)
    body = {
        "kind": "destroyed",
        "serials": serials[:2],
        "reason": "destroyed under supervision",
        "evidence_reference": "DEST-CERT-0002",
    }
    first = client.post(
        f"/v1/batches/{batch_id}/dispositions",
        json=body,
        headers=auth(tenant.operator.token, new_key("dispose")),
    )
    assert first.status_code == 201, first.text
    repeat = client.post(
        f"/v1/batches/{batch_id}/dispositions",
        json=body,
        headers=auth(tenant.operator.token, new_key("dispose-2")),
    )
    assert repeat.status_code == 409, repeat.text

    inspection = client.post(
        f"/v1/batches/{batch_id}/inspections",
        json={"defects_found": 0, "defective_serials": []},
        headers=auth(tenant.operator.token, new_key("inspect")),
    )
    assert inspection.status_code == 201, inspection.text
    activation = client.post(
        "/v1/stamps/activate",
        json={"serials": serials[5:8]},
        headers=auth(tenant.operator.token, new_key("activate")),
    )
    assert activation.status_code == 200, activation.text
    on_market = client.post(
        f"/v1/batches/{batch_id}/dispositions",
        json={**body, "serials": serials[5:8], "evidence_reference": "DEST-CERT-0003"},
        headers=auth(tenant.operator.token, new_key("dispose-3")),
    )
    assert on_market.status_code == 409, on_market.text

    unknown = client.post(
        f"/v1/batches/{batch_id}/dispositions",
        json={**body, "serials": ["NG-ALC-2026-9999999999-0"], "evidence_reference": "E-4"},
        headers=auth(tenant.operator.token, new_key("dispose-4")),
    )
    assert unknown.status_code in (404, 422), unknown.text

    malformed = client.post(
        f"/v1/batches/{batch_id}/dispositions",
        json={**body, "serials": ["not-a-serial"], "evidence_reference": "E-5"},
        headers=auth(tenant.operator.token, new_key("dispose-5")),
    )
    assert malformed.status_code == 422, malformed.text


def test_only_operators_may_declare_dispositions(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> None:
    batch_id, serials = _issued_batch(client, runtime, settings, clock, tenant, session_factory)
    response = client.post(
        f"/v1/batches/{batch_id}/dispositions",
        json={
            "kind": "returned",
            "serials": serials[:1],
            "reason": "returned to the authority",
            "evidence_reference": "RET-0001",
        },
        headers=auth(tenant.requester.token, new_key("dispose")),
    )
    assert response.status_code == 403, response.text

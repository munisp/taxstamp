"""Drive an order through to issued stamps, over HTTP, for tests that need serials."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import Stamp
from taxstamp.runtime import Runtime
from taxstamp.worker.relay import relay_once
from tests.support.api import Remittance, auth, new_key, post_remittance
from tests.support.tenant import Tenant


def issue_stamps(
    client: TestClient,
    *,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
    quantity: int,
) -> list[str]:
    """The serials of a freshly issued batch, in serial order."""
    order = client.post(
        "/v1/orders",
        json={
            "company_id": str(tenant.company.id),
            "product_id": str(tenant.product.id),
            "quantity": quantity,
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
        serials = list(session.execute(select(Stamp.serial).order_by(Stamp.serial)).scalars().all())
    assert len(serials) >= quantity
    return serials[-quantity:]

"""Migration-backed durable TigerBeetle intent and relay control tests.

The local client below is a deterministic unit-level branch driver only. It does not
provide evidence of TigerBeetle client, cluster, account, or settlement interoperability.
All persistence, constraints, transactions and worker leasing use the real local PostgreSQL
and Redis fixtures.
"""

from __future__ import annotations

import datetime as dt
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from taxstamp.audit import AuditActor
from taxstamp.clock import FixedClock
from taxstamp.enums import PaymentIntentStatus
from taxstamp.errors import IllegalState
from taxstamp.models import Order, OutboxMessage, PaymentIntent, Tariff, TigerBeetleLedgerIntent
from taxstamp.runtime import Runtime
from taxstamp.services.reconciliation import run_reconciliation
from taxstamp.services.tigerbeetle_ledger import LedgerIntentRequest, create_ledger_intent
from taxstamp.tigerbeetle import TigerBeetleCreateResult, TigerBeetleTransfer
from taxstamp.worker.relay import relay_once
from tests.support.tenant import Tenant

pytestmark = pytest.mark.integration

ACCOUNT_DEBIT = "1" * 32
ACCOUNT_CREDIT = "2" * 32
TRANSFER_ID = "a" * 32


@dataclass
class DeterministicTigerBeetleClient:
    stored: TigerBeetleTransfer | None = None
    create_result: TigerBeetleCreateResult = TigerBeetleCreateResult.CREATED
    stored_after_create: TigerBeetleTransfer | None = None
    create_calls: int = 0
    lookup_calls: int = 0

    def lookup_transfer(self, transfer_id: str) -> TigerBeetleTransfer | None:
        self.lookup_calls += 1
        if self.stored is not None and self.stored.transfer_id == transfer_id:
            return self.stored
        return None

    def create_transfer(self, transfer: TigerBeetleTransfer) -> TigerBeetleCreateResult:
        self.create_calls += 1
        if self.create_result is TigerBeetleCreateResult.CREATED:
            self.stored = TigerBeetleTransfer(
                transfer_id=transfer.transfer_id,
                debit_account_id=transfer.debit_account_id,
                credit_account_id=transfer.credit_account_id,
                ledger_code=transfer.ledger_code,
                transfer_code=transfer.transfer_code,
                transfer_flags=transfer.transfer_flags,
                amount_minor=transfer.amount_minor,
                timestamp=123_456,
            )
        elif self.create_result is TigerBeetleCreateResult.EXISTS:
            self.stored = self.stored_after_create
        return self.create_result


def _payment(
    session: Session,
    tenant: Tenant,
    now: dt.datetime,
    *,
    payment_status: PaymentIntentStatus = PaymentIntentStatus.SETTLED,
) -> PaymentIntent:
    tariff = session.execute(select(Tariff)).scalar_one()
    order = Order(
        order_ref=f"ORD-TB-{uuid.uuid4().hex[:20]}",
        company_id=tenant.company.id,
        submitted_by=tenant.requester.principal_id,
        product_category=tariff.product_category,
        quantity=1,
        tariff_id=tariff.id,
        unit_price_minor=tariff.unit_price_minor,
        subtotal_minor=tariff.unit_price_minor,
        vat_bps=0,
        vat_minor=0,
        total_minor=tariff.unit_price_minor,
        currency=tariff.currency,
        status="paid",
        risk_tier="low",
        delivery_state="Lagos",
        delivery_address="12 Marina Road, Lagos Island, Lagos",
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    session.flush()
    payment = PaymentIntent(
        order_id=order.id,
        reference=f"PAY-TB-{uuid.uuid4().hex[:20]}",
        amount_minor=order.total_minor,
        currency=order.currency,
        status=payment_status.value,
        created_at=now,
        settled_at=now if payment_status is PaymentIntentStatus.SETTLED else None,
    )
    session.add(payment)
    session.flush()
    return payment


def _intent_request(payment: PaymentIntent) -> LedgerIntentRequest:
    return LedgerIntentRequest(
        payment_intent_id=payment.id,
        tigerbeetle_transfer_id=TRANSFER_ID,
        debit_account_id=ACCOUNT_DEBIT,
        credit_account_id=ACCOUNT_CREDIT,
        ledger_code=566,
        transfer_code=7,
        transfer_flags=0,
        amount_minor=payment.amount_minor,
        currency=payment.currency,
    )


def _create_intent(runtime: Runtime, tenant: Tenant, clock: FixedClock) -> uuid.UUID:
    with runtime.session_factory() as session:
        payment = _payment(session, tenant, clock.now())
        intent = create_ledger_intent(
            session,
            request=_intent_request(payment),
            now=clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
            revision=runtime.settings.revision,
            actor=AuditActor(
                principal_id=tenant.operator.principal_id,
                subject=tenant.operator.subject,
                role=tenant.operator.role.value,
                company_id=tenant.company.id,
            ),
        )
        session.commit()
        return intent.id


def test_migration_rejects_mutation_of_durable_financial_fields(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    intent_id = _create_intent(runtime, tenant, clock)
    with (
        runtime.engine.begin() as connection,
        pytest.raises(ProgrammingError, match="financial fields are immutable"),
    ):
        connection.execute(
            text("UPDATE tigerbeetle_ledger_intents SET amount_minor = 1 WHERE id = :id"),
            {"id": intent_id},
        )


def test_unsettled_payment_cannot_create_an_external_ledger_intent(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    with runtime.session_factory() as session:
        payment = _payment(
            session,
            tenant,
            clock.now(),
            payment_status=PaymentIntentStatus.AWAITING_PAYMENT,
        )
        with pytest.raises(IllegalState, match="requires a settled payment intent"):
            create_ledger_intent(
                session,
                request=_intent_request(payment),
                now=clock.now(),
                audit_secret=runtime.settings.audit_chain_secret,
                revision=runtime.settings.revision,
                actor=AuditActor(
                    principal_id=tenant.operator.principal_id,
                    subject=tenant.operator.subject,
                    role=tenant.operator.role.value,
                    company_id=tenant.company.id,
                ),
            )


def test_concurrent_duplicate_intent_requests_return_the_same_durable_record(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    with runtime.session_factory() as session:
        payment = _payment(session, tenant, clock.now())
        session.commit()
        payment_id = payment.id

    def create_once() -> uuid.UUID:
        with runtime.session_factory() as session:
            intent = create_ledger_intent(
                session,
                request=LedgerIntentRequest(
                    payment_intent_id=payment_id,
                    tigerbeetle_transfer_id=TRANSFER_ID,
                    debit_account_id=ACCOUNT_DEBIT,
                    credit_account_id=ACCOUNT_CREDIT,
                    ledger_code=566,
                    transfer_code=7,
                    transfer_flags=0,
                    amount_minor=1_250,
                    currency="NGN",
                ),
                now=clock.now(),
                audit_secret=runtime.settings.audit_chain_secret,
                revision=runtime.settings.revision,
                actor=AuditActor(
                    principal_id=tenant.operator.principal_id,
                    subject=tenant.operator.subject,
                    role=tenant.operator.role.value,
                    company_id=tenant.company.id,
                ),
            )
            session.commit()
            return intent.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        intent_ids = list(executor.map(lambda _: create_once(), range(2)))

    assert intent_ids[0] == intent_ids[1]
    with runtime.session_factory() as session:
        intents = session.execute(select(TigerBeetleLedgerIntent)).scalars().all()
        messages = session.execute(select(OutboxMessage)).scalars().all()
    assert len(intents) == 1
    assert len(messages) == 1


def test_relay_looks_up_before_creating_and_posts_matching_transfer(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    intent_id = _create_intent(runtime, tenant, clock)
    runtime.tigerbeetle_client = DeterministicTigerBeetleClient()

    stats = relay_once(runtime, worker_id="tigerbeetle-test-worker")

    assert stats.delivered == 1
    assert stats.failed == 0
    client = runtime.tigerbeetle_client
    assert isinstance(client, DeterministicTigerBeetleClient)
    assert client.create_calls == 1
    assert client.lookup_calls == 2
    with runtime.session_factory() as session:
        intent = session.get(TigerBeetleLedgerIntent, intent_id)
        message = session.execute(select(OutboxMessage)).scalar_one()
    assert intent is not None
    assert intent.state == "posted"
    assert intent.external_timestamp == 123_456
    assert intent.posted_at is not None
    assert message.processed_at is not None


def test_existing_matching_transfer_is_confirmed_without_a_create_call(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    intent_id = _create_intent(runtime, tenant, clock)
    with runtime.session_factory() as session:
        intent = session.get(TigerBeetleLedgerIntent, intent_id)
    assert intent is not None
    runtime.tigerbeetle_client = DeterministicTigerBeetleClient(
        stored=TigerBeetleTransfer(
            transfer_id=intent.tigerbeetle_transfer_id,
            debit_account_id=intent.debit_account_id,
            credit_account_id=intent.credit_account_id,
            ledger_code=int(intent.ledger_code),
            transfer_code=int(intent.transfer_code),
            transfer_flags=int(intent.transfer_flags),
            amount_minor=int(intent.amount_minor),
            timestamp=123_456,
        )
    )

    stats = relay_once(runtime, worker_id="tigerbeetle-test-worker")

    assert stats.delivered == 1
    client = runtime.tigerbeetle_client
    assert isinstance(client, DeterministicTigerBeetleClient)
    assert client.create_calls == 0
    assert client.lookup_calls == 1


def test_exists_result_requires_a_second_matching_lookup_before_posting(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    intent_id = _create_intent(runtime, tenant, clock)
    with runtime.session_factory() as session:
        intent = session.get(TigerBeetleLedgerIntent, intent_id)
    assert intent is not None
    existing = TigerBeetleTransfer(
        transfer_id=intent.tigerbeetle_transfer_id,
        debit_account_id=intent.debit_account_id,
        credit_account_id=intent.credit_account_id,
        ledger_code=int(intent.ledger_code),
        transfer_code=int(intent.transfer_code),
        transfer_flags=int(intent.transfer_flags),
        amount_minor=int(intent.amount_minor),
        timestamp=123_456,
    )
    runtime.tigerbeetle_client = DeterministicTigerBeetleClient(
        create_result=TigerBeetleCreateResult.EXISTS,
        stored_after_create=existing,
    )

    stats = relay_once(runtime, worker_id="tigerbeetle-test-worker")

    assert stats.delivered == 1
    client = runtime.tigerbeetle_client
    assert isinstance(client, DeterministicTigerBeetleClient)
    assert client.create_calls == 1
    assert client.lookup_calls == 2


def test_existing_mismatched_transfer_is_quarantined_without_create(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    intent_id = _create_intent(runtime, tenant, clock)
    runtime.tigerbeetle_client = DeterministicTigerBeetleClient(
        stored=TigerBeetleTransfer(
            transfer_id=TRANSFER_ID,
            debit_account_id="f" * 32,
            credit_account_id=ACCOUNT_CREDIT,
            ledger_code=566,
            transfer_code=7,
            transfer_flags=0,
            amount_minor=12_500,
            timestamp=123_456,
        )
    )

    stats = relay_once(runtime, worker_id="tigerbeetle-test-worker")

    assert stats.delivered == 1
    client = runtime.tigerbeetle_client
    assert isinstance(client, DeterministicTigerBeetleClient)
    assert client.create_calls == 0
    with runtime.session_factory() as session:
        intent = session.get(TigerBeetleLedgerIntent, intent_id)
    assert intent is not None
    assert intent.state == "quarantined"
    assert intent.last_error == "external transfer differs from immutable local intent"
    with runtime.session_factory() as session:
        report = run_reconciliation(
            session,
            now=clock.now(),
            audit_secret=runtime.settings.audit_chain_secret,
        )
    assert any(finding.kind == "tigerbeetle_intent_control_failure" for finding in report.findings)


def test_unconfigured_client_leaves_the_outbox_retryable(
    runtime: Runtime, tenant: Tenant, clock: FixedClock
) -> None:
    _create_intent(runtime, tenant, clock)

    stats = relay_once(runtime, worker_id="tigerbeetle-test-worker")

    assert stats.delivered == 0
    assert stats.failed == 1
    with runtime.session_factory() as session:
        intent = session.execute(select(TigerBeetleLedgerIntent)).scalar_one()
        message = session.execute(select(OutboxMessage)).scalar_one()
    assert intent.state == "ready"
    assert message.processed_at is None
    assert message.dead_lettered_at is None
    assert message.attempts == 1

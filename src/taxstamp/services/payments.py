"""Settlement ingestion and funds posting.

A remittance is accepted only once (unique external reference), matched only on an
exact amount and currency, and posted as a balanced double-entry journal in the same
transaction that advances the order. Mismatches are quarantined for treasury review
instead of being rounded, ignored, or partially applied.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditActor, AuditRecord, record_audit_event
from taxstamp.canonical import canonical_hash
from taxstamp.clock import ensure_utc
from taxstamp.enums import OrderStatus, PaymentIntentStatus, ReceiptStatus, assert_order_transition
from taxstamp.jsontypes import JsonObject
from taxstamp.ledger import Account, Posting, post_journal
from taxstamp.models import Order, OrderTransition, PaymentIntent, PaymentReceipt
from taxstamp.money import Money
from taxstamp.outbox import enqueue

SYSTEM_ACTOR = AuditActor(
    principal_id=None, subject="system:payment-ingestor", role="treasury", company_id=None
)


@dataclass(frozen=True, slots=True)
class RemittanceAdvice:
    external_reference: str
    declared_reference: str
    amount_minor: int
    currency: str
    value_date: dt.datetime
    raw: JsonObject


@dataclass(frozen=True, slots=True)
class IngestResult:
    receipt_id: uuid.UUID
    status: ReceiptStatus
    order_id: uuid.UUID | None
    duplicate: bool


def ingest_remittance(
    session: Session,
    *,
    advice: RemittanceAdvice,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> IngestResult:
    existing = session.execute(
        select(PaymentReceipt).where(PaymentReceipt.external_reference == advice.external_reference)
    ).scalar_one_or_none()
    if existing is not None:
        replay_order_id: uuid.UUID | None = None
        if existing.payment_intent_id is not None:
            replay_order_id = session.execute(
                select(PaymentIntent.order_id).where(PaymentIntent.id == existing.payment_intent_id)
            ).scalar_one()
        return IngestResult(
            receipt_id=existing.id,
            status=ReceiptStatus(existing.status),
            order_id=replay_order_id,
            duplicate=True,
        )

    # The order row is locked before its intents, matching cancel_order: any other
    # acquisition sequence lets a concurrent cancellation and settlement deadlock.
    intent_order_id = session.execute(
        select(PaymentIntent.order_id).where(PaymentIntent.reference == advice.declared_reference)
    ).scalar_one_or_none()

    intent: PaymentIntent | None = None
    order: Order | None = None
    if intent_order_id is not None:
        order = session.execute(
            select(Order).where(Order.id == intent_order_id).with_for_update()
        ).scalar_one()
        intent = session.execute(
            select(PaymentIntent)
            .where(PaymentIntent.reference == advice.declared_reference)
            .with_for_update()
        ).scalar_one()

    if intent is None:
        status = ReceiptStatus.UNKNOWN_REFERENCE
    elif (
        intent.status != PaymentIntentStatus.AWAITING_PAYMENT.value
        or order is None
        or OrderStatus(order.status) is not OrderStatus.AWAITING_PAYMENT
    ):
        # The order was cancelled or has moved on: the money still arrived, so it is
        # quarantined rather than forced onto an order that cannot legally become paid.
        status = ReceiptStatus.ORDER_NOT_PAYABLE
    elif intent.amount_minor != advice.amount_minor or intent.currency != advice.currency:
        status = ReceiptStatus.AMOUNT_MISMATCH
    else:
        status = ReceiptStatus.MATCHED

    receipt = PaymentReceipt(
        external_reference=advice.external_reference,
        payment_intent_id=intent.id if intent is not None else None,
        declared_reference=advice.declared_reference,
        amount_minor=advice.amount_minor,
        currency=advice.currency,
        status=status.value,
        payload_hash=canonical_hash(advice.raw),
        value_date=ensure_utc(advice.value_date),
        ingested_at=now,
    )
    session.add(receipt)
    session.flush()

    order_id: uuid.UUID | None = None
    if status is ReceiptStatus.MATCHED and intent is not None and order is not None:
        order_id = order.id
        assert_order_transition(OrderStatus(order.status), OrderStatus.PAID)
        previous_status = order.status
        order.status = OrderStatus.PAID.value
        order.updated_at = now
        session.add(
            OrderTransition(
                order_id=order.id,
                from_status=previous_status,
                to_status=OrderStatus.PAID.value,
                actor_principal_id=None,
                reason=f"settlement {receipt.external_reference}",
                created_at=now,
            )
        )
        intent.status = PaymentIntentStatus.SETTLED.value
        intent.settled_at = now
        post_journal(
            session,
            reference=f"settlement:{receipt.external_reference}",
            kind="duty_settlement",
            postings=[
                Posting(Account.BANK_COLLECTIONS, "debit", Money(order.total_minor, order.currency)),
                Posting(Account.DUTY_PAYABLE, "credit", Money(order.subtotal_minor, order.currency)),
                Posting(Account.VAT_PAYABLE, "credit", Money(order.vat_minor, order.currency)),
            ],
            now=now,
            order_id=order.id,
            payment_receipt_id=receipt.id,
        )
        enqueue(
            session,
            aggregate_type="order",
            aggregate_id=order.id,
            event_type="order.issue_stamps",
            dedupe_key=f"order.issue_stamps:{order.id}",
            payload={"order_id": str(order.id), "quantity": order.quantity},
            available_at=now,
        )
    elif status in (ReceiptStatus.AMOUNT_MISMATCH, ReceiptStatus.ORDER_NOT_PAYABLE) and intent is not None:
        order_id = intent.order_id
        # Quarantine the funds: they are held as an unapplied liability until treasury acts.
        post_journal(
            session,
            reference=f"unapplied:{receipt.external_reference}",
            kind="unapplied_receipt",
            postings=[
                Posting(Account.BANK_COLLECTIONS, "debit", Money(advice.amount_minor, advice.currency)),
                Posting(Account.UNAPPLIED_RECEIPTS, "credit", Money(advice.amount_minor, advice.currency)),
            ],
            now=now,
            payment_receipt_id=receipt.id,
        )
        enqueue(
            session,
            aggregate_type="payment_receipt",
            aggregate_id=receipt.id,
            event_type="payment.mismatch_requires_review",
            dedupe_key=f"payment.mismatch:{receipt.external_reference}",
            payload={
                "receipt_id": str(receipt.id),
                "reason": status.value,
                "declared_reference": advice.declared_reference,
                "amount_minor": advice.amount_minor,
                "expected_minor": intent.amount_minor,
                "order_status": order.status if order is not None else None,
            },
            available_at=now,
        )

    record_audit_event(
        session,
        actor=SYSTEM_ACTOR,
        record=AuditRecord(
            action="payment.ingest",
            target_type="payment_receipt",
            target_id=str(receipt.id),
            outcome="success" if status is ReceiptStatus.MATCHED else "failure",
            after_state={
                "status": status.value,
                "external_reference": advice.external_reference,
                "amount_minor": advice.amount_minor,
                "currency": advice.currency,
                "order_id": str(order_id) if order_id else None,
            },
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return IngestResult(receipt_id=receipt.id, status=status, order_id=order_id, duplicate=False)

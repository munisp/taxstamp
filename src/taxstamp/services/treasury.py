"""Treasury resolution of quarantined receipts.

Funds held in ``liability:unapplied_receipts`` leave that account exactly once, either
applied to an order that is legally payable or refunded to the payer. Both directions
post a balanced journal, and the unique resolution row makes a second attempt a conflict
rather than a second movement of money.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import (
    OrderStatus,
    PaymentIntentStatus,
    ReceiptStatus,
    ResolutionKind,
    Role,
    assert_order_transition,
)
from taxstamp.errors import Conflict, IllegalState, NotFound, ValidationFailed
from taxstamp.ledger import Account, Posting, post_journal
from taxstamp.models import (
    Order,
    OrderTransition,
    PaymentIntent,
    PaymentReceipt,
    ReceiptResolution,
)
from taxstamp.money import Money
from taxstamp.outbox import enqueue
from taxstamp.services.context import Actor

#: Receipt states that hold money in the unapplied-receipts account.
QUARANTINED = frozenset(
    {
        ReceiptStatus.AMOUNT_MISMATCH,
        ReceiptStatus.ORDER_NOT_PAYABLE,
        ReceiptStatus.UNKNOWN_REFERENCE,
    }
)


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    resolution_id: uuid.UUID
    receipt_id: uuid.UUID
    kind: ResolutionKind
    order_id: uuid.UUID | None
    journal_reference: str


def _locked_quarantined_receipt(session: Session, receipt_id: uuid.UUID) -> PaymentReceipt:
    receipt = session.execute(
        select(PaymentReceipt).where(PaymentReceipt.id == receipt_id).with_for_update()
    ).scalar_one_or_none()
    if receipt is None:
        raise NotFound("receipt not found")
    if ReceiptStatus(receipt.status) not in QUARANTINED:
        raise IllegalState(
            f"receipt in status {receipt.status} holds no unapplied funds",
        )
    existing = session.execute(
        select(ReceiptResolution).where(ReceiptResolution.payment_receipt_id == receipt.id)
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"receipt was already {existing.kind}")
    return receipt


def apply_receipt_to_order(
    session: Session,
    *,
    actor: Actor,
    receipt_id: uuid.UUID,
    order_id: uuid.UUID,
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> ResolutionResult:
    """Apply held funds to an order awaiting payment, for the exact amount only."""
    actor.require_role(Role.TREASURY, Role.ADMIN)
    if not reason.strip():
        raise ValidationFailed("a reason is required")

    # Order before intent, matching cancellation and settlement, so the three paths
    # cannot deadlock against each other.
    order = session.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise NotFound("order not found")
    receipt = _locked_quarantined_receipt(session, receipt_id)
    intent = session.execute(
        select(PaymentIntent).where(PaymentIntent.order_id == order.id).with_for_update()
    ).scalar_one_or_none()
    if intent is None:
        raise IllegalState("order has no payment intent")
    if OrderStatus(order.status) is not OrderStatus.AWAITING_PAYMENT:
        raise IllegalState(f"order in status {order.status} cannot be paid")
    if intent.status != PaymentIntentStatus.AWAITING_PAYMENT.value:
        raise IllegalState(f"payment intent in status {intent.status} cannot be settled")
    if receipt.currency != order.currency:
        raise ValidationFailed("receipt currency does not match the order")
    if receipt.amount_minor != order.total_minor:
        raise ValidationFailed(
            "held amount does not equal the order total; a partial application is not permitted",
            detail={
                "held_minor": str(receipt.amount_minor),
                "order_total_minor": str(order.total_minor),
            },
        )

    assert_order_transition(OrderStatus(order.status), OrderStatus.PAID)
    previous_status = order.status
    order.status = OrderStatus.PAID.value
    order.updated_at = now
    session.add(
        OrderTransition(
            order_id=order.id,
            from_status=previous_status,
            to_status=OrderStatus.PAID.value,
            actor_principal_id=actor.principal_id,
            reason=f"unapplied receipt {receipt.external_reference} applied",
            created_at=now,
        )
    )
    intent.status = PaymentIntentStatus.SETTLED.value
    intent.settled_at = now

    reference = f"apply:{receipt.external_reference}"
    journal = post_journal(
        session,
        reference=reference,
        kind="unapplied_receipt_applied",
        postings=[
            Posting(Account.UNAPPLIED_RECEIPTS, "debit", Money(receipt.amount_minor, receipt.currency)),
            Posting(Account.DUTY_PAYABLE, "credit", Money(order.subtotal_minor, order.currency)),
            Posting(Account.VAT_PAYABLE, "credit", Money(order.vat_minor, order.currency)),
        ],
        now=now,
        order_id=order.id,
        payment_receipt_id=receipt.id,
    )
    resolution = ReceiptResolution(
        payment_receipt_id=receipt.id,
        kind=ResolutionKind.APPLIED.value,
        order_id=order.id,
        journal_id=journal.id,
        reason=reason,
        actor_principal_id=actor.principal_id,
        created_at=now,
    )
    session.add(resolution)
    session.flush()
    enqueue(
        session,
        aggregate_type="order",
        aggregate_id=order.id,
        event_type="order.issue_stamps",
        dedupe_key=f"order.issue_stamps:{order.id}",
        payload={"order_id": str(order.id), "quantity": order.quantity},
        available_at=now,
    )
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="payment.receipt.apply",
            target_type="payment_receipt",
            target_id=str(receipt.id),
            outcome="success",
            after_state={
                "order_id": str(order.id),
                "amount_minor": receipt.amount_minor,
                "currency": receipt.currency,
                "journal_reference": reference,
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return ResolutionResult(
        resolution_id=resolution.id,
        receipt_id=receipt.id,
        kind=ResolutionKind.APPLIED,
        order_id=order.id,
        journal_reference=reference,
    )


def refund_receipt(
    session: Session,
    *,
    actor: Actor,
    receipt_id: uuid.UUID,
    beneficiary_reference: str,
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> ResolutionResult:
    """Return held funds to the payer: the money leaves collections, not revenue."""
    actor.require_role(Role.TREASURY, Role.ADMIN)
    if not reason.strip():
        raise ValidationFailed("a reason is required")
    if not beneficiary_reference.strip():
        raise ValidationFailed("a beneficiary reference is required to refund")
    receipt = _locked_quarantined_receipt(session, receipt_id)

    reference = f"refund:{receipt.external_reference}"
    journal = post_journal(
        session,
        reference=reference,
        kind="unapplied_receipt_refunded",
        postings=[
            Posting(Account.UNAPPLIED_RECEIPTS, "debit", Money(receipt.amount_minor, receipt.currency)),
            Posting(Account.BANK_COLLECTIONS, "credit", Money(receipt.amount_minor, receipt.currency)),
        ],
        now=now,
        payment_receipt_id=receipt.id,
    )
    resolution = ReceiptResolution(
        payment_receipt_id=receipt.id,
        kind=ResolutionKind.REFUNDED.value,
        order_id=None,
        journal_id=journal.id,
        beneficiary_reference=beneficiary_reference,
        reason=reason,
        actor_principal_id=actor.principal_id,
        created_at=now,
    )
    session.add(resolution)
    session.flush()
    enqueue(
        session,
        aggregate_type="payment_receipt",
        aggregate_id=receipt.id,
        event_type="payment.refund_authorised",
        dedupe_key=f"payment.refund:{receipt.external_reference}",
        payload={
            "receipt_id": str(receipt.id),
            "amount_minor": receipt.amount_minor,
            "currency": receipt.currency,
            "beneficiary_reference": beneficiary_reference,
        },
        available_at=now,
    )
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="payment.receipt.refund",
            target_type="payment_receipt",
            target_id=str(receipt.id),
            outcome="success",
            after_state={
                "amount_minor": receipt.amount_minor,
                "currency": receipt.currency,
                "beneficiary_reference": beneficiary_reference,
                "journal_reference": reference,
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return ResolutionResult(
        resolution_id=resolution.id,
        receipt_id=receipt.id,
        kind=ResolutionKind.REFUNDED,
        order_id=None,
        journal_reference=reference,
    )


def unresolved_receipts(session: Session, *, limit: int) -> list[PaymentReceipt]:
    """Quarantined receipts still awaiting a treasury decision."""
    return list(
        session.execute(
            select(PaymentReceipt)
            .outerjoin(ReceiptResolution, ReceiptResolution.payment_receipt_id == PaymentReceipt.id)
            .where(
                PaymentReceipt.status.in_([status.value for status in QUARANTINED]),
                ReceiptResolution.id.is_(None),
            )
            .order_by(PaymentReceipt.ingested_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )

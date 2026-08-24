"""Stamp issuance.

Issuance is chunked, resumable and idempotent: serial blocks are claimed atomically,
each chunk commits its own progress, and a crash mid-way is recovered by re-running the
job. A batch can never issue more stamps than its order paid for.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.audit import AuditActor, AuditRecord, record_audit_event
from taxstamp.clock import Clock, SystemClock
from taxstamp.db import transaction
from taxstamp.enums import BatchStatus, OrderStatus, StampStatus, assert_order_transition
from taxstamp.errors import IllegalState, NotFound
from taxstamp.models import Order, OrderTransition, SerialCounter, Stamp, StampBatch
from taxstamp.outbox import enqueue
from taxstamp.providers.anchor import merkle_root
from taxstamp.security import derive_secure_code, hash_secure_code
from taxstamp.serials import format_serial

SYSTEM_ACTOR = AuditActor(principal_id=None, subject="system:issuer", role="operator", company_id=None)
STAMP_VALIDITY_DAYS = 730


@dataclass(frozen=True, slots=True)
class IssuanceProgress:
    batch_id: uuid.UUID
    requested: int
    issued: int
    completed: bool


def allocate_serial_block(session: Session, *, product_category: str, year: int, count: int) -> int:
    """Claim ``count`` consecutive serials and return the first sequence number."""
    if count <= 0:
        raise IllegalState("serial block size must be positive")
    session.execute(
        pg_insert(SerialCounter)
        .values(product_category=product_category, year=year, next_value=1)
        .on_conflict_do_nothing(index_elements=["product_category", "year"])
    )
    start = session.execute(
        update(SerialCounter)
        .where(
            SerialCounter.product_category == product_category,
            SerialCounter.year == year,
        )
        .values(next_value=SerialCounter.next_value + count)
        .returning(SerialCounter.next_value - count)
    ).scalar_one()
    return int(start)


def ensure_batch(session: Session, *, order: Order, now: dt.datetime) -> StampBatch:
    batch = session.execute(
        select(StampBatch).where(StampBatch.order_id == order.id).with_for_update()
    ).scalar_one_or_none()
    if batch is not None:
        return batch
    batch = StampBatch(
        order_id=order.id,
        requested_count=order.quantity,
        issued_count=0,
        status=BatchStatus.PENDING.value,
        created_at=now,
    )
    session.add(batch)
    session.flush()
    return batch


def issue_chunk(
    session: Session,
    *,
    order_id: uuid.UUID,
    chunk_size: int,
    secure_code_secret: str,
    now: dt.datetime,
) -> IssuanceProgress:
    """Issue at most ``chunk_size`` stamps for an order, committing progress atomically."""
    order = session.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise NotFound("order not found")
    status = OrderStatus(order.status)
    if status not in (OrderStatus.PAID, OrderStatus.ISSUING, OrderStatus.ISSUED):
        raise IllegalState(f"order in status {order.status} is not payable for issuance")

    batch = ensure_batch(session, order=order, now=now)
    if status is OrderStatus.PAID:
        assert_order_transition(status, OrderStatus.ISSUING)
        order.status = OrderStatus.ISSUING.value
        order.updated_at = now
        session.add(
            OrderTransition(
                order_id=order.id,
                from_status=status.value,
                to_status=OrderStatus.ISSUING.value,
                reason="issuance started",
                created_at=now,
            )
        )
        batch.status = BatchStatus.ISSUING.value

    issued_now = int(
        session.execute(
            select(func.count()).select_from(Stamp).where(Stamp.batch_id == batch.id)
        ).scalar_one()
    )
    remaining = batch.requested_count - issued_now
    if remaining <= 0:
        return _complete_batch(session, order=order, batch=batch, issued=issued_now, now=now)

    take = min(chunk_size, remaining)
    year = now.year
    start = allocate_serial_block(session, product_category=order.product_category, year=year, count=take)
    expires_at = now + dt.timedelta(days=STAMP_VALIDITY_DAYS)
    rows: list[dict[str, object]] = []
    for offset in range(take):
        serial = format_serial(order.product_category, year, start + offset)
        code = derive_secure_code(serial, secret=secure_code_secret)
        rows.append(
            {
                "id": uuid.uuid4(),
                "serial": serial,
                "batch_id": batch.id,
                "order_id": order.id,
                "company_id": order.company_id,
                "product_category": order.product_category,
                "status": StampStatus.ISSUED.value,
                "secure_code_hash": hash_secure_code(serial, code, secret=secure_code_secret),
                "issued_at": now,
                "expires_at": expires_at,
                "version": 1,
            }
        )
    session.execute(insert(Stamp), rows)
    batch.issued_count = issued_now + take
    session.flush()

    if batch.issued_count >= batch.requested_count:
        return _complete_batch(session, order=order, batch=batch, issued=batch.issued_count, now=now)
    return IssuanceProgress(
        batch_id=batch.id, requested=batch.requested_count, issued=batch.issued_count, completed=False
    )


def _complete_batch(
    session: Session, *, order: Order, batch: StampBatch, issued: int, now: dt.datetime
) -> IssuanceProgress:
    if batch.status != BatchStatus.ISSUED.value:
        batch.status = BatchStatus.ISSUED.value
        batch.issued_count = issued
        batch.completed_at = now
    if OrderStatus(order.status) is OrderStatus.ISSUING:
        assert_order_transition(OrderStatus.ISSUING, OrderStatus.ISSUED)
        order.status = OrderStatus.ISSUED.value
        order.updated_at = now
        session.add(
            OrderTransition(
                order_id=order.id,
                from_status=OrderStatus.ISSUING.value,
                to_status=OrderStatus.ISSUED.value,
                reason="issuance complete",
                created_at=now,
            )
        )
        serials = list(
            session.execute(
                select(Stamp.serial).where(Stamp.batch_id == batch.id).order_by(Stamp.serial)
            ).scalars()
        )
        enqueue(
            session,
            aggregate_type="stamp_batch",
            aggregate_id=batch.id,
            event_type="batch.anchor_requested",
            dedupe_key=f"batch.anchor:{batch.id}",
            payload={
                "batch_id": str(batch.id),
                "order_id": str(order.id),
                "stamp_count": len(serials),
                "merkle_root": merkle_root(serials),
            },
            available_at=now,
        )
    session.flush()
    return IssuanceProgress(batch_id=batch.id, requested=batch.requested_count, issued=issued, completed=True)


def issue_order(
    session_factory: sessionmaker[Session],
    *,
    order_id: uuid.UUID,
    chunk_size: int,
    secure_code_secret: str,
    audit_secret: str,
    revision: str,
    clock: Clock | None = None,
) -> IssuanceProgress:
    """Run issuance to completion, one transaction per chunk."""
    resolved_clock = clock or SystemClock()
    while True:
        with transaction(session_factory) as session:
            now = resolved_clock.now()
            progress = issue_chunk(
                session,
                order_id=order_id,
                chunk_size=chunk_size,
                secure_code_secret=secure_code_secret,
                now=now,
            )
            if progress.completed:
                record_audit_event(
                    session,
                    actor=SYSTEM_ACTOR,
                    record=AuditRecord(
                        action="batch.issued",
                        target_type="stamp_batch",
                        target_id=str(progress.batch_id),
                        outcome="success",
                        after_state={"issued": progress.issued, "requested": progress.requested},
                    ),
                    occurred_at=now,
                    secret=audit_secret,
                    revision=revision,
                )
        if progress.completed:
            return progress

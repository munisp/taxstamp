"""Stamp accountability: spoilage, damage, destruction and returns.

Stamps are fiscal instruments, so every stamp that leaves the usable population without
being applied to goods must be declared and voided in the same transaction. Anything
else lets a manufacturer hold live stamps that the authority believes were destroyed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import DispositionKind, Role, StampStatus
from taxstamp.errors import Conflict, NotFound, ValidationFailed
from taxstamp.models import Order, Stamp, StampBatch, StampDisposition, StampEvent
from taxstamp.serials import is_valid_serial
from taxstamp.services.context import Actor

MAX_DISPOSITION_SERIALS = 1_000


@dataclass(frozen=True, slots=True)
class DeclareDispositionCommand:
    batch_id: uuid.UUID
    kind: DispositionKind
    serials: tuple[str, ...]
    reason: str
    evidence_reference: str


@dataclass(frozen=True, slots=True)
class DispositionResult:
    disposition_id: uuid.UUID
    batch_id: uuid.UUID
    kind: DispositionKind
    stamp_count: int


def declare_disposition(
    session: Session,
    *,
    actor: Actor,
    command: DeclareDispositionCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> DispositionResult:
    """Void the declared serials and record why they left circulation.

    An already-active stamp cannot be declared spoiled: it is on goods in the market, so
    the correct instrument is a void with a stated reason, not a spoilage declaration.
    """
    actor.require_role(Role.OPERATOR, Role.ADMIN)
    serials = list(dict.fromkeys(command.serials))
    if not serials:
        raise ValidationFailed("at least one serial is required")
    if len(serials) > MAX_DISPOSITION_SERIALS:
        raise ValidationFailed(f"at most {MAX_DISPOSITION_SERIALS} serials may be declared at once")
    malformed = [serial for serial in serials if not is_valid_serial(serial)]
    if malformed:
        raise ValidationFailed(
            "one or more serials are malformed", detail={"invalid_count": str(len(malformed))}
        )
    if not command.reason.strip():
        raise ValidationFailed("a reason is required")
    if not command.evidence_reference.strip():
        raise ValidationFailed("an evidence reference is required")

    batch = session.execute(
        select(StampBatch).where(StampBatch.id == command.batch_id).with_for_update()
    ).scalar_one_or_none()
    if batch is None:
        raise NotFound("batch not found")

    stamps = list(
        session.execute(select(Stamp).where(Stamp.serial.in_(serials)).with_for_update()).scalars().all()
    )
    found = {stamp.serial: stamp for stamp in stamps}
    missing = [serial for serial in serials if serial not in found]
    if missing:
        raise NotFound("one or more serials are not issued")
    foreign = [serial for serial in serials if found[serial].batch_id != batch.id]
    if foreign:
        raise ValidationFailed(
            "one or more serials belong to another batch",
            detail={"foreign_count": str(len(foreign))},
        )
    already_disposed = [serial for serial in serials if StampStatus(found[serial].status) is StampStatus.VOID]
    if already_disposed:
        raise Conflict(
            "one or more serials are already void",
            detail={"void_count": str(len(already_disposed))},
        )
    active = [serial for serial in serials if StampStatus(found[serial].status) is StampStatus.ACTIVE]
    if active:
        raise Conflict(
            "active stamps are on goods in the market and cannot be declared unused",
            detail={"active_count": str(len(active))},
        )

    for serial in serials:
        stamp = found[serial]
        stamp.status = StampStatus.VOID.value
        stamp.voided_at = now
        session.add(
            StampEvent(
                stamp_id=stamp.id,
                event_type="disposed",
                actor_principal_id=actor.principal_id,
                context={
                    "serial": serial,
                    "kind": command.kind.value,
                    "reason": command.reason,
                    "evidence_reference": command.evidence_reference,
                },
                created_at=now,
            )
        )

    disposition = StampDisposition(
        batch_id=batch.id,
        kind=command.kind.value,
        stamp_count=len(serials),
        serials=serials,
        reason=command.reason,
        evidence_reference=command.evidence_reference,
        declared_by=actor.principal_id,
        created_at=now,
    )
    session.add(disposition)
    session.flush()

    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action=f"stamp.disposition.{command.kind.value}",
            target_type="stamp_batch",
            target_id=str(batch.id),
            outcome="success",
            after_state={
                "kind": command.kind.value,
                "stamp_count": len(serials),
                "evidence_reference": command.evidence_reference,
                "serials": list(serials[:50]),
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return DispositionResult(
        disposition_id=disposition.id,
        batch_id=batch.id,
        kind=command.kind,
        stamp_count=len(serials),
    )


@dataclass(frozen=True, slots=True)
class BatchAccount:
    batch_id: uuid.UUID
    order_ref: str
    issued_count: int
    active: int
    unused: int
    void: int
    expired: int
    declared_disposed: int

    @property
    def balances(self) -> bool:
        return self.issued_count == self.active + self.unused + self.void + self.expired


def batch_account(session: Session, batch_id: uuid.UUID) -> BatchAccount:
    """Stamp population of one batch, by status, with declared dispositions."""
    batch = session.get(StampBatch, batch_id)
    if batch is None:
        raise NotFound("batch not found")
    order_ref = session.execute(select(Order.order_ref).where(Order.id == batch.order_id)).scalar_one()
    by_status = {
        str(row[0]): int(row[1])
        for row in session.execute(
            select(Stamp.status, func.count()).where(Stamp.batch_id == batch.id).group_by(Stamp.status)
        ).all()
    }
    disposed = int(
        session.execute(
            select(func.coalesce(func.sum(StampDisposition.stamp_count), 0)).where(
                StampDisposition.batch_id == batch.id
            )
        ).scalar_one()
    )
    return BatchAccount(
        batch_id=batch.id,
        order_ref=order_ref,
        issued_count=int(batch.issued_count),
        active=by_status.get(StampStatus.ACTIVE.value, 0),
        unused=by_status.get(StampStatus.ISSUED.value, 0),
        void=by_status.get(StampStatus.VOID.value, 0),
        expired=by_status.get(StampStatus.EXPIRED.value, 0),
        declared_disposed=disposed,
    )


def unaccounted_dispositions(session: Session) -> list[tuple[uuid.UUID, int, int]]:
    """Dispositions whose declared serials are not all void.

    Returns (disposition_id, declared, still_live) for reconciliation.
    """
    rows = session.execute(
        select(StampDisposition.id, StampDisposition.stamp_count, StampDisposition.serials)
    ).all()
    findings: list[tuple[uuid.UUID, int, int]] = []
    for disposition_id, declared, serials in rows:
        live = int(
            session.execute(
                select(func.count())
                .select_from(Stamp)
                .where(Stamp.serial.in_(list(serials)), Stamp.status != StampStatus.VOID.value)
            ).scalar_one()
        )
        if live:
            findings.append((disposition_id, int(declared), live))
    return findings

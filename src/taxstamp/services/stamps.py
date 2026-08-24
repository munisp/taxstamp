"""Stamp lifecycle: activation, voiding and expiry.

Activation is owner-scoped, batch-gated on quality inspection, idempotent per stamp and
recorded per stamp. A stamp that fails its transition is reported individually rather
than aborting the whole request or being silently skipped.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from taxstamp.audit import AuditActor, AuditRecord, record_audit_event
from taxstamp.enums import BatchStatus, Role, StampStatus, TransitionError, assert_stamp_transition
from taxstamp.errors import Forbidden, NotFound, ValidationFailed
from taxstamp.models import Stamp, StampBatch, StampEvent
from taxstamp.serials import is_valid_serial
from taxstamp.services.context import Actor

MAX_BULK_SERIALS = 1_000
SYSTEM_ACTOR = AuditActor(principal_id=None, subject="system:expiry", role="operator", company_id=None)


@dataclass(frozen=True, slots=True)
class StampOutcome:
    serial: str
    status: str
    changed: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class BulkResult:
    outcomes: tuple[StampOutcome, ...]

    @property
    def changed_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.changed)


def _validate_serials(serials: list[str]) -> list[str]:
    if not serials:
        raise ValidationFailed("at least one serial is required")
    if len(serials) > MAX_BULK_SERIALS:
        raise ValidationFailed(f"at most {MAX_BULK_SERIALS} serials may be submitted at once")
    deduped = list(dict.fromkeys(serials))
    invalid = [serial for serial in deduped if not is_valid_serial(serial)]
    if invalid:
        raise ValidationFailed(
            "one or more serials are malformed", detail={"invalid_count": str(len(invalid))}
        )
    return deduped


def activate_stamps(
    session: Session,
    *,
    actor: Actor,
    serials: list[str],
    now: dt.datetime,
    audit_secret: str,
    revision: str,
    idempotency_key: str | None,
) -> BulkResult:
    actor.require_role(Role.REQUESTER, Role.OPERATOR, Role.ADMIN)
    wanted = _validate_serials(serials)

    stamps = list(
        session.execute(select(Stamp).where(Stamp.serial.in_(wanted)).with_for_update()).scalars().all()
    )
    found = {stamp.serial: stamp for stamp in stamps}
    batch_status: dict[uuid.UUID, str] = {
        row.id: row.status
        for row in session.execute(
            select(StampBatch.id, StampBatch.status).where(
                StampBatch.id.in_({stamp.batch_id for stamp in stamps})
            )
        ).all()
    }

    outcomes: list[StampOutcome] = []
    for serial in wanted:
        stamp = found.get(serial)
        if stamp is None:
            outcomes.append(StampOutcome(serial, "unknown", False, "serial not issued"))
            continue
        if actor.role not in (Role.ADMIN, Role.OPERATOR) and actor.company_id != stamp.company_id:
            raise Forbidden("one or more stamps belong to another company")
        if batch_status.get(stamp.batch_id) == BatchStatus.INSPECTION_FAILED.value:
            outcomes.append(StampOutcome(serial, stamp.status, False, "batch failed quality inspection"))
            continue
        if StampStatus(stamp.status) is StampStatus.ACTIVE:
            outcomes.append(StampOutcome(serial, stamp.status, False, "already active"))
            continue
        if stamp.expires_at <= now:
            outcomes.append(StampOutcome(serial, stamp.status, False, "stamp has expired"))
            continue
        try:
            assert_stamp_transition(StampStatus(stamp.status), StampStatus.ACTIVE)
        except TransitionError as exc:
            outcomes.append(StampOutcome(serial, stamp.status, False, str(exc)))
            continue
        stamp.status = StampStatus.ACTIVE.value
        stamp.activated_at = now
        session.add(
            StampEvent(
                stamp_id=stamp.id,
                event_type="activated",
                actor_principal_id=actor.principal_id,
                context={"serial": serial},
                created_at=now,
            )
        )
        outcomes.append(StampOutcome(serial, StampStatus.ACTIVE.value, True, None))

    session.flush()
    result = BulkResult(tuple(outcomes))
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="stamp.activate",
            target_type="stamp_set",
            target_id=f"{len(wanted)} serials",
            outcome="success",
            after_state={
                "requested": len(wanted),
                "activated": result.changed_count,
                "serials": list(wanted[:50]),
            },
            request_id=actor.request_id,
            idempotency_key=idempotency_key,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return result


def void_stamps(
    session: Session,
    *,
    actor: Actor,
    serials: list[str],
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> BulkResult:
    actor.require_role(Role.ADMIN, Role.OPERATOR)
    if not reason.strip():
        raise ValidationFailed("a reason is required to void stamps")
    wanted = _validate_serials(serials)
    stamps = list(
        session.execute(select(Stamp).where(Stamp.serial.in_(wanted)).with_for_update()).scalars().all()
    )
    found = {stamp.serial: stamp for stamp in stamps}
    outcomes: list[StampOutcome] = []
    for serial in wanted:
        stamp = found.get(serial)
        if stamp is None:
            outcomes.append(StampOutcome(serial, "unknown", False, "serial not issued"))
            continue
        try:
            assert_stamp_transition(StampStatus(stamp.status), StampStatus.VOID)
        except TransitionError as exc:
            outcomes.append(StampOutcome(serial, stamp.status, False, str(exc)))
            continue
        stamp.status = StampStatus.VOID.value
        stamp.voided_at = now
        session.add(
            StampEvent(
                stamp_id=stamp.id,
                event_type="voided",
                actor_principal_id=actor.principal_id,
                context={"serial": serial, "reason": reason},
                created_at=now,
            )
        )
        outcomes.append(StampOutcome(serial, StampStatus.VOID.value, True, None))
    session.flush()
    result = BulkResult(tuple(outcomes))
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="stamp.void",
            target_type="stamp_set",
            target_id=f"{len(wanted)} serials",
            outcome="success",
            after_state={"requested": len(wanted), "voided": result.changed_count, "reason": reason},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return result


def expire_due_stamps(
    session: Session, *, now: dt.datetime, limit: int, audit_secret: str, revision: str
) -> int:
    """Mark stamps whose validity has elapsed as expired. Safe to run repeatedly."""
    due = list(
        session.execute(
            select(Stamp.id)
            .where(
                Stamp.expires_at <= now,
                Stamp.status.in_([StampStatus.ISSUED.value, StampStatus.ACTIVE.value]),
            )
            .order_by(Stamp.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    if not due:
        return 0
    session.execute(update(Stamp).where(Stamp.id.in_(due)).values(status=StampStatus.EXPIRED.value))
    for stamp_id in due:
        session.add(
            StampEvent(
                stamp_id=stamp_id,
                event_type="expired",
                actor_principal_id=None,
                context={"reason": "validity elapsed"},
                created_at=now,
            )
        )
    record_audit_event(
        session,
        actor=SYSTEM_ACTOR,
        record=AuditRecord(
            action="stamp.expire",
            target_type="stamp_set",
            target_id=f"{len(due)} stamps",
            outcome="success",
            after_state={"expired": len(due)},
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    session.flush()
    return len(due)


def get_stamp(session: Session, *, serial: str, actor: Actor) -> Stamp:
    stamp = session.execute(select(Stamp).where(Stamp.serial == serial)).scalar_one_or_none()
    if stamp is None:
        raise NotFound("stamp not found")
    if actor.role in (Role.REQUESTER,):
        actor.require_company(stamp.company_id)
    return stamp


def batch_id_for(session: Session, batch_id: uuid.UUID) -> StampBatch:
    batch = session.get(StampBatch, batch_id)
    if batch is None:
        raise NotFound("batch not found")
    return batch

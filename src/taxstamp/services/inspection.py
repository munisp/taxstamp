"""Batch quality inspection.

The sampling plan is derived from the lot size; the inspector records the observed
defect count and the defective serials. A rejected lot marks the batch as failed, voids
the reported defectives, and blocks activation for the whole batch.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import BatchStatus, Role, StampStatus
from taxstamp.errors import Conflict, IllegalState, NotFound, ValidationFailed
from taxstamp.models import Inspection, Stamp, StampBatch, StampEvent
from taxstamp.quality import sampling_plan
from taxstamp.services.context import Actor


@dataclass(frozen=True, slots=True)
class InspectionCommand:
    batch_id: uuid.UUID
    defects_found: int
    defective_serials: list[str]


@dataclass(frozen=True, slots=True)
class InspectionResult:
    inspection_id: uuid.UUID
    accepted: bool
    sample_size: int
    accept_number: int
    reject_number: int
    voided_serials: int


def record_inspection(
    session: Session,
    *,
    actor: Actor,
    command: InspectionCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> InspectionResult:
    actor.require_role(Role.OPERATOR, Role.ADMIN)
    batch = session.execute(
        select(StampBatch).where(StampBatch.id == command.batch_id).with_for_update()
    ).scalar_one_or_none()
    if batch is None:
        raise NotFound("batch not found")
    if batch.status != BatchStatus.ISSUED.value:
        raise IllegalState(f"batch in status {batch.status} cannot be inspected")
    existing = session.execute(select(Inspection).where(Inspection.batch_id == batch.id)).scalar_one_or_none()
    if existing is not None:
        raise Conflict("this batch has already been inspected")

    plan = sampling_plan(batch.issued_count)
    if command.defects_found > plan.sample_size:
        raise ValidationFailed(f"defects found cannot exceed the sample size of {plan.sample_size}")
    if len(command.defective_serials) > command.defects_found:
        raise ValidationFailed("more defective serials were listed than defects reported")

    accepted = plan.evaluate(command.defects_found)
    inspection = Inspection(
        batch_id=batch.id,
        lot_size=plan.lot_size,
        sample_size=plan.sample_size,
        accept_number=plan.accept_number,
        reject_number=plan.reject_number,
        defects_found=command.defects_found,
        accepted=accepted,
        inspector_principal_id=actor.principal_id,
        defective_serials=list(command.defective_serials),
        created_at=now,
    )
    session.add(inspection)

    voided = 0
    if command.defective_serials:
        stamps = (
            session.execute(
                select(Stamp)
                .where(Stamp.serial.in_(command.defective_serials), Stamp.batch_id == batch.id)
                .with_for_update()
            )
            .scalars()
            .all()
        )
        for stamp in stamps:
            if StampStatus(stamp.status) is StampStatus.VOID:
                continue
            stamp.status = StampStatus.VOID.value
            stamp.voided_at = now
            session.add(
                StampEvent(
                    stamp_id=stamp.id,
                    event_type="voided",
                    actor_principal_id=actor.principal_id,
                    context={"serial": stamp.serial, "reason": "quality defect"},
                    created_at=now,
                )
            )
            voided += 1

    if not accepted:
        batch.status = BatchStatus.INSPECTION_FAILED.value
    session.flush()

    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="batch.inspect",
            target_type="stamp_batch",
            target_id=str(batch.id),
            outcome="success" if accepted else "failure",
            after_state={
                "accepted": accepted,
                "sample_size": plan.sample_size,
                "defects_found": command.defects_found,
                "voided": voided,
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return InspectionResult(
        inspection_id=inspection.id,
        accepted=accepted,
        sample_size=plan.sample_size,
        accept_number=plan.accept_number,
        reject_number=plan.reject_number,
        voided_serials=voided,
    )

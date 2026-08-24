"""Enforcement cases, seizures and chain of custody.

Three properties make this usable as evidence rather than as a workflow convenience:

* a case's revenue at risk is derived from the seizures attached to it and the tariff
  effective when each seizure was recorded, so the figure can be recomputed and
  challenged; nothing accepts a loss estimate from a request body;
* evidence and custody handovers are append-only, and custody handovers are numbered
  and hash-chained per seizure, so a removed or reordered handover is detectable;
* the officer who opened a case cannot be the one who closes it, so a case cannot be
  opened and quietly disposed of by a single person.

No prosecution system is integrated. Referral records that the platform's holder decided
to refer; it does not claim that a court or agency received anything.
"""

from __future__ import annotations

import datetime as dt
import hmac
import uuid
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.canonical import canonical_bytes
from taxstamp.enums import (
    CLOSED_CASE_STATUSES,
    AnomalySeverity,
    CaseKind,
    CaseStatus,
    EvidenceKind,
    Role,
    SeizureStatus,
    TransitionError,
    assert_case_transition,
    assert_seizure_transition,
)
from taxstamp.errors import Conflict, Forbidden, NotFound, ValidationFailed
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import (
    Anomaly,
    CaseEvidence,
    Company,
    Consignment,
    CustodyTransfer,
    EnforcementCase,
    Facility,
    Seizure,
    Stamp,
    Tariff,
    TraceEvent,
    Verification,
)
from taxstamp.serials import CATEGORY_CODES
from taxstamp.services.context import Actor

#: Staff who investigate. Company principals never see enforcement records: a case is
#: about them, and disclosing it would tip off the subject of an investigation.
INVESTIGATORS: frozenset[Role] = frozenset({Role.ANALYST, Role.SUPERVISOR, Role.ADMIN})

#: Deciding to refer or close is a supervisory act, distinct from investigating.
DECIDERS: frozenset[Role] = frozenset({Role.SUPERVISOR, Role.ADMIN})

GENESIS_CUSTODY_HASH = "0" * 64
CUSTODY_PURPOSE = "custody-chain"


@dataclass(frozen=True, slots=True)
class OpenCaseCommand:
    case_ref: str
    kind: CaseKind
    severity: AnomalySeverity
    summary: str
    company_id: uuid.UUID | None
    product_category: str


@dataclass(frozen=True, slots=True)
class EvidenceCommand:
    case_ref: str
    kind: EvidenceKind
    reference: str
    detail: JsonObject


@dataclass(frozen=True, slots=True)
class SeizureCommand:
    seizure_ref: str
    case_ref: str
    location: str
    description: str
    product_category: str
    seized_quantity: int
    facility_code: str | None
    seized_at: dt.datetime
    custodian: str


@dataclass(frozen=True, slots=True)
class CustodyCommand:
    seizure_ref: str
    from_custodian: str
    to_custodian: str
    location: str
    reason: str
    evidence_reference: str
    occurred_at: dt.datetime


@dataclass(frozen=True, slots=True)
class CaseDecisionCommand:
    case_ref: str
    status: CaseStatus
    reason: str


def open_case(
    session: Session,
    *,
    actor: Actor,
    command: OpenCaseCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> EnforcementCase:
    actor.require_role(*INVESTIGATORS)
    if not command.summary.strip():
        raise ValidationFailed("a case summary is required")
    if command.product_category and command.product_category not in CATEGORY_CODES:
        raise ValidationFailed(f"unsupported product category: {command.product_category}")
    if command.company_id is not None and session.get(Company, command.company_id) is None:
        raise NotFound("company not found")
    if session.execute(
        select(EnforcementCase.id).where(EnforcementCase.case_ref == command.case_ref)
    ).scalar_one_or_none():
        raise Conflict("a case with this reference already exists")

    case = EnforcementCase(
        case_ref=command.case_ref,
        kind=command.kind.value,
        status=CaseStatus.OPEN.value,
        severity=command.severity.value,
        company_id=command.company_id,
        product_category=command.product_category,
        summary=command.summary,
        revenue_at_risk_minor=0,
        opened_by=actor.principal_id,
        created_at=now,
    )
    session.add(case)
    session.flush()
    _audit(
        session,
        actor=actor,
        action="case.open",
        case=case,
        now=now,
        secret=audit_secret,
        revision=revision,
    )
    return case


def attach_evidence(
    session: Session,
    *,
    actor: Actor,
    command: EvidenceCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> CaseEvidence:
    """Attach an existing platform record, or a witness statement, to a case."""
    actor.require_role(*INVESTIGATORS)
    case = _locked_case(session, case_ref=command.case_ref)
    if CaseStatus(case.status) in CLOSED_CASE_STATUSES:
        raise Conflict("evidence cannot be added to a closed case")
    if not command.reference.strip():
        raise ValidationFailed("an evidence reference is required")
    _require_referenced_record(session, kind=command.kind, reference=command.reference)
    if session.execute(
        select(CaseEvidence.id).where(
            CaseEvidence.case_id == case.id,
            CaseEvidence.kind == command.kind.value,
            CaseEvidence.reference == command.reference,
        )
    ).scalar_one_or_none():
        raise Conflict("this evidence is already attached to the case")

    evidence = CaseEvidence(
        case_id=case.id,
        kind=command.kind.value,
        reference=command.reference,
        detail=command.detail,
        added_by=actor.principal_id,
        created_at=now,
    )
    session.add(evidence)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="case.attach_evidence",
            target_type="enforcement_case",
            target_id=case.case_ref,
            outcome="success",
            after_state={"kind": command.kind.value, "reference": command.reference},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return evidence


def _require_referenced_record(session: Session, *, kind: EvidenceKind, reference: str) -> None:
    """Refuse evidence that points at nothing, except a free-text statement."""
    if kind is EvidenceKind.STATEMENT:
        return
    if kind is EvidenceKind.STAMP:
        found = session.execute(select(Stamp.id).where(Stamp.serial == reference)).scalar_one_or_none()
    elif kind is EvidenceKind.CONSIGNMENT:
        found = session.execute(
            select(Consignment.id).where(Consignment.consignment_ref == reference)
        ).scalar_one_or_none()
    elif kind is EvidenceKind.TRACE_EVENT:
        found = session.execute(
            select(TraceEvent.id).where(TraceEvent.event_ref == reference)
        ).scalar_one_or_none()
    else:
        try:
            identifier = uuid.UUID(reference)
        except ValueError as exc:
            raise ValidationFailed(f"{kind.value} evidence must reference a record identifier") from exc
        table = Anomaly if kind is EvidenceKind.ANOMALY else Verification
        found = session.execute(select(table.id).where(table.id == identifier)).scalar_one_or_none()
    if found is None:
        raise NotFound(f"no {kind.value} record matches this reference", detail={"reference": reference})


def record_seizure(
    session: Session,
    *,
    actor: Actor,
    command: SeizureCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Seizure:
    """Record goods taken into custody, and open the chain of custody for them."""
    actor.require_role(*INVESTIGATORS)
    if command.seized_quantity <= 0:
        raise ValidationFailed("seized_quantity must be positive")
    if command.product_category not in CATEGORY_CODES:
        raise ValidationFailed(f"unsupported product category: {command.product_category}")
    if command.seized_at > now:
        raise ValidationFailed("a seizure cannot be recorded before it happened")
    if not command.custodian.strip():
        raise ValidationFailed("the first custodian must be named")
    case = _locked_case(session, case_ref=command.case_ref)
    if CaseStatus(case.status) in CLOSED_CASE_STATUSES:
        raise Conflict("a seizure cannot be attached to a closed case")
    if session.execute(
        select(Seizure.id).where(Seizure.seizure_ref == command.seizure_ref)
    ).scalar_one_or_none():
        raise Conflict("a seizure with this reference already exists")

    facility: Facility | None = None
    if command.facility_code is not None:
        facility = session.execute(
            select(Facility).where(Facility.facility_code == command.facility_code)
        ).scalar_one_or_none()
        if facility is None:
            raise NotFound("facility not found", detail={"facility_code": command.facility_code})

    tariff = _tariff_for(session, product_category=command.product_category, at=command.seized_at)
    duty_minor = 0 if tariff is None else tariff.unit_price_minor * command.seized_quantity
    seizure = Seizure(
        seizure_ref=command.seizure_ref,
        case_id=case.id,
        facility_id=None if facility is None else facility.id,
        location=command.location,
        description=command.description,
        product_category=command.product_category,
        seized_quantity=command.seized_quantity,
        estimated_duty_minor=duty_minor,
        currency="NGN" if tariff is None else tariff.currency,
        tariff_id=None if tariff is None else tariff.id,
        status=SeizureStatus.HELD.value,
        seized_at=command.seized_at,
        recorded_by=actor.principal_id,
        created_at=now,
    )
    session.add(seizure)
    session.flush()
    _append_custody(
        session,
        seizure=seizure,
        from_custodian="scene",
        to_custodian=command.custodian,
        location=command.location,
        reason="initial seizure",
        evidence_reference=command.seizure_ref,
        occurred_at=command.seized_at,
        actor=actor,
        now=now,
        chain_secret=audit_secret,
    )
    case.revenue_at_risk_minor = _case_revenue_at_risk(session, case_id=case.id)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="seizure.record",
            target_type="seizure",
            target_id=seizure.seizure_ref,
            outcome="success",
            after_state=seizure_snapshot(seizure),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return seizure


def transfer_custody(
    session: Session,
    *,
    actor: Actor,
    command: CustodyCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> CustodyTransfer:
    """Hand seized goods from one named custodian to the next."""
    actor.require_role(*INVESTIGATORS)
    if command.from_custodian.strip() == command.to_custodian.strip():
        raise ValidationFailed("a handover must name two different custodians")
    if not command.evidence_reference.strip():
        raise ValidationFailed("a handover requires an evidence reference")
    seizure = _locked_seizure(session, seizure_ref=command.seizure_ref)
    if SeizureStatus(seizure.status) is not SeizureStatus.HELD:
        raise Conflict(
            "custody cannot change once goods have left custody",
            detail={"status": seizure.status},
        )
    last = _last_custody(session, seizure_id=seizure.id)
    if last is not None and last.to_custodian != command.from_custodian:
        raise Conflict(
            "the handover does not start from the current custodian",
            detail={"current_custodian": last.to_custodian},
        )
    if last is not None and command.occurred_at < last.occurred_at:
        raise ValidationFailed("a handover cannot precede the previous handover")

    transfer = _append_custody(
        session,
        seizure=seizure,
        from_custodian=command.from_custodian,
        to_custodian=command.to_custodian,
        location=command.location,
        reason=command.reason,
        evidence_reference=command.evidence_reference,
        occurred_at=command.occurred_at,
        actor=actor,
        now=now,
        chain_secret=audit_secret,
    )
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="seizure.transfer_custody",
            target_type="seizure",
            target_id=seizure.seizure_ref,
            outcome="success",
            after_state={
                "sequence": transfer.sequence,
                "from_custodian": transfer.from_custodian,
                "to_custodian": transfer.to_custodian,
                "hash": transfer.hash,
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return transfer


def settle_seizure(
    session: Session,
    *,
    actor: Actor,
    seizure_ref: str,
    status: SeizureStatus,
    reason: str,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> Seizure:
    """Release, forfeit or destroy seized goods. Supervisory decision, reason required."""
    actor.require_role(*DECIDERS)
    if not reason.strip():
        raise ValidationFailed("a reason is required to change the status of seized goods")
    seizure = _locked_seizure(session, seizure_ref=seizure_ref)
    before = seizure_snapshot(seizure)
    try:
        assert_seizure_transition(SeizureStatus(seizure.status), status)
    except TransitionError as exc:
        raise Conflict(str(exc)) from exc
    seizure.status = status.value
    seizure.status_reason = reason
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="seizure.settle",
            target_type="seizure",
            target_id=seizure.seizure_ref,
            outcome="success",
            before_state=before,
            after_state=seizure_snapshot(seizure),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return seizure


def decide_case(
    session: Session,
    *,
    actor: Actor,
    command: CaseDecisionCommand,
    now: dt.datetime,
    audit_secret: str,
    revision: str,
) -> EnforcementCase:
    """Advance a case, including referral and closure.

    Closure and referral are reserved to a supervisor other than the opener: one person
    cannot both raise a case and dispose of it.
    """
    case = _locked_case(session, case_ref=command.case_ref)
    target = command.status
    if target is CaseStatus.UNDER_INVESTIGATION:
        actor.require_role(*INVESTIGATORS)
    else:
        actor.require_role(*DECIDERS)
        if actor.principal_id == case.opened_by:
            raise Forbidden("the officer who opened a case may not refer or close it")
    if not command.reason.strip():
        raise ValidationFailed("a reason is required to change a case's status")
    before = case_snapshot(case)
    try:
        assert_case_transition(CaseStatus(case.status), target)
    except TransitionError as exc:
        raise Conflict(str(exc)) from exc
    closing = target in CLOSED_CASE_STATUSES
    if closing:
        # Queried before the status changes: an autoflush of a half-closed row would
        # trip the constraint requiring a closure time and reason.
        held = session.execute(
            select(func.count(Seizure.id)).where(
                Seizure.case_id == case.id, Seizure.status == SeizureStatus.HELD.value
            )
        ).scalar_one()
        if int(held) > 0:
            raise Conflict(
                "goods are still in custody under this case",
                detail={"seizures_held": str(int(held))},
            )
    case.status = target.value
    if closing:
        case.closed_at = now
        case.closure_reason = command.reason
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="case.decide",
            target_type="enforcement_case",
            target_id=case.case_ref,
            outcome="success",
            before_state=before,
            after_state={**case_snapshot(case), "reason": command.reason},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return case


def _append_custody(
    session: Session,
    *,
    seizure: Seizure,
    from_custodian: str,
    to_custodian: str,
    location: str,
    reason: str,
    evidence_reference: str,
    occurred_at: dt.datetime,
    actor: Actor,
    now: dt.datetime,
    chain_secret: str,
) -> CustodyTransfer:
    last = _last_custody(session, seizure_id=seizure.id)
    sequence = 1 if last is None else last.sequence + 1
    prev_hash = GENESIS_CUSTODY_HASH if last is None else last.hash
    document: JsonObject = {
        "seizure_ref": seizure.seizure_ref,
        "sequence": sequence,
        "from_custodian": from_custodian,
        "to_custodian": to_custodian,
        "location": location,
        "reason": reason,
        "evidence_reference": evidence_reference,
        "occurred_at": occurred_at.isoformat(),
        "recorded_by": str(actor.principal_id),
    }
    transfer = CustodyTransfer(
        seizure_id=seizure.id,
        sequence=sequence,
        from_custodian=from_custodian,
        to_custodian=to_custodian,
        location=location,
        reason=reason,
        evidence_reference=evidence_reference,
        occurred_at=occurred_at,
        recorded_by=actor.principal_id,
        prev_hash=prev_hash,
        hash=custody_hash(prev_hash, document, secret=chain_secret),
        created_at=now,
    )
    session.add(transfer)
    session.flush()
    return transfer


def custody_hash(prev_hash: str, document: JsonObject, *, secret: str) -> str:
    """Keyed chain hash over one handover, domain-separated from the audit chain."""
    key = hmac.new(secret.encode("utf-8"), f"purpose:{CUSTODY_PURPOSE}".encode(), sha256).digest()
    return hmac.new(key, prev_hash.encode("ascii") + b"." + canonical_bytes(document), sha256).hexdigest()


def custody_chain(session: Session, *, seizure: Seizure) -> list[CustodyTransfer]:
    return list(
        session.execute(
            select(CustodyTransfer)
            .where(CustodyTransfer.seizure_id == seizure.id)
            .order_by(CustodyTransfer.sequence)
        )
        .scalars()
        .all()
    )


def custody_chain_intact(session: Session, *, seizure: Seizure, secret: str) -> tuple[bool, int | None]:
    """Recompute a seizure's custody chain.

    Returns whether the chain verifies and, when it does not, the first sequence number
    whose recorded hash disagrees with the recomputed one.
    """
    prev_hash = GENESIS_CUSTODY_HASH
    for expected_sequence, transfer in enumerate(custody_chain(session, seizure=seizure), start=1):
        document: JsonObject = {
            "seizure_ref": seizure.seizure_ref,
            "sequence": transfer.sequence,
            "from_custodian": transfer.from_custodian,
            "to_custodian": transfer.to_custodian,
            "location": transfer.location,
            "reason": transfer.reason,
            "evidence_reference": transfer.evidence_reference,
            "occurred_at": transfer.occurred_at.isoformat(),
            "recorded_by": str(transfer.recorded_by),
        }
        if (
            transfer.sequence != expected_sequence
            or transfer.prev_hash != prev_hash
            or transfer.hash != custody_hash(prev_hash, document, secret=secret)
        ):
            return False, transfer.sequence
        prev_hash = transfer.hash
    return True, None


def _tariff_for(session: Session, *, product_category: str, at: dt.datetime) -> Tariff | None:
    return session.execute(
        select(Tariff)
        .where(
            Tariff.product_category == product_category,
            Tariff.effective_from <= at,
            (Tariff.effective_to.is_(None)) | (Tariff.effective_to > at),
        )
        .order_by(Tariff.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()


def _case_revenue_at_risk(session: Session, *, case_id: uuid.UUID) -> int:
    total = session.execute(
        select(func.coalesce(func.sum(Seizure.estimated_duty_minor), 0)).where(
            Seizure.case_id == case_id,
            Seizure.status != SeizureStatus.RELEASED.value,
        )
    ).scalar_one()
    return int(total)


def _locked_case(session: Session, *, case_ref: str) -> EnforcementCase:
    case = session.execute(
        select(EnforcementCase).where(EnforcementCase.case_ref == case_ref).with_for_update()
    ).scalar_one_or_none()
    if case is None:
        raise NotFound("case not found", detail={"case_ref": case_ref})
    return case


def _locked_seizure(session: Session, *, seizure_ref: str) -> Seizure:
    seizure = session.execute(
        select(Seizure).where(Seizure.seizure_ref == seizure_ref).with_for_update()
    ).scalar_one_or_none()
    if seizure is None:
        raise NotFound("seizure not found", detail={"seizure_ref": seizure_ref})
    return seizure


def _last_custody(session: Session, *, seizure_id: uuid.UUID) -> CustodyTransfer | None:
    return session.execute(
        select(CustodyTransfer)
        .where(CustodyTransfer.seizure_id == seizure_id)
        .order_by(CustodyTransfer.sequence.desc())
        .limit(1)
    ).scalar_one_or_none()


def _audit(
    session: Session,
    *,
    actor: Actor,
    action: str,
    case: EnforcementCase,
    now: dt.datetime,
    secret: str,
    revision: str,
) -> None:
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action=action,
            target_type="enforcement_case",
            target_id=case.case_ref,
            outcome="success",
            after_state=case_snapshot(case),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=secret,
        revision=revision,
    )


def case_snapshot(case: EnforcementCase) -> JsonObject:
    return {
        "case_ref": case.case_ref,
        "kind": case.kind,
        "status": case.status,
        "severity": case.severity,
        "company_id": None if case.company_id is None else str(case.company_id),
        "product_category": case.product_category,
        "summary": case.summary,
        "revenue_at_risk_minor": case.revenue_at_risk_minor,
        "currency": case.currency,
    }


def seizure_snapshot(seizure: Seizure) -> JsonObject:
    return {
        "seizure_ref": seizure.seizure_ref,
        "location": seizure.location,
        "description": seizure.description,
        "product_category": seizure.product_category,
        "seized_quantity": seizure.seized_quantity,
        "estimated_duty_minor": seizure.estimated_duty_minor,
        "currency": seizure.currency,
        "status": seizure.status,
        "status_reason": seizure.status_reason,
        "seized_at": seizure.seized_at.isoformat(),
    }


def case_document(session: Session, *, case: EnforcementCase, audit_secret: str) -> JsonObject:
    seizures = list(
        session.execute(select(Seizure).where(Seizure.case_id == case.id).order_by(Seizure.created_at))
        .scalars()
        .all()
    )
    evidence: JsonArray = [
        {
            "kind": item.kind,
            "reference": item.reference,
            "detail": item.detail,
            "created_at": item.created_at.isoformat(),
        }
        for item in session.execute(
            select(CaseEvidence).where(CaseEvidence.case_id == case.id).order_by(CaseEvidence.created_at)
        )
        .scalars()
        .all()
    ]
    seizure_documents: JsonArray = []
    for seizure in seizures:
        intact, broken_at = custody_chain_intact(session, seizure=seizure, secret=audit_secret)
        seizure_documents.append(
            {
                **seizure_snapshot(seizure),
                "custody": [
                    {
                        "sequence": transfer.sequence,
                        "from_custodian": transfer.from_custodian,
                        "to_custodian": transfer.to_custodian,
                        "location": transfer.location,
                        "reason": transfer.reason,
                        "evidence_reference": transfer.evidence_reference,
                        "occurred_at": transfer.occurred_at.isoformat(),
                        "hash": transfer.hash,
                    }
                    for transfer in custody_chain(session, seizure=seizure)
                ],
                "custody_chain_intact": intact,
                "custody_chain_broken_at": broken_at,
            }
        )
    return {
        **case_snapshot(case),
        "created_at": case.created_at.isoformat(),
        "closed_at": None if case.closed_at is None else case.closed_at.isoformat(),
        "closure_reason": case.closure_reason,
        "evidence": evidence,
        "seizures": seizure_documents,
    }


def case_for_read(session: Session, *, actor: Actor, case_ref: str) -> EnforcementCase:
    actor.require_role(*INVESTIGATORS)
    case = session.execute(
        select(EnforcementCase).where(EnforcementCase.case_ref == case_ref)
    ).scalar_one_or_none()
    if case is None:
        raise NotFound("case not found", detail={"case_ref": case_ref})
    return case


def list_cases(
    session: Session,
    *,
    actor: Actor,
    company_id: uuid.UUID | None,
    status: CaseStatus | None,
    limit: int,
    offset: int,
) -> JsonObject:
    actor.require_role(*INVESTIGATORS)
    if not 1 <= limit <= 200:
        raise ValidationFailed("limit must be between 1 and 200")
    if offset < 0:
        raise ValidationFailed("offset must not be negative")
    conditions = []
    if company_id is not None:
        conditions.append(EnforcementCase.company_id == company_id)
    if status is not None:
        conditions.append(EnforcementCase.status == status.value)
    total = session.execute(select(func.count(EnforcementCase.id)).where(*conditions)).scalar_one()
    rows = (
        session.execute(
            select(EnforcementCase)
            .where(*conditions)
            .order_by(EnforcementCase.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    cases: JsonArray = [{**case_snapshot(case), "created_at": case.created_at.isoformat()} for case in rows]
    return {"total": int(total), "limit": limit, "offset": offset, "cases": cases}

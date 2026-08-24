"""Field verification.

Verification is deterministic: a serial with a valid check character, a matching keyed
secure-code hash, an active status and an unexpired validity window is authentic. There
is no confidence score, no image model and no default-authentic branch. Every attempt,
including failures, is recorded, and repeat scans across devices are flagged.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.enums import StampStatus, VerificationOutcome
from taxstamp.models import Stamp, Verification
from taxstamp.security import hash_secure_code, secure_codes_match
from taxstamp.serials import is_valid_serial
from taxstamp.services.context import Actor

VELOCITY_WINDOW = dt.timedelta(hours=24)
VELOCITY_DISTINCT_DEVICE_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    serial: str
    secure_code: str
    device_id: str
    nonce: str
    latitude_e7: int | None = None
    longitude_e7: int | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    outcome: VerificationOutcome
    authentic: bool
    serial: str
    product_category: str | None
    expires_at: dt.datetime | None
    reason: str


_REASONS: dict[VerificationOutcome, str] = {
    VerificationOutcome.VALID: "serial and secure code match an active stamp",
    VerificationOutcome.UNKNOWN_SERIAL: "serial is not present in the issuance register",
    VerificationOutcome.SECURE_CODE_MISMATCH: "secure code does not match the serial",
    VerificationOutcome.NOT_ACTIVE: "stamp has been issued but not activated",
    VerificationOutcome.VOID: "stamp has been voided",
    VerificationOutcome.EXPIRED: "stamp validity has elapsed",
    VerificationOutcome.VELOCITY_SUSPECT: (
        "stamp verified from an unusual number of distinct devices in the last 24 hours"
    ),
}


def verify(
    session: Session,
    *,
    actor: Actor,
    request: VerificationRequest,
    now: dt.datetime,
    secure_code_secret: str,
    audit_secret: str,
    revision: str,
) -> VerificationResult:
    stamp: Stamp | None = None
    if is_valid_serial(request.serial):
        stamp = session.execute(select(Stamp).where(Stamp.serial == request.serial)).scalar_one_or_none()

    if stamp is None:
        outcome = VerificationOutcome.UNKNOWN_SERIAL
    elif not secure_codes_match(
        hash_secure_code(stamp.serial, request.secure_code, secret=secure_code_secret),
        stamp.secure_code_hash,
    ):
        outcome = VerificationOutcome.SECURE_CODE_MISMATCH
    elif StampStatus(stamp.status) is StampStatus.VOID:
        outcome = VerificationOutcome.VOID
    elif stamp.expires_at <= now or StampStatus(stamp.status) is StampStatus.EXPIRED:
        outcome = VerificationOutcome.EXPIRED
    elif StampStatus(stamp.status) is not StampStatus.ACTIVE:
        outcome = VerificationOutcome.NOT_ACTIVE
    elif _distinct_recent_devices(session, stamp, now, request.device_id) >= (
        VELOCITY_DISTINCT_DEVICE_THRESHOLD
    ):
        outcome = VerificationOutcome.VELOCITY_SUSPECT
    else:
        outcome = VerificationOutcome.VALID

    session.add(
        Verification(
            stamp_id=stamp.id if stamp is not None else None,
            serial_presented=request.serial[:64],
            outcome=outcome.value,
            principal_id=actor.principal_id,
            device_id=request.device_id,
            nonce=request.nonce,
            latitude_e7=request.latitude_e7,
            longitude_e7=request.longitude_e7,
            occurred_at=now,
        )
    )
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="stamp.verify",
            target_type="stamp",
            target_id=request.serial[:64],
            outcome="success" if outcome is VerificationOutcome.VALID else "failure",
            after_state={"outcome": outcome.value, "device_id": request.device_id},
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    session.flush()
    return VerificationResult(
        outcome=outcome,
        authentic=outcome is VerificationOutcome.VALID,
        serial=request.serial,
        product_category=stamp.product_category if stamp is not None else None,
        expires_at=stamp.expires_at if stamp is not None else None,
        reason=_REASONS[outcome],
    )


def _distinct_recent_devices(session: Session, stamp: Stamp, now: dt.datetime, device_id: str) -> int:
    others = session.execute(
        select(func.count(func.distinct(Verification.device_id))).where(
            Verification.stamp_id == stamp.id,
            Verification.outcome == VerificationOutcome.VALID.value,
            Verification.occurred_at >= now - VELOCITY_WINDOW,
            Verification.device_id != device_id,
        )
    ).scalar_one()
    return int(others)

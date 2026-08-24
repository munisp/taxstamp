"""Verification of a stamp, for a field device or a member of the public.

Verification is deterministic: a serial with a valid check character, a matching keyed
secure-code hash, an active status and an unexpired validity window is authentic. There
is no confidence score, no image model and no default-authentic branch. Every attempt,
including failures, is recorded, and repeat scans across devices are flagged.

The consumer channel runs the identical decision but discloses less: a member of the
public learns whether the stamp is authentic, the product it belongs to and where it was
meant to be sold, never the licence holder, the order or the supply chain. Consumer
attempts are recorded against a keyed hash of the caller address, so abuse and cloning
can be seen without retaining anything that identifies a person.
"""

from __future__ import annotations

import datetime as dt
import hmac
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditActor, AuditRecord, record_audit_event
from taxstamp.enums import StampStatus, VerificationChannel, VerificationOutcome
from taxstamp.models import ConsumerVerification, Product, Stamp, Verification
from taxstamp.security import hash_secure_code, secure_codes_match
from taxstamp.serials import is_valid_serial
from taxstamp.services.context import Actor

VELOCITY_WINDOW = dt.timedelta(hours=24)
VELOCITY_DISTINCT_DEVICE_THRESHOLD = 3

#: Consumers are many and uncoordinated, so a genuine pack is legitimately checked by
#: more distinct clients than a genuine pack is scanned by distinct inspection devices.
CONSUMER_VELOCITY_DISTINCT_CLIENT_THRESHOLD = 8

CONSUMER_ACTOR = AuditActor(principal_id=None, subject="public:consumer", role="anonymous", company_id=None)


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


@dataclass(frozen=True, slots=True)
class ConsumerVerificationRequest:
    serial: str
    secure_code: str
    client_address: str
    reported_state: str


@dataclass(frozen=True, slots=True)
class ConsumerVerificationResult:
    """What the public is told. Deliberately narrower than the field-device answer."""

    outcome: VerificationOutcome
    authentic: bool
    serial: str
    brand: str | None
    product_category: str | None
    intended_market: str | None
    reason: str
    advice: str


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

_CONSUMER_ADVICE: dict[VerificationOutcome, str] = {
    VerificationOutcome.VALID: "This stamp is genuine and active.",
    VerificationOutcome.UNKNOWN_SERIAL: "This stamp is not in the register. Do not buy the product.",
    VerificationOutcome.SECURE_CODE_MISMATCH: (
        "The code under the panel does not match the serial. Check you typed it correctly; "
        "if it is correct, the stamp is not genuine."
    ),
    VerificationOutcome.NOT_ACTIVE: (
        "This stamp was issued but never activated for sale. Report where you found it."
    ),
    VerificationOutcome.VOID: "This stamp has been cancelled. Do not buy the product.",
    VerificationOutcome.EXPIRED: "This stamp is no longer valid.",
    VerificationOutcome.VELOCITY_SUSPECT: (
        "This stamp has been checked unusually often, which can mean it has been copied. "
        "Report where you found it."
    ),
}


def _decide(
    session: Session,
    *,
    serial: str,
    secure_code: str,
    now: dt.datetime,
    secure_code_secret: str,
    velocity: int,
    velocity_threshold: int,
) -> tuple[Stamp | None, VerificationOutcome]:
    stamp: Stamp | None = None
    if is_valid_serial(serial):
        stamp = session.execute(select(Stamp).where(Stamp.serial == serial)).scalar_one_or_none()

    if stamp is None:
        return None, VerificationOutcome.UNKNOWN_SERIAL

    status = StampStatus(stamp.status)
    # Ordered worst-first: the first condition that holds is the answer, so a voided
    # stamp is never reported merely as inactive.
    failures: tuple[tuple[bool, VerificationOutcome], ...] = (
        (
            not secure_codes_match(
                hash_secure_code(stamp.serial, secure_code, secret=secure_code_secret),
                stamp.secure_code_hash,
            ),
            VerificationOutcome.SECURE_CODE_MISMATCH,
        ),
        (status is StampStatus.VOID, VerificationOutcome.VOID),
        (stamp.expires_at <= now or status is StampStatus.EXPIRED, VerificationOutcome.EXPIRED),
        (status is not StampStatus.ACTIVE, VerificationOutcome.NOT_ACTIVE),
        (velocity >= velocity_threshold, VerificationOutcome.VELOCITY_SUSPECT),
    )
    for holds, outcome in failures:
        if holds:
            return stamp, outcome
    return stamp, VerificationOutcome.VALID


def verify(
    session: Session,
    *,
    actor: Actor,
    request: VerificationRequest,
    now: dt.datetime,
    secure_code_secret: str,
    audit_secret: str,
    revision: str,
    channel: VerificationChannel = VerificationChannel.FIELD_DEVICE,
) -> VerificationResult:
    stamp, outcome = _decide(
        session,
        serial=request.serial,
        secure_code=request.secure_code,
        now=now,
        secure_code_secret=secure_code_secret,
        velocity=_distinct_recent_devices(session, request.serial, now, request.device_id),
        velocity_threshold=VELOCITY_DISTINCT_DEVICE_THRESHOLD,
    )

    session.add(
        Verification(
            stamp_id=stamp.id if stamp is not None else None,
            serial_presented=request.serial[:64],
            outcome=outcome.value,
            principal_id=actor.principal_id,
            device_id=request.device_id,
            nonce=request.nonce,
            channel=channel.value,
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
            after_state={
                "outcome": outcome.value,
                "device_id": request.device_id,
                "channel": channel.value,
            },
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


def verify_for_consumer(
    session: Session,
    *,
    request: ConsumerVerificationRequest,
    now: dt.datetime,
    secure_code_secret: str,
    fingerprint_secret: str,
    audit_secret: str,
    revision: str,
) -> ConsumerVerificationResult:
    """Run the same decision for an unauthenticated caller, disclosing less."""
    fingerprint = client_fingerprint(request.client_address, secret=fingerprint_secret)
    stamp, outcome = _decide(
        session,
        serial=request.serial,
        secure_code=request.secure_code,
        now=now,
        secure_code_secret=secure_code_secret,
        velocity=_distinct_recent_clients(session, request.serial, now, fingerprint),
        velocity_threshold=CONSUMER_VELOCITY_DISTINCT_CLIENT_THRESHOLD,
    )
    session.add(
        ConsumerVerification(
            stamp_id=stamp.id if stamp is not None else None,
            serial_presented=request.serial[:64],
            outcome=outcome.value,
            client_fingerprint=fingerprint,
            reported_state=request.reported_state[:64],
            occurred_at=now,
        )
    )
    record_audit_event(
        session,
        actor=CONSUMER_ACTOR,
        record=AuditRecord(
            action="stamp.verify_consumer",
            target_type="stamp",
            target_id=request.serial[:64],
            outcome="success" if outcome is VerificationOutcome.VALID else "failure",
            after_state={"outcome": outcome.value, "reported_state": request.reported_state[:64]},
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    session.flush()
    product = _product_for(session, stamp) if stamp is not None else None
    disclosable = outcome in (VerificationOutcome.VALID, VerificationOutcome.VELOCITY_SUSPECT)
    return ConsumerVerificationResult(
        outcome=outcome,
        authentic=outcome is VerificationOutcome.VALID,
        serial=request.serial,
        brand=product.brand if product is not None and disclosable else None,
        product_category=stamp.product_category if stamp is not None and disclosable else None,
        intended_market=product.intended_market if product is not None and disclosable else None,
        reason=_REASONS[outcome],
        advice=_CONSUMER_ADVICE[outcome],
    )


def client_fingerprint(client_address: str, *, secret: str) -> str:
    """Keyed hash of a caller address: linkable across attempts, not reversible to a person."""
    return hmac.new(secret.encode("utf-8"), f"client:{client_address}".encode(), sha256).hexdigest()


def _product_for(session: Session, stamp: Stamp) -> Product | None:
    """The product a stamp's order was placed against, when master data records one."""
    return session.execute(
        select(Product)
        .where(Product.company_id == stamp.company_id, Product.product_category == stamp.product_category)
        .order_by(Product.created_at)
        .limit(1)
    ).scalar_one_or_none()


def _distinct_recent_devices(session: Session, serial: str, now: dt.datetime, device_id: str) -> int:
    others = session.execute(
        select(func.count(func.distinct(Verification.device_id))).where(
            Verification.serial_presented == serial[:64],
            Verification.outcome == VerificationOutcome.VALID.value,
            Verification.occurred_at >= now - VELOCITY_WINDOW,
            Verification.device_id != device_id,
        )
    ).scalar_one()
    return int(others)


def _distinct_recent_clients(session: Session, serial: str, now: dt.datetime, fingerprint: str) -> int:
    others = session.execute(
        select(func.count(func.distinct(ConsumerVerification.client_fingerprint))).where(
            ConsumerVerification.serial_presented == serial[:64],
            ConsumerVerification.outcome == VerificationOutcome.VALID.value,
            ConsumerVerification.occurred_at >= now - VELOCITY_WINDOW,
            ConsumerVerification.client_fingerprint != fingerprint,
        )
    ).scalar_one()
    return int(others)

"""Offline verification: signed revocation bundles and replay-protected sync.

Field inspection happens where there is no network. Two mechanisms make that safe
without inventing authority the platform does not have:

* a **revocation bundle** is a signed, sequenced snapshot of the void and expired
  serials, expressed as a keyed Bloom filter. It is one-sided: a device may see a false
  "possibly revoked" but never a false "clean", and ``valid_until`` bounds how stale an
  offline answer may be. A bundle proves nothing about a serial being genuine — only
  that it is not on the revocation list — so an offline device that gets a negative
  answer still has to confirm online before treating the stamp as verified;
* **scan synchronisation** re-decides every captured scan server-side. The device's own
  verdict is never trusted or stored as an outcome. Batches are numbered per device and
  the pair is unique, so replaying a batch cannot inflate scan counts, and resubmitting
  a sequence with different contents is a conflict rather than an overwrite.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.bloom import HASH_COUNT, BloomFilter, sized_bits
from taxstamp.enums import Role, StampStatus, VerificationChannel, VerificationOutcome
from taxstamp.errors import Conflict, ValidationFailed
from taxstamp.jsontypes import JsonArray, JsonObject
from taxstamp.models import OfflineBundle, OfflineScanBatch, Stamp, Verification
from taxstamp.security import document_hash, sign_document
from taxstamp.services.context import Actor
from taxstamp.services.verification import VerificationRequest, verify

BUNDLE_PURPOSE = "offline-revocation-bundle"

#: Roles that may mint a bundle for distribution to devices.
BUNDLE_PUBLISHERS: frozenset[Role] = frozenset({Role.SUPERVISOR, Role.OPERATOR, Role.ADMIN})

MAX_REVOKED_SERIALS = 200_000
MAX_SCANS_PER_BATCH = 500

#: How long a device may keep trusting a bundle before it must fetch a newer one.
DEFAULT_BUNDLE_TTL = dt.timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class OfflineScan:
    serial: str
    secure_code: str
    nonce: str
    captured_at: dt.datetime
    latitude_e7: int | None = None
    longitude_e7: int | None = None


@dataclass(frozen=True, slots=True)
class SyncCommand:
    device_id: str
    batch_sequence: int
    scans: tuple[OfflineScan, ...]


@dataclass(frozen=True, slots=True)
class SyncResult:
    batch: OfflineScanBatch
    outcomes: tuple[tuple[str, VerificationOutcome], ...]
    duplicates: tuple[str, ...]


def build_bundle(
    session: Session,
    *,
    actor: Actor,
    now: dt.datetime,
    signing_secret: str,
    filter_secret: str,
    audit_secret: str,
    revision: str,
    ttl: dt.timedelta = DEFAULT_BUNDLE_TTL,
) -> OfflineBundle:
    """Publish the next revocation bundle from the current stamp register."""
    actor.require_role(*BUNDLE_PUBLISHERS)
    if ttl <= dt.timedelta(0):
        raise ValidationFailed("a bundle must be valid for a positive period")
    revoked = tuple(
        session.execute(
            select(Stamp.serial)
            .where(Stamp.status.in_((StampStatus.VOID.value, StampStatus.EXPIRED.value)))
            .order_by(Stamp.serial)
            .limit(MAX_REVOKED_SERIALS + 1)
        )
        .scalars()
        .all()
    )
    if len(revoked) > MAX_REVOKED_SERIALS:
        raise Conflict(
            "the revocation list is larger than one bundle may carry; publish a "
            "segmented distribution instead of silently truncating it",
            detail={"limit": str(MAX_REVOKED_SERIALS)},
        )
    bits = sized_bits(len(revoked))
    filter_ = BloomFilter.build(revoked, bits=bits, secret=filter_secret, hash_count=HASH_COUNT)
    sequence = (
        int(session.execute(select(func.coalesce(func.max(OfflineBundle.sequence), 0))).scalar_one()) + 1
    )
    bundle_ref = f"OVB-{now.strftime('%Y%m%d')}-{sequence:06d}"
    document = _bundle_document(
        bundle_ref=bundle_ref,
        sequence=sequence,
        revoked_count=len(revoked),
        filter_bits=filter_.bits,
        filter_hash_count=filter_.hash_count,
        filter_base64=filter_.encode(),
        generated_at=now,
        valid_until=now + ttl,
    )
    bundle = OfflineBundle(
        bundle_ref=bundle_ref,
        sequence=sequence,
        revoked_count=len(revoked),
        filter_bits=filter_.bits,
        filter_hash_count=filter_.hash_count,
        filter_base64=filter_.encode(),
        content_hash=document_hash(document),
        signature=sign_document(document, secret=signing_secret, purpose=BUNDLE_PURPOSE),
        generated_at=now,
        valid_until=now + ttl,
        created_by=actor.principal_id,
        created_at=now,
    )
    session.add(bundle)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="offline.bundle_published",
            target_type="offline_bundle",
            target_id=bundle.bundle_ref,
            outcome="success",
            after_state={
                "sequence": bundle.sequence,
                "revoked_count": bundle.revoked_count,
                "content_hash": bundle.content_hash,
                "valid_until": bundle.valid_until.isoformat(),
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return bundle


def _bundle_document(
    *,
    bundle_ref: str,
    sequence: int,
    revoked_count: int,
    filter_bits: int,
    filter_hash_count: int,
    filter_base64: str,
    generated_at: dt.datetime,
    valid_until: dt.datetime,
) -> JsonObject:
    return {
        "bundle_ref": bundle_ref,
        "sequence": sequence,
        "revoked_count": revoked_count,
        "filter_bits": filter_bits,
        "filter_hash_count": filter_hash_count,
        "filter_base64": filter_base64,
        "generated_at": generated_at.isoformat(),
        "valid_until": valid_until.isoformat(),
    }


def bundle_envelope(bundle: OfflineBundle) -> JsonObject:
    """The document a device receives, including what a negative answer does not prove."""
    return {
        "payload": _bundle_document(
            bundle_ref=bundle.bundle_ref,
            sequence=bundle.sequence,
            revoked_count=bundle.revoked_count,
            filter_bits=bundle.filter_bits,
            filter_hash_count=bundle.filter_hash_count,
            filter_base64=bundle.filter_base64,
            generated_at=bundle.generated_at,
            valid_until=bundle.valid_until,
        ),
        "content_hash": bundle.content_hash,
        "signature": bundle.signature,
        "signature_purpose": BUNDLE_PURPOSE,
        "semantics": (
            "A positive filter answer means the serial may be revoked and must be refused "
            "offline. A negative answer means only that the serial is not on this "
            "revocation list; authenticity still requires an online verification."
        ),
    }


def latest_bundle(session: Session) -> OfflineBundle | None:
    return session.execute(
        select(OfflineBundle).order_by(OfflineBundle.sequence.desc()).limit(1)
    ).scalar_one_or_none()


def bundle_signature_valid(bundle: OfflineBundle, *, signing_secret: str) -> bool:
    document = _bundle_document(
        bundle_ref=bundle.bundle_ref,
        sequence=bundle.sequence,
        revoked_count=bundle.revoked_count,
        filter_bits=bundle.filter_bits,
        filter_hash_count=bundle.filter_hash_count,
        filter_base64=bundle.filter_base64,
        generated_at=bundle.generated_at,
        valid_until=bundle.valid_until,
    )
    return bundle.signature == sign_document(document, secret=signing_secret, purpose=BUNDLE_PURPOSE)


def sync_scans(
    session: Session,
    *,
    actor: Actor,
    command: SyncCommand,
    now: dt.datetime,
    max_staleness: dt.timedelta,
    secure_code_secret: str,
    audit_secret: str,
    revision: str,
) -> SyncResult:
    """Ingest a device's offline captures, re-deciding every one of them server-side."""
    actor.require_role(Role.DEVICE, Role.OPERATOR, Role.ADMIN)
    if not command.scans:
        raise ValidationFailed("a batch must contain at least one scan")
    if len(command.scans) > MAX_SCANS_PER_BATCH:
        raise ValidationFailed(f"at most {MAX_SCANS_PER_BATCH} scans may be submitted per batch")
    if command.batch_sequence < 1:
        raise ValidationFailed("batch_sequence must be positive")

    captured = tuple(scan.captured_at for scan in command.scans)
    captured_from, captured_to = min(captured), max(captured)
    if captured_to > now:
        raise ValidationFailed("a scan cannot be captured in the future")
    if now - captured_from > max_staleness:
        raise ValidationFailed("one or more scans are older than the accepted synchronisation window")
    content_hash = document_hash(_batch_document(command))

    existing = session.execute(
        select(OfflineScanBatch)
        .where(
            OfflineScanBatch.device_id == command.device_id,
            OfflineScanBatch.batch_sequence == command.batch_sequence,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content_hash != content_hash:
            raise Conflict(
                "this batch sequence was already synchronised with different contents",
                detail={"batch_sequence": str(command.batch_sequence)},
            )
        return SyncResult(batch=existing, outcomes=(), duplicates=())

    seen: set[str] = set()
    duplicates: list[str] = []
    outcomes: list[tuple[str, VerificationOutcome]] = []
    for scan in command.scans:
        if scan.nonce in seen or _nonce_used(session, device_id=command.device_id, nonce=scan.nonce):
            duplicates.append(scan.nonce)
            continue
        seen.add(scan.nonce)
        result = verify(
            session,
            actor=actor,
            request=VerificationRequest(
                serial=scan.serial,
                secure_code=scan.secure_code,
                device_id=command.device_id,
                nonce=scan.nonce,
                latitude_e7=scan.latitude_e7,
                longitude_e7=scan.longitude_e7,
            ),
            now=scan.captured_at,
            secure_code_secret=secure_code_secret,
            audit_secret=audit_secret,
            revision=revision,
            channel=VerificationChannel.OFFLINE_DEVICE,
        )
        outcomes.append((scan.serial, result.outcome))

    batch = OfflineScanBatch(
        device_id=command.device_id,
        batch_sequence=command.batch_sequence,
        principal_id=actor.principal_id,
        content_hash=content_hash,
        scan_count=len(command.scans),
        accepted_count=len(outcomes),
        duplicate_count=len(duplicates),
        captured_from=captured_from,
        captured_to=captured_to,
        created_at=now,
    )
    session.add(batch)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="offline.scans_synchronised",
            target_type="offline_scan_batch",
            target_id=f"{command.device_id}:{command.batch_sequence}",
            outcome="success",
            after_state={
                "scan_count": batch.scan_count,
                "accepted_count": batch.accepted_count,
                "duplicate_count": batch.duplicate_count,
                "content_hash": batch.content_hash,
            },
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return SyncResult(batch=batch, outcomes=tuple(outcomes), duplicates=tuple(duplicates))


def _batch_document(command: SyncCommand) -> JsonObject:
    scans: JsonArray = [
        {
            "serial": scan.serial,
            "nonce": scan.nonce,
            "captured_at": scan.captured_at.isoformat(),
            "latitude_e7": scan.latitude_e7,
            "longitude_e7": scan.longitude_e7,
        }
        for scan in command.scans
    ]
    return {
        "device_id": command.device_id,
        "batch_sequence": command.batch_sequence,
        "scans": scans,
    }


def _nonce_used(session: Session, *, device_id: str, nonce: str) -> bool:
    found = session.execute(
        select(Verification.id).where(Verification.device_id == device_id, Verification.nonce == nonce)
    ).scalar_one_or_none()
    return found is not None


def sync_result_document(result: SyncResult) -> JsonObject:
    outcomes: JsonArray = [
        {"serial": serial, "outcome": outcome.value} for serial, outcome in result.outcomes
    ]
    return {
        "device_id": result.batch.device_id,
        "batch_sequence": result.batch.batch_sequence,
        "content_hash": result.batch.content_hash,
        "scan_count": result.batch.scan_count,
        "accepted_count": result.batch.accepted_count,
        "duplicate_count": result.batch.duplicate_count,
        "outcomes": outcomes,
        "rejected_nonces": list(result.duplicates),
    }


def batch_history(session: Session, *, device_id: str, limit: int) -> JsonObject:
    rows = (
        session.execute(
            select(OfflineScanBatch)
            .where(OfflineScanBatch.device_id == device_id)
            .order_by(OfflineScanBatch.batch_sequence.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    batches: JsonArray = [
        {
            "batch_sequence": row.batch_sequence,
            "content_hash": row.content_hash,
            "scan_count": row.scan_count,
            "accepted_count": row.accepted_count,
            "duplicate_count": row.duplicate_count,
            "captured_from": row.captured_from.isoformat(),
            "captured_to": row.captured_to.isoformat(),
        }
        for row in rows
    ]
    return {"device_id": device_id, "batches": batches}

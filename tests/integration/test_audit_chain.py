"""The audit log is append-only and tamper-evident."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from taxstamp.audit import AuditActor, AuditRecord, record_audit_event, verify_audit_chain

pytestmark = pytest.mark.integration
NOW = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
SECRET = "integration-audit-secret-" + "y" * 30
ACTOR = AuditActor(principal_id=None, subject="system:test", role="admin", company_id=None)


def _write(db: Session, index: int) -> None:
    record_audit_event(
        db,
        actor=ACTOR,
        record=AuditRecord(action="test.event", target_type="test", target_id=str(index), outcome="success"),
        occurred_at=NOW + dt.timedelta(seconds=index),
        secret=SECRET,
        revision="test",
    )


def test_chain_links_each_event_to_its_predecessor(db: Session) -> None:
    for index in range(5):
        _write(db, index)
    db.commit()
    hashes = db.execute(text("SELECT prev_hash, hash FROM audit_events ORDER BY seq")).all()
    assert hashes[0][0] == "0" * 64
    for previous, current in zip(hashes, hashes[1:], strict=False):
        assert current[0] == previous[1]
    assert verify_audit_chain(db, secret=SECRET).intact


def test_verification_fails_under_the_wrong_key(db: Session) -> None:
    _write(db, 0)
    db.commit()
    result = verify_audit_chain(db, secret=SECRET + "-different")
    assert not result.intact


def test_verification_detects_a_removed_event(db: Session) -> None:
    for index in range(3):
        _write(db, index)
    db.commit()
    # Bypass the append-only trigger the way a privileged attacker would, to prove the
    # chain still reveals the tampering.
    db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
    db.execute(text("DELETE FROM audit_events WHERE seq = (SELECT max(seq) - 1 FROM audit_events)"))
    db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
    db.commit()
    result = verify_audit_chain(db, secret=SECRET)
    assert not result.intact
    assert result.first_bad_seq is not None

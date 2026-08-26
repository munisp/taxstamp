"""Durable idempotency semantics."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import Session

from taxstamp import idempotency
from taxstamp.enums import Role
from taxstamp.errors import Conflict, IdempotencyKeyReused
from tests.support.factories import create_identity

pytestmark = pytest.mark.integration
NOW = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)


@pytest.fixture
def principal(db: Session) -> uuid.UUID:
    return create_identity(db, role=Role.ADMIN, api_token_secret="x" * 48).principal_id


def _claim(db: Session, key: str, request_hash: str, principal: uuid.UUID) -> object:
    return idempotency.claim(
        db,
        scope="test",
        key=key,
        principal_id=principal,
        request_hash=request_hash,
        now=NOW,
        ttl_seconds=3_600,
    )


def test_replay_returns_the_stored_response(db: Session, principal: uuid.UUID) -> None:
    key = uuid.uuid4().hex
    assert _claim(db, key, "hash-a", principal) is None
    idempotency.complete(db, scope="test", key=key, status=201, body={"ok": True}, now=NOW)
    db.commit()
    replay = _claim(db, key, "hash-a", principal)
    assert replay is not None
    assert replay.status == 201  # type: ignore[attr-defined]
    assert replay.body == {"ok": True}  # type: ignore[attr-defined]


def test_same_key_with_different_payload_is_rejected(db: Session, principal: uuid.UUID) -> None:
    key = uuid.uuid4().hex
    _claim(db, key, "hash-a", principal)
    idempotency.complete(db, scope="test", key=key, status=201, body={"ok": True}, now=NOW)
    db.commit()
    with pytest.raises(IdempotencyKeyReused):
        _claim(db, key, "hash-b", principal)


def test_in_flight_duplicate_is_rejected(db: Session, principal: uuid.UUID) -> None:
    key = uuid.uuid4().hex
    _claim(db, key, "hash-a", principal)
    db.commit()
    with pytest.raises(Conflict):
        _claim(db, key, "hash-a", principal)


def test_replay_by_another_principal_is_rejected(db: Session, principal: uuid.UUID) -> None:
    key = uuid.uuid4().hex
    assert _claim(db, key, "hash-a", principal) is None
    idempotency.complete(db, scope="test", key=key, status=201, body={"ok": True}, now=NOW)
    db.commit()
    other = create_identity(db, role=Role.ADMIN, api_token_secret="x" * 48).principal_id
    db.commit()
    with pytest.raises(Conflict, match="another principal"):
        _claim(db, key, "hash-a", other)

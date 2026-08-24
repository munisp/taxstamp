"""Database-level invariants hold even if application code is bypassed."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from taxstamp.audit import AuditActor, AuditRecord, record_audit_event
from taxstamp.enums import Role
from taxstamp.ledger import Account, Posting, post_journal
from taxstamp.models import Order
from taxstamp.money import Money
from tests.support.factories import create_company, create_identity, create_tariff

pytestmark = pytest.mark.integration
NOW = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
SECRET = "integration-audit-secret-" + "x" * 30


def test_audit_events_cannot_be_updated_or_deleted(db: Session) -> None:
    event = record_audit_event(
        db,
        actor=AuditActor(principal_id=None, subject="system:test", role="admin", company_id=None),
        record=AuditRecord(action="test.event", target_type="test", target_id="1", outcome="success"),
        occurred_at=NOW,
        secret=SECRET,
        revision="test",
    )
    db.commit()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE audit_events SET action = 'tampered' WHERE seq = :seq"), {"seq": event.seq})
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM audit_events WHERE seq = :seq"), {"seq": event.seq})
    db.rollback()


def test_unbalanced_journal_is_rejected_at_commit(db: Session) -> None:
    db.execute(
        text(
            "INSERT INTO journals (id, reference, kind, currency, created_at) "
            "VALUES (:id, :ref, 'test', 'NGN', :now)"
        ),
        {"id": uuid.uuid4(), "ref": f"J-{uuid.uuid4().hex[:8]}", "now": NOW},
    )
    journal_id = db.execute(text("SELECT id FROM journals LIMIT 1")).scalar_one()
    db.execute(
        text(
            "INSERT INTO ledger_entries (id, journal_id, account, direction, amount_minor,"
            " currency, created_at) VALUES (:id, :journal, 'asset:bank_collections', 'debit',"
            " 100, 'NGN', :now)"
        ),
        {"id": uuid.uuid4(), "journal": journal_id, "now": NOW},
    )
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_balanced_journal_commits(db: Session) -> None:
    post_journal(
        db,
        reference=f"J-{uuid.uuid4().hex[:8]}",
        kind="test",
        postings=[
            Posting(Account.BANK_COLLECTIONS, "debit", Money(1_000, "NGN")),
            Posting(Account.DUTY_PAYABLE, "credit", Money(1_000, "NGN")),
        ],
        now=NOW,
    )
    db.commit()


def test_negative_quantity_order_is_rejected(db: Session) -> None:
    company = create_company(db)
    identity = create_identity(db, role=Role.REQUESTER, api_token_secret="x" * 48, company_id=company.id)
    tariff = create_tariff(db)
    db.add(
        Order(
            order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
            company_id=company.id,
            submitted_by=identity.principal_id,
            tariff_id=tariff.id,
            product_category="alcohol",
            quantity=-5,
            unit_price_minor=100,
            subtotal_minor=-500,
            vat_bps=750,
            vat_minor=0,
            total_minor=-500,
            currency="NGN",
            status="submitted",
            risk_tier="low",
            delivery_state="Lagos",
            delivery_address="1 Test Road, Lagos",
            compliance_evidence={},
            created_at=NOW,
            updated_at=NOW,
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        db.commit()
    db.rollback()


def test_one_provider_subject_cannot_be_linked_to_two_principals(db: Session) -> None:
    """Two principals sharing a provider subject would make the audit identity ambiguous."""
    create_identity(db, role=Role.ADMIN, api_token_secret="x" * 48, oidc_subject="idp-shared")
    db.commit()
    with pytest.raises(IntegrityError):
        create_identity(db, role=Role.AUDITOR, api_token_secret="x" * 48, oidc_subject="idp-shared")
    db.rollback()


def test_devices_cannot_be_federated(db: Session) -> None:
    """A device fleet must keep its own credentials.

    A device has no interactive login, and must be able to verify a stamp while the
    identity provider is unreachable, so linking one to a provider subject is refused by
    the database rather than only by application code.
    """
    company = create_company(db)
    db.commit()
    with pytest.raises((IntegrityError, DBAPIError)):
        create_identity(
            db,
            role=Role.DEVICE,
            api_token_secret="x" * 48,
            company_id=company.id,
            oidc_subject="idp-device",
        )
    db.rollback()

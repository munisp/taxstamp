"""SQLAlchemy models.

Invariants that can be enforced by the database are enforced there: unique
constraints on every external reference, CHECK constraints on states, amounts and
signs, foreign keys with explicit ON DELETE behaviour, and a deferred constraint
trigger that rejects an unbalanced ledger journal at COMMIT time.
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from taxstamp.enums import (
    ApprovalDecision,
    ApprovalLevel,
    BatchStatus,
    KybStatus,
    OrderStatus,
    PaymentIntentStatus,
    ReceiptStatus,
    RiskTier,
    Role,
    StampStatus,
    VerificationOutcome,
)
from taxstamp.jsontypes import JsonObject

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


def _enum_check(column: str, enum_cls: type[StrEnum]) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=f"{column}_valid")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tin: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kyb_status: Mapped[str] = mapped_column(String(32), nullable=False, default=KybStatus.UNVERIFIED)
    kyb_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    risk_tier: Mapped[str] = mapped_column(String(16), nullable=False, default=RiskTier.MEDIUM)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("kyb_status", KybStatus),
        _enum_check("risk_tier", RiskTier),
        CheckConstraint("char_length(tin) >= 8", name="tin_length"),
    )


class Principal(Base):
    """An authenticated actor: a staff user, a company user, or a field device."""

    __tablename__ = "principals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    subject: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT")
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("role", Role),
        CheckConstraint(
            "(role IN ('requester') AND company_id IS NOT NULL) OR role NOT IN ('requester')",
            name="requester_requires_company",
        ),
    )


class Credential(Base):
    """A bearer credential. Only a keyed hash of the token is stored."""

    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = _uuid_pk()
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    principal: Mapped[Principal] = relationship(lazy="joined")


class Tariff(Base):
    """Effective-dated unit price and VAT rate. Pricing provenance lives in the database."""

    __tablename__ = "tariffs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_category: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    vat_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    statutory_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("unit_price_minor > 0", name="unit_price_positive"),
        CheckConstraint("vat_bps >= 0 AND vat_bps <= 10000", name="vat_bps_range"),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="effective_range"),
        Index(
            "ix_tariffs_category_current",
            "product_category",
            "effective_from",
            unique=True,
        ),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_ref: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    product_category: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tariff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tariffs.id", ondelete="RESTRICT"), nullable=False
    )
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vat_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    vat_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_address: Mapped[str] = mapped_column(Text, nullable=False)
    compliance_evidence: Mapped[JsonObject | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _created_at()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        _enum_check("status", OrderStatus),
        _enum_check("risk_tier", RiskTier),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_minor > 0", name="unit_price_positive"),
        CheckConstraint("subtotal_minor > 0", name="subtotal_positive"),
        CheckConstraint("vat_minor >= 0", name="vat_non_negative"),
        CheckConstraint("subtotal_minor = unit_price_minor * quantity", name="subtotal_matches_quantity"),
        CheckConstraint("total_minor = subtotal_minor + vat_minor", name="total_matches_components"),
        Index("ix_orders_company_status", "company_id", "status"),
    )


class OrderTransition(Base):
    """Append-only history of order status changes."""

    __tablename__ = "order_transitions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (Index("ix_order_transitions_order", "order_id", "created_at"),)


class Approval(Base):
    """Maker-checker approval. One decision per level, never by the submitter."""

    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("order_id", "level", name="uq_approvals_order_level"),
        _enum_check("level", ApprovalLevel),
        _enum_check("decision", ApprovalDecision),
    )


class PaymentIntent(Base):
    __tablename__ = "payment_intents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()
    settled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _enum_check("status", PaymentIntentStatus),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
    )


class PaymentReceipt(Base):
    """A remittance advice ingested from the collecting bank.

    ``external_reference`` is unique: replaying the same settlement notification can
    never create a second receipt or a second ledger effect.
    """

    __tablename__ = "payment_receipts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    external_reference: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    payment_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_intents.id", ondelete="RESTRICT")
    )
    declared_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    value_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("status", ReceiptStatus),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
    )


class Journal(Base):
    """A double-entry journal. Balance is enforced by a deferred constraint trigger."""

    __tablename__ = "journals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    reference: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT")
    )
    payment_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_receipts.id", ondelete="RESTRICT")
    )
    reverses_journal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT"), unique=True
    )
    created_at: Mapped[dt.datetime] = _created_at()


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    journal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT"), nullable=False
    )
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(6), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("direction IN ('debit', 'credit')", name="direction_valid"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        Index("ix_ledger_entries_journal", "journal_id"),
        Index("ix_ledger_entries_account", "account", "created_at"),
    )


class SerialCounter(Base):
    """Monotonic serial allocator per (category, year). Blocks are claimed atomically."""

    __tablename__ = "serial_counters"

    product_category: Mapped[str] = mapped_column(String(64), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    __table_args__ = (CheckConstraint("next_value >= 1", name="next_value_positive"),)


class StampBatch(Base):
    __tablename__ = "stamp_batches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    requested_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _enum_check("status", BatchStatus),
        CheckConstraint("requested_count > 0", name="requested_positive"),
        CheckConstraint(
            "issued_count >= 0 AND issued_count <= requested_count", name="issued_within_requested"
        ),
    )


class Stamp(Base):
    __tablename__ = "stamps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    serial: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamp_batches.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    product_category: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    secure_code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[dt.datetime] = _created_at()
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        _enum_check("status", StampStatus),
        CheckConstraint(
            "(status <> 'active') OR (activated_at IS NOT NULL)", name="active_requires_timestamp"
        ),
        CheckConstraint("(status <> 'void') OR (voided_at IS NOT NULL)", name="void_requires_timestamp"),
        Index("ix_stamps_batch", "batch_id"),
        Index("ix_stamps_company_status", "company_id", "status"),
    )


class StampEvent(Base):
    """Append-only per-stamp lifecycle history."""

    __tablename__ = "stamp_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    stamp_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamps.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT")
    )
    context: Mapped[JsonObject] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (Index("ix_stamp_events_stamp", "stamp_id", "created_at"),)


class Inspection(Base):
    """Quality inspection of an issued batch using an ANSI/ASQ Z1.4-style plan."""

    __tablename__ = "inspections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamp_batches.id", ondelete="RESTRICT"), nullable=False
    )
    lot_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    accept_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reject_number: Mapped[int] = mapped_column(Integer, nullable=False)
    defects_found: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inspector_principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    defective_serials: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("sample_size > 0 AND sample_size <= lot_size", name="sample_within_lot"),
        CheckConstraint("defects_found >= 0", name="defects_non_negative"),
        UniqueConstraint("batch_id", name="uq_inspections_batch_id"),
    )


class Verification(Base):
    """Every verification attempt is recorded, including failures."""

    __tablename__ = "verifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    stamp_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamps.id", ondelete="RESTRICT")
    )
    serial_presented: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    latitude_e7: Mapped[int | None] = mapped_column(BigInteger)
    longitude_e7: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("outcome", VerificationOutcome),
        Index("ix_verifications_serial", "serial_presented", "occurred_at"),
        Index("ix_verifications_stamp", "stamp_id", "occurred_at"),
    )


class IdempotencyRecord(Base):
    """Durable outcome of a mutating request, keyed by scope + client key."""

    __tablename__ = "idempotency_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[JsonObject | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _created_at()
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_records_scope_key"),
        CheckConstraint("state IN ('in_progress', 'completed')", name="state_valid"),
    )


class OutboxMessage(Base):
    """Transactional outbox: side effects are committed with the state change."""

    __tablename__ = "outbox_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload: Mapped[JsonObject] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()
    available_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_outbox_pending", "available_at", postgresql_where=text("processed_at IS NULL")),
    )


class AuditEvent(Base):
    """Tamper-evident audit trail.

    ``prev_hash``/``hash`` form a keyed hash chain. Database triggers reject UPDATE
    and DELETE, so history cannot be rewritten by the application role.
    """

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_principal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    before_state: Mapped[JsonObject | None] = mapped_column(JSONB)
    after_state: Mapped[JsonObject | None] = mapped_column(JSONB)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="outcome_valid"),
        Index("ix_audit_events_target", "target_type", "target_id"),
        Index("ix_audit_events_actor", "actor_subject", "occurred_at"),
    )


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[dt.datetime] = _created_at()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    findings: Mapped[JsonObject] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (CheckConstraint("status IN ('clean', 'findings', 'error')", name="status_valid"),)

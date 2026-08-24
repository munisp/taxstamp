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
    AnomalyKind,
    AnomalySeverity,
    ApprovalDecision,
    ApprovalLevel,
    BatchStatus,
    CaseKind,
    CaseStatus,
    ConsignmentStatus,
    CustomsRegime,
    DispositionKind,
    EvidenceKind,
    ExportKind,
    FacilityKind,
    KybStatus,
    LicenceStatus,
    LicenceType,
    OrderStatus,
    PaymentIntentStatus,
    ProductStatus,
    ReceiptStatus,
    ResolutionKind,
    RiskTier,
    Role,
    SeizureStatus,
    StampStatus,
    TraceEventType,
    TradeUnitLevel,
    TradeUnitStatus,
    VerificationChannel,
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
    #: The identity provider's immutable subject for this person, when their sessions are
    #: federated. Set explicitly by an administrator: a token for an unlinked subject is
    #: refused rather than provisioning a principal, so the provider cannot mint access.
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("role", Role),
        CheckConstraint(
            "(role IN ('requester') AND company_id IS NOT NULL) OR role NOT IN ('requester')",
            name="requester_requires_company",
        ),
        CheckConstraint(
            "oidc_subject IS NULL OR role <> 'device'",
            name="devices_are_not_federated",
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


class Licence(Base):
    """An excise licence: the legal entitlement to manufacture, import or distribute.

    A company may only procure stamps for a product category covered by an effective
    licence of an ordering type, which is what FCTC Article 6 licensing control means in
    practice. Suspension and revocation are recorded, never deleted.
    """

    __tablename__ = "licences"

    id: Mapped[uuid.UUID] = _uuid_pk()
    licence_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    licence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=LicenceStatus.ACTIVE)
    valid_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    statutory_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    status_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("licence_type", LicenceType),
        _enum_check("status", LicenceStatus),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="validity_range"),
        CheckConstraint("jsonb_array_length(product_categories) > 0", name="categories_present"),
        Index("ix_licences_company_status", "company_id", "status"),
    )


class Product(Base):
    """Registered product master data: the SKU a stamp order is placed against.

    ``intended_market`` is the market of intended retail sale, the field the EU
    traceability regime requires so diverted product can be recognised.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False)
    product_category: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_size: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(16), nullable=False)
    intended_market: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ProductStatus.ACTIVE)
    withdrawn_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("status", ProductStatus),
        CheckConstraint("pack_size > 0", name="pack_size_positive"),
        UniqueConstraint("company_id", "sku", name="uq_products_company_id_sku"),
        Index("ix_products_company_category", "company_id", "product_category"),
    )


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
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT")
    )
    # Nullable only because orders created before licensing existed cannot be relicensed
    # retroactively; the service always sets it, and reconciliation reports any live
    # order whose licence is missing or no longer effective.
    licence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("licences.id", ondelete="RESTRICT")
    )
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


class StampDisposition(Base):
    """Stamps that left the usable population without being applied to goods.

    Every disposed serial is voided in the same transaction, so the batch population
    always reconciles: issued = active + issued-unused + void.
    """

    __tablename__ = "stamp_dispositions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamp_batches.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    stamp_count: Mapped[int] = mapped_column(Integer, nullable=False)
    serials: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    declared_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("kind", DispositionKind),
        CheckConstraint("stamp_count > 0", name="stamp_count_positive"),
        CheckConstraint("stamp_count = jsonb_array_length(serials)", name="stamp_count_matches_serials"),
        Index("ix_stamp_dispositions_batch", "batch_id", "created_at"),
    )


class ReceiptResolution(Base):
    """How a quarantined receipt was cleared out of the unapplied-receipts account.

    Unique per receipt: funds held once can be applied or refunded once.
    """

    __tablename__ = "receipt_resolutions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    payment_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_receipts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT")
    )
    journal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journals.id", ondelete="RESTRICT"), nullable=False
    )
    beneficiary_reference: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("kind", ResolutionKind),
        CheckConstraint("(kind <> 'applied') OR (order_id IS NOT NULL)", name="applied_requires_order"),
        CheckConstraint(
            "(kind <> 'refunded') OR (char_length(beneficiary_reference) > 0)",
            name="refund_requires_beneficiary",
        ),
    )


class Facility(Base):
    """A physical location in the supply chain, with coordinates for geometry checks."""

    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    facility_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    latitude_e7: Mapped[int] = mapped_column(BigInteger, nullable=False)
    longitude_e7: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("kind", FacilityKind),
        CheckConstraint("latitude_e7 BETWEEN -900000000 AND 900000000", name="latitude_range"),
        CheckConstraint("longitude_e7 BETWEEN -1800000000 AND 1800000000", name="longitude_range"),
        CheckConstraint("char_length(country) = 2", name="country_iso2"),
        Index("ix_facilities_company", "company_id", "kind"),
    )


class TradeUnit(Base):
    """An aggregation unit: a case of stamped items, a pallet of cases, a container.

    ``stamp_count`` is the number of stamps the unit transitively contains. Movement
    events are recorded against units, which is why the count has to be maintained
    here rather than recomputed from a claim in a request body.
    """

    __tablename__ = "trade_units"

    id: Mapped[uuid.UUID] = _uuid_pk()
    unit_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_units.id", ondelete="RESTRICT")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    stamp_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        _enum_check("level", TradeUnitLevel),
        _enum_check("status", TradeUnitStatus),
        CheckConstraint("stamp_count > 0", name="stamp_count_positive"),
        CheckConstraint("parent_unit_id IS NULL OR parent_unit_id <> id", name="parent_not_self"),
        Index("ix_trade_units_company_status", "company_id", "status"),
        Index("ix_trade_units_parent", "parent_unit_id"),
    )


class UnitMembership(Base):
    """Which stamp sits in which case, and when it was removed.

    A partial unique index enforces that a stamp is in at most one open unit, so a
    serial cannot be aggregated into two cases at the same time.
    """

    __tablename__ = "unit_memberships"

    id: Mapped[uuid.UUID] = _uuid_pk()
    trade_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_units.id", ondelete="RESTRICT"), nullable=False
    )
    stamp_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamps.id", ondelete="RESTRICT"), nullable=False
    )
    added_at: Mapped[dt.datetime] = _created_at()
    removed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_unit_memberships_active_stamp",
            "stamp_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index("ix_unit_memberships_unit", "trade_unit_id"),
    )


class TraceEvent(Base):
    """A recorded supply-chain movement of one trade unit.

    ``observed_stamp_count`` is what the reporting party says it handled. It is stored
    even when it disagrees with the unit's contents, because the disagreement is the
    evidence; an anomaly is raised instead of overwriting the claim.
    """

    __tablename__ = "trace_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    trade_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_units.id", ondelete="RESTRICT"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    origin_facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    destination_facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="RESTRICT")
    )
    consignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("consignments.id", ondelete="RESTRICT")
    )
    observed_stamp_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transport_reference: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[dt.datetime] = _created_at()
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    context: Mapped[JsonObject] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        _enum_check("event_type", TraceEventType),
        CheckConstraint("observed_stamp_count > 0", name="observed_count_positive"),
        CheckConstraint(
            "destination_facility_id IS NULL OR destination_facility_id <> origin_facility_id",
            name="destination_differs",
        ),
        Index("ix_trace_events_unit", "trade_unit_id", "occurred_at"),
        Index("ix_trace_events_company_occurred", "company_id", "occurred_at"),
    )


class Consignment(Base):
    """An import, free-zone, transit or duty-free consignment.

    The customs declaration reference is operator-entered: no customs system is
    integrated, so the platform records the declaration and reconciles the stamped
    quantity against it rather than claiming customs confirmation.
    """

    __tablename__ = "consignments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    consignment_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    regime: Mapped[str] = mapped_column(String(32), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    declared_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customs_declaration_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    origin_country: Mapped[str] = mapped_column(String(2), nullable=False)
    entry_facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    declared_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()
    released_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        _enum_check("regime", CustomsRegime),
        _enum_check("status", ConsignmentStatus),
        CheckConstraint("declared_quantity > 0", name="declared_quantity_positive"),
        CheckConstraint("char_length(origin_country) = 2", name="origin_country_iso2"),
        CheckConstraint(
            "(status <> 'released') OR (released_at IS NOT NULL)", name="released_requires_timestamp"
        ),
        Index("ix_consignments_company_status", "company_id", "status"),
    )


class ConsignmentStamp(Base):
    """The stamps that cover an import consignment's declared quantity.

    A stamp may cover at most one consignment, so the same stamps cannot be presented
    twice to release two shipments.
    """

    __tablename__ = "consignment_stamps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    consignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("consignments.id", ondelete="RESTRICT"), nullable=False
    )
    stamp_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamps.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    linked_at: Mapped[dt.datetime] = _created_at()
    linked_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (Index("ix_consignment_stamps_consignment", "consignment_id"),)


class Anomaly(Base):
    """A deterministic finding from movement or scan geometry.

    ``dedupe_key`` makes detection idempotent: re-running the sweep over the same
    evidence never multiplies the finding.
    """

    __tablename__ = "anomalies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT")
    )
    stamp_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamps.id", ondelete="RESTRICT")
    )
    trade_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_units.id", ondelete="RESTRICT")
    )
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence: Mapped[JsonObject] = mapped_column(JSONB, nullable=False, default=dict)
    detected_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("kind", AnomalyKind),
        _enum_check("severity", AnomalySeverity),
        Index("ix_anomalies_kind_detected", "kind", "detected_at"),
    )


class TransparencyCheckpoint(Base):
    """A published Merkle checkpoint over the audit log.

    Each checkpoint commits to every audit event up to ``covers_to_seq``; the signature
    lets a third party check a checkpoint it was handed, and an inclusion proof lets it
    check one record without database access.
    """

    __tablename__ = "transparency_checkpoints"

    id: Mapped[uuid.UUID] = _uuid_pk()
    checkpoint_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    tree_size: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    covers_to_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    root_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_root_hash: Mapped[str | None] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    published_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (CheckConstraint("tree_size > 0", name="tree_size_positive"),)


class DataExport(Base):
    """Evidence of a portability or regulator export: what was released, to whom, hashed."""

    __tablename__ = "data_exports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    export_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT")
    )
    scope: Mapped[JsonObject] = mapped_column(JSONB, nullable=False, default=dict)
    record_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("kind", ExportKind),
        CheckConstraint("record_count >= 0", name="record_count_non_negative"),
        CheckConstraint(
            "(kind <> 'portability') OR (company_id IS NOT NULL)", name="portability_requires_company"
        ),
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
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default=VerificationChannel.FIELD_DEVICE)
    latitude_e7: Mapped[int | None] = mapped_column(BigInteger)
    longitude_e7: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("outcome", VerificationOutcome),
        _enum_check("channel", VerificationChannel),
        Index("ix_verifications_serial", "serial_presented", "occurred_at"),
        Index("ix_verifications_stamp", "stamp_id", "occurred_at"),
    )


class ConsumerVerification(Base):
    """A verification performed by a member of the public, with no account.

    No consumer identity is stored. ``client_fingerprint`` is a keyed hash of the caller
    address, which supports abuse control and clone detection without retaining an
    identifier that could be reversed to a person.
    """

    __tablename__ = "consumer_verifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    stamp_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stamps.id", ondelete="RESTRICT")
    )
    serial_presented: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    client_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reported_state: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    occurred_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("outcome", VerificationOutcome),
        Index("ix_consumer_verifications_serial", "serial_presented", "occurred_at"),
        Index("ix_consumer_verifications_client", "client_fingerprint", "occurred_at"),
    )


class EnforcementCase(Base):
    """An investigation into suspected illicit trade.

    A case is opened from evidence the platform already holds; the estimated revenue at
    risk is recomputed from that evidence rather than entered by hand, so a case cannot
    assert a loss the records do not support.
    """

    __tablename__ = "enforcement_cases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT")
    )
    product_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    revenue_at_risk_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    opened_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    closure_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        _enum_check("kind", CaseKind),
        _enum_check("status", CaseStatus),
        _enum_check("severity", AnomalySeverity),
        CheckConstraint("revenue_at_risk_minor >= 0", name="revenue_at_risk_non_negative"),
        CheckConstraint(
            "(status NOT IN ('closed_substantiated', 'closed_unsubstantiated')) "
            "OR (closed_at IS NOT NULL AND char_length(closure_reason) > 0)",
            name="closure_requires_reason",
        ),
        Index("ix_enforcement_cases_status", "status", "created_at"),
        Index("ix_enforcement_cases_company", "company_id", "status"),
    )


class CaseEvidence(Base):
    """An item of evidence attached to a case. Append-only: evidence is never edited."""

    __tablename__ = "case_evidence"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enforcement_cases.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[JsonObject] = mapped_column(JSONB, nullable=False, default=dict)
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        _enum_check("kind", EvidenceKind),
        UniqueConstraint("case_id", "kind", "reference", name="uq_case_evidence_case_id_kind_reference"),
    )


class Seizure(Base):
    """Goods taken into custody under a case.

    ``estimated_duty_minor`` is the duty the seized quantity would have carried at the
    tariff effective when the seizure was recorded, so the figure is reproducible.
    """

    __tablename__ = "seizures"

    id: Mapped[uuid.UUID] = _uuid_pk()
    seizure_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enforcement_cases.id", ondelete="RESTRICT"), nullable=False
    )
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="RESTRICT")
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    product_category: Mapped[str] = mapped_column(String(64), nullable=False)
    seized_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_duty_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    tariff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tariffs.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    seized_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        _enum_check("status", SeizureStatus),
        CheckConstraint("seized_quantity > 0", name="seized_quantity_positive"),
        CheckConstraint("estimated_duty_minor >= 0", name="estimated_duty_non_negative"),
        Index("ix_seizures_case", "case_id", "created_at"),
    )


class CustodyTransfer(Base):
    """One link in a seizure's chain of custody.

    Links are numbered per seizure and hash-chained, so a removed or reordered handover
    is detectable: the chain is the admissibility evidence, not a convenience log.
    """

    __tablename__ = "custody_transfers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    seizure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seizures.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_custodian: Mapped[str] = mapped_column(String(255), nullable=False)
    to_custodian: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("from_custodian <> to_custodian", name="custodians_differ"),
        UniqueConstraint("seizure_id", "sequence", name="uq_custody_transfers_seizure_id_sequence"),
    )


class OfflineBundle(Base):
    """A signed, offline-verifiable snapshot of the revoked-serial filter.

    A device with no connectivity checks a serial against the filter and the signature;
    ``valid_until`` bounds how stale that answer may be, so an offline device cannot keep
    trusting an indefinitely old revocation list.
    """

    __tablename__ = "offline_bundles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    bundle_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    revoked_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filter_bits: Mapped[int] = mapped_column(Integer, nullable=False)
    filter_hash_count: Mapped[int] = mapped_column(Integer, nullable=False)
    filter_base64: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("revoked_count >= 0", name="revoked_count_non_negative"),
        CheckConstraint("filter_bits > 0", name="filter_bits_positive"),
        CheckConstraint("filter_hash_count > 0", name="filter_hash_count_positive"),
        CheckConstraint("valid_until > generated_at", name="validity_range"),
    )


class OfflineScanBatch(Base):
    """A batch of verifications a device captured while offline.

    ``(device_id, batch_sequence)`` is unique, so a replayed batch cannot be counted
    twice; a resubmission of the same sequence with different contents is a conflict
    rather than an overwrite.
    """

    __tablename__ = "offline_scan_batches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_to: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("batch_sequence >= 1", name="batch_sequence_positive"),
        CheckConstraint("scan_count > 0", name="scan_count_positive"),
        CheckConstraint(
            "accepted_count >= 0 AND duplicate_count >= 0 "
            "AND accepted_count + duplicate_count = scan_count",
            name="counts_reconcile",
        ),
        CheckConstraint("captured_to >= captured_from", name="capture_window"),
        UniqueConstraint(
            "device_id", "batch_sequence", name="uq_offline_scan_batches_device_id_batch_sequence"
        ),
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

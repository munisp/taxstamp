"""Licensing, product master data, stamp dispositions and receipt resolutions.

Licences make procurement conditional on a legal entitlement, products carry the master
data a stamp is issued against, dispositions account for stamps that never reached
goods, and resolutions record how quarantined funds left the unapplied account.

Revision ID: 9c4b7e15a2d8
Revises: 5f1c9a2d7b04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c4b7e15a2d8"
down_revision: str | None = "5f1c9a2d7b04"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "licences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("licence_number", sa.String(length=64), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("licence_type", sa.String(length=32), nullable=False),
        sa.Column("product_categories", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("statutory_reference", sa.String(length=255), nullable=False),
        sa.Column("status_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_licences_company_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_licences"),
        sa.UniqueConstraint("licence_number", name="uq_licences_licence_number"),
        sa.CheckConstraint(
            "licence_type IN ('manufacturer', 'importer', 'distributor')",
            name="ck_licences_licence_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')", name="ck_licences_status_valid"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_licences_validity_range"
        ),
        sa.CheckConstraint(
            "jsonb_array_length(product_categories) > 0", name="ck_licences_categories_present"
        ),
    )
    op.create_index("ix_licences_created_at", "licences", ["created_at"])
    op.create_index("ix_licences_company_status", "licences", ["company_id", "status"])

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("brand", sa.String(length=128), nullable=False),
        sa.Column("product_category", sa.String(length=64), nullable=False),
        sa.Column("pack_size", sa.Integer(), nullable=False),
        sa.Column("unit_of_measure", sa.String(length=16), nullable=False),
        sa.Column("intended_market", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_products_company_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("company_id", "sku", name="uq_products_company_id_sku"),
        sa.CheckConstraint("status IN ('active', 'withdrawn')", name="ck_products_status_valid"),
        sa.CheckConstraint("pack_size > 0", name="ck_products_pack_size_positive"),
    )
    op.create_index("ix_products_created_at", "products", ["created_at"])
    op.create_index("ix_products_company_category", "products", ["company_id", "product_category"])

    op.add_column(
        "orders", sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "orders", sa.Column("licence_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_orders_product_id", "orders", "products", ["product_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_foreign_key(
        "fk_orders_licence_id", "orders", "licences", ["licence_id"], ["id"], ondelete="RESTRICT"
    )

    op.create_table(
        "stamp_dispositions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("stamp_count", sa.Integer(), nullable=False),
        sa.Column("serials", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("evidence_reference", sa.String(length=128), nullable=False),
        sa.Column("declared_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["stamp_batches.id"],
            name="fk_stamp_dispositions_batch_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["declared_by"],
            ["principals.id"],
            name="fk_stamp_dispositions_declared_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stamp_dispositions"),
        sa.CheckConstraint(
            "kind IN ('spoiled', 'damaged', 'destroyed', 'returned')",
            name="ck_stamp_dispositions_kind_valid",
        ),
        sa.CheckConstraint("stamp_count > 0", name="ck_stamp_dispositions_stamp_count_positive"),
        sa.CheckConstraint(
            "stamp_count = jsonb_array_length(serials)",
            name="ck_stamp_dispositions_stamp_count_matches_serials",
        ),
    )
    op.create_index("ix_stamp_dispositions_created_at", "stamp_dispositions", ["created_at"])
    op.create_index(
        "ix_stamp_dispositions_batch", "stamp_dispositions", ["batch_id", "created_at"]
    )

    op.create_table(
        "receipt_resolutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("payment_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "beneficiary_reference", sa.String(length=128), nullable=False, server_default=""
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["payment_receipt_id"],
            ["payment_receipts.id"],
            name="fk_receipt_resolutions_payment_receipt_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_receipt_resolutions_order_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["journal_id"],
            ["journals.id"],
            name="fk_receipt_resolutions_journal_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_principal_id"],
            ["principals.id"],
            name="fk_receipt_resolutions_actor_principal_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_receipt_resolutions"),
        sa.UniqueConstraint(
            "payment_receipt_id", name="uq_receipt_resolutions_payment_receipt_id"
        ),
        sa.CheckConstraint(
            "kind IN ('applied', 'refunded')", name="ck_receipt_resolutions_kind_valid"
        ),
        sa.CheckConstraint(
            "(kind <> 'applied') OR (order_id IS NOT NULL)",
            name="ck_receipt_resolutions_applied_requires_order",
        ),
        sa.CheckConstraint(
            "(kind <> 'refunded') OR (char_length(beneficiary_reference) > 0)",
            name="ck_receipt_resolutions_refund_requires_beneficiary",
        ),
    )
    op.create_index("ix_receipt_resolutions_created_at", "receipt_resolutions", ["created_at"])


def downgrade() -> None:
    # Licences, dispositions and resolutions are regulatory and financial evidence, so the
    # downgrade refuses rather than deleting them.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM licences) "
        "OR EXISTS (SELECT 1 FROM stamp_dispositions) "
        "OR EXISTS (SELECT 1 FROM receipt_resolutions) THEN "
        "RAISE EXCEPTION 'licensing or accountability records exist; archive them before "
        "downgrading'; "
        "END IF; END $$;"
    )
    op.drop_index("ix_receipt_resolutions_created_at", table_name="receipt_resolutions")
    op.drop_table("receipt_resolutions")
    op.drop_index("ix_stamp_dispositions_batch", table_name="stamp_dispositions")
    op.drop_index("ix_stamp_dispositions_created_at", table_name="stamp_dispositions")
    op.drop_table("stamp_dispositions")
    op.drop_constraint("fk_orders_licence_id", "orders", type_="foreignkey")
    op.drop_constraint("fk_orders_product_id", "orders", type_="foreignkey")
    op.drop_column("orders", "licence_id")
    op.drop_column("orders", "product_id")
    op.drop_index("ix_products_company_category", table_name="products")
    op.drop_index("ix_products_created_at", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_licences_company_status", table_name="licences")
    op.drop_index("ix_licences_created_at", table_name="licences")
    op.drop_table("licences")

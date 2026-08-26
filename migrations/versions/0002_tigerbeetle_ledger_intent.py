"""add durable tigerbeetle ledger intents

Revision ID: 4bf6b1f5f0ab
Revises: 273aefd6e9e8
Create Date: 2026-08-25 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4bf6b1f5f0ab"
down_revision: str | None = "273aefd6e9e8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

IMMUTABLE_COLUMNS = (
    "payment_intent_id",
    "tigerbeetle_transfer_id",
    "debit_account_id",
    "credit_account_id",
    "ledger_code",
    "transfer_code",
    "transfer_flags",
    "amount_minor",
    "currency",
    "payload_hash",
)


def upgrade() -> None:
    op.create_table(
        "tigerbeetle_ledger_intents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("payment_intent_id", sa.UUID(), nullable=False),
        sa.Column("tigerbeetle_transfer_id", sa.String(length=32), nullable=False),
        sa.Column("debit_account_id", sa.String(length=32), nullable=False),
        sa.Column("credit_account_id", sa.String(length=32), nullable=False),
        sa.Column("ledger_code", sa.BigInteger(), nullable=False),
        sa.Column("transfer_code", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("transfer_flags", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default=sa.text("'ready'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("external_timestamp", sa.BigInteger(), nullable=True),
        sa.Column("external_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("amount_minor > 0", name=op.f("ck_tigerbeetle_ledger_intents_amount_positive")),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_tigerbeetle_ledger_intents_attempt_count_non_negative")
        ),
        sa.CheckConstraint(
            "debit_account_id <> credit_account_id",
            name=op.f("ck_tigerbeetle_ledger_intents_accounts_must_differ"),
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name=op.f("ck_tigerbeetle_ledger_intents_currency_format"),
        ),
        sa.CheckConstraint(
            "credit_account_id ~ '^[0-9a-f]{32}$'",
            name=op.f("ck_tigerbeetle_ledger_intents_credit_account_lower_hex"),
        ),
        sa.CheckConstraint(
            "debit_account_id ~ '^[0-9a-f]{32}$'",
            name=op.f("ck_tigerbeetle_ledger_intents_debit_account_lower_hex"),
        ),
        sa.CheckConstraint(
            "ledger_code > 0 AND ledger_code <= 4294967295",
            name=op.f("ck_tigerbeetle_ledger_intents_ledger_code_range"),
        ),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_tigerbeetle_ledger_intents_payload_hash_lower_hex"),
        ),
        sa.CheckConstraint(
            "state IN ('ready', 'submission_uncertain', 'external_confirmed', "
            "'posted', 'rejected', 'quarantined')",
            name=op.f("ck_tigerbeetle_ledger_intents_state_valid"),
        ),
        sa.CheckConstraint(
            "tigerbeetle_transfer_id ~ '^[0-9a-f]{32}$'",
            name=op.f("ck_tigerbeetle_ledger_intents_transfer_id_lower_hex"),
        ),
        sa.CheckConstraint(
            "transfer_code >= 0 AND transfer_code <= 65535",
            name=op.f("ck_tigerbeetle_ledger_intents_transfer_code_range"),
        ),
        sa.CheckConstraint(
            "transfer_flags >= 0",
            name=op.f("ck_tigerbeetle_ledger_intents_transfer_flags_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_intent_id"],
            ["payment_intents.id"],
            name=op.f("fk_tigerbeetle_ledger_intents_payment_intent_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tigerbeetle_ledger_intents")),
        sa.UniqueConstraint(
            "payment_intent_id", name=op.f("uq_tigerbeetle_ledger_intents_payment_intent_id")
        ),
        sa.UniqueConstraint(
            "tigerbeetle_transfer_id",
            name=op.f("uq_tigerbeetle_ledger_intents_tigerbeetle_transfer_id"),
        ),
    )
    op.create_index(
        "ix_tigerbeetle_ledger_intents_state_created",
        "tigerbeetle_ledger_intents",
        ["state", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tigerbeetle_ledger_intents_created_at"),
        "tigerbeetle_ledger_intents",
        ["created_at"],
        unique=False,
    )

    fields = " OR ".join(f"OLD.{column} IS DISTINCT FROM NEW.{column}" for column in IMMUTABLE_COLUMNS)
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION taxstamp_tigerbeetle_intent_immutable_fields() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF {fields} THEN
                RAISE EXCEPTION 'tigerbeetle ledger intent financial fields are immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER tigerbeetle_ledger_intents_immutable_fields "
        "BEFORE UPDATE ON tigerbeetle_ledger_intents "
        "FOR EACH ROW EXECUTE FUNCTION taxstamp_tigerbeetle_intent_immutable_fields()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS tigerbeetle_ledger_intents_immutable_fields " "ON tigerbeetle_ledger_intents"
    )
    op.execute("DROP FUNCTION IF EXISTS taxstamp_tigerbeetle_intent_immutable_fields()")
    op.drop_index(op.f("ix_tigerbeetle_ledger_intents_created_at"), table_name="tigerbeetle_ledger_intents")
    op.drop_index("ix_tigerbeetle_ledger_intents_state_created", table_name="tigerbeetle_ledger_intents")
    op.drop_table("tigerbeetle_ledger_intents")

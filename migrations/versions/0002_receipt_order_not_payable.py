"""Allow the order_not_payable receipt status.

A remittance that arrives for an order which can no longer become paid (typically a
cancelled order) is recorded and quarantined instead of being forced onto that order.

Revision ID: 5f1c9a2d7b04
Revises: 273aefd6e9e8
"""

from __future__ import annotations

from alembic import op

revision: str = "5f1c9a2d7b04"
down_revision: str | None = "273aefd6e9e8"
branch_labels: None = None
depends_on: None = None

_CONSTRAINT = "status_valid"
_OLD = "status IN ('matched', 'amount_mismatch', 'unknown_reference', 'duplicate')"
_NEW = (
    "status IN ('matched', 'amount_mismatch', 'unknown_reference', "
    "'order_not_payable', 'duplicate')"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "payment_receipts", type_="check")
    op.create_check_constraint(_CONSTRAINT, "payment_receipts", _NEW)


def downgrade() -> None:
    # Receipts are financial evidence and are never deleted to satisfy a downgrade.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM payment_receipts WHERE status = 'order_not_payable') THEN "
        "RAISE EXCEPTION 'order_not_payable receipts exist; resolve them before downgrading'; "
        "END IF; END $$;"
    )
    op.drop_constraint(_CONSTRAINT, "payment_receipts", type_="check")
    op.create_check_constraint(_CONSTRAINT, "payment_receipts", _OLD)

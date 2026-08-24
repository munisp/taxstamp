"""Link principals to federated identity provider subjects.

A federated session is only accepted for a principal an administrator has explicitly
linked, so the column is nullable and unique: no automatic provisioning, and one provider
subject can never map to two principals. Devices are excluded, because a device fleet has
no interactive login and must keep working when the provider is unreachable.

Revision ID: 3ab5c1d94e77
Revises: 8d077aa09351
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '3ab5c1d94e77'
down_revision: str | None = '8d077aa09351'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "principals",
        sa.Column("oidc_subject", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        op.f("uq_principals_oidc_subject"),
        "principals",
        ["oidc_subject"],
    )
    op.create_check_constraint(
        op.f("ck_principals_devices_are_not_federated"),
        "principals",
        "oidc_subject IS NULL OR role <> 'device'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_principals_devices_are_not_federated"), "principals", type_="check")
    op.drop_constraint(op.f("uq_principals_oidc_subject"), "principals", type_="unique")
    op.drop_column("principals", "oidc_subject")

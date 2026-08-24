"""Consumer verification, enforcement cases, seizures, offline bundles and scan batches.

Consumer checks, case evidence, custody handovers, published bundles and synchronised
batches are all evidence of what the platform was told and what it decided, so the
database refuses to update or delete them. Existing field verifications are labelled with
the channel they were taken on.

Revision ID: 8d077aa09351
Revises: 7cac4f6a5000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '8d077aa09351'
down_revision: str | None = '7cac4f6a5000'
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Evidence tables: recorded once, never rewritten.
APPEND_ONLY_TABLES = (
    "consumer_verifications",
    "case_evidence",
    "custody_transfers",
    "offline_bundles",
    "offline_scan_batches",
)


def upgrade() -> None:
    op.create_table('enforcement_cases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('case_ref', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('product_category', sa.String(length=64), nullable=False),
    sa.Column('summary', sa.String(length=500), nullable=False),
    sa.Column('revenue_at_risk_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('opened_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closure_reason', sa.String(length=500), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status NOT IN ('closed_substantiated', 'closed_unsubstantiated')) OR (closed_at IS NOT NULL AND char_length(closure_reason) > 0)", name=op.f('ck_enforcement_cases_closure_requires_reason')),
    sa.CheckConstraint("kind IN ('counterfeit', 'diversion', 'unstamped_goods', 'quantity_discrepancy', 'licensing_breach')", name=op.f('ck_enforcement_cases_kind_valid')),
    sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name=op.f('ck_enforcement_cases_severity_valid')),
    sa.CheckConstraint("status IN ('open', 'under_investigation', 'referred_for_prosecution', 'closed_substantiated', 'closed_unsubstantiated')", name=op.f('ck_enforcement_cases_status_valid')),
    sa.CheckConstraint('revenue_at_risk_minor >= 0', name=op.f('ck_enforcement_cases_revenue_at_risk_non_negative')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_enforcement_cases_company_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['opened_by'], ['principals.id'], name=op.f('fk_enforcement_cases_opened_by'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_enforcement_cases')),
    sa.UniqueConstraint('case_ref', name=op.f('uq_enforcement_cases_case_ref'))
    )
    op.create_index('ix_enforcement_cases_company', 'enforcement_cases', ['company_id', 'status'], unique=False)
    op.create_index(op.f('ix_enforcement_cases_created_at'), 'enforcement_cases', ['created_at'], unique=False)
    op.create_index('ix_enforcement_cases_status', 'enforcement_cases', ['status', 'created_at'], unique=False)
    op.create_table('offline_bundles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bundle_ref', sa.String(length=64), nullable=False),
    sa.Column('sequence', sa.BigInteger(), nullable=False),
    sa.Column('revoked_count', sa.BigInteger(), nullable=False),
    sa.Column('filter_bits', sa.Integer(), nullable=False),
    sa.Column('filter_hash_count', sa.Integer(), nullable=False),
    sa.Column('filter_base64', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('signature', sa.String(length=64), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('filter_bits > 0', name=op.f('ck_offline_bundles_filter_bits_positive')),
    sa.CheckConstraint('filter_hash_count > 0', name=op.f('ck_offline_bundles_filter_hash_count_positive')),
    sa.CheckConstraint('revoked_count >= 0', name=op.f('ck_offline_bundles_revoked_count_non_negative')),
    sa.CheckConstraint('sequence >= 1', name=op.f('ck_offline_bundles_sequence_positive')),
    sa.CheckConstraint('valid_until > generated_at', name=op.f('ck_offline_bundles_validity_range')),
    sa.ForeignKeyConstraint(['created_by'], ['principals.id'], name=op.f('fk_offline_bundles_created_by'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_offline_bundles')),
    sa.UniqueConstraint('bundle_ref', name=op.f('uq_offline_bundles_bundle_ref')),
    sa.UniqueConstraint('sequence', name=op.f('uq_offline_bundles_sequence'))
    )
    op.create_index(op.f('ix_offline_bundles_created_at'), 'offline_bundles', ['created_at'], unique=False)
    op.create_table('offline_scan_batches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=False),
    sa.Column('batch_sequence', sa.BigInteger(), nullable=False),
    sa.Column('principal_id', sa.UUID(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('scan_count', sa.Integer(), nullable=False),
    sa.Column('accepted_count', sa.Integer(), nullable=False),
    sa.Column('duplicate_count', sa.Integer(), nullable=False),
    sa.Column('captured_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('captured_to', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('accepted_count >= 0 AND duplicate_count >= 0 AND accepted_count + duplicate_count = scan_count', name=op.f('ck_offline_scan_batches_counts_reconcile')),
    sa.CheckConstraint('batch_sequence >= 1', name=op.f('ck_offline_scan_batches_batch_sequence_positive')),
    sa.CheckConstraint('captured_to >= captured_from', name=op.f('ck_offline_scan_batches_capture_window')),
    sa.CheckConstraint('scan_count > 0', name=op.f('ck_offline_scan_batches_scan_count_positive')),
    sa.ForeignKeyConstraint(['principal_id'], ['principals.id'], name=op.f('fk_offline_scan_batches_principal_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_offline_scan_batches')),
    sa.UniqueConstraint('device_id', 'batch_sequence', name='uq_offline_scan_batches_device_id_batch_sequence')
    )
    op.create_index(op.f('ix_offline_scan_batches_created_at'), 'offline_scan_batches', ['created_at'], unique=False)
    op.create_table('case_evidence',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('reference', sa.String(length=128), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('added_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('anomaly', 'verification', 'consignment', 'stamp', 'trace_event', 'statement')", name=op.f('ck_case_evidence_kind_valid')),
    sa.ForeignKeyConstraint(['added_by'], ['principals.id'], name=op.f('fk_case_evidence_added_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['case_id'], ['enforcement_cases.id'], name=op.f('fk_case_evidence_case_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_case_evidence')),
    sa.UniqueConstraint('case_id', 'kind', 'reference', name='uq_case_evidence_case_id_kind_reference')
    )
    op.create_index(op.f('ix_case_evidence_created_at'), 'case_evidence', ['created_at'], unique=False)
    op.create_table('seizures',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('seizure_ref', sa.String(length=64), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('facility_id', sa.UUID(), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('product_category', sa.String(length=64), nullable=False),
    sa.Column('seized_quantity', sa.BigInteger(), nullable=False),
    sa.Column('estimated_duty_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('tariff_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('status_reason', sa.String(length=500), nullable=False),
    sa.Column('seized_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status IN ('held', 'released', 'destroyed', 'forfeited')", name=op.f('ck_seizures_status_valid')),
    sa.CheckConstraint('estimated_duty_minor >= 0', name=op.f('ck_seizures_estimated_duty_non_negative')),
    sa.CheckConstraint('seized_quantity > 0', name=op.f('ck_seizures_seized_quantity_positive')),
    sa.ForeignKeyConstraint(['case_id'], ['enforcement_cases.id'], name=op.f('fk_seizures_case_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['facility_id'], ['facilities.id'], name=op.f('fk_seizures_facility_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recorded_by'], ['principals.id'], name=op.f('fk_seizures_recorded_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tariff_id'], ['tariffs.id'], name=op.f('fk_seizures_tariff_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_seizures')),
    sa.UniqueConstraint('seizure_ref', name=op.f('uq_seizures_seizure_ref'))
    )
    op.create_index('ix_seizures_case', 'seizures', ['case_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_seizures_created_at'), 'seizures', ['created_at'], unique=False)
    op.create_table('custody_transfers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('seizure_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('from_custodian', sa.String(length=255), nullable=False),
    sa.Column('to_custodian', sa.String(length=255), nullable=False),
    sa.Column('location', sa.String(length=255), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('evidence_reference', sa.String(length=128), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_by', sa.UUID(), nullable=False),
    sa.Column('prev_hash', sa.String(length=64), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('from_custodian <> to_custodian', name=op.f('ck_custody_transfers_custodians_differ')),
    sa.CheckConstraint('sequence >= 1', name=op.f('ck_custody_transfers_sequence_positive')),
    sa.ForeignKeyConstraint(['recorded_by'], ['principals.id'], name=op.f('fk_custody_transfers_recorded_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['seizure_id'], ['seizures.id'], name=op.f('fk_custody_transfers_seizure_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_custody_transfers')),
    sa.UniqueConstraint('hash', name=op.f('uq_custody_transfers_hash')),
    sa.UniqueConstraint('seizure_id', 'sequence', name='uq_custody_transfers_seizure_id_sequence')
    )
    op.create_index(op.f('ix_custody_transfers_created_at'), 'custody_transfers', ['created_at'], unique=False)
    op.create_table('consumer_verifications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('stamp_id', sa.UUID(), nullable=True),
    sa.Column('serial_presented', sa.String(length=64), nullable=False),
    sa.Column('outcome', sa.String(length=32), nullable=False),
    sa.Column('client_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('reported_state', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("outcome IN ('valid', 'unknown_serial', 'secure_code_mismatch', 'not_active', 'void', 'expired', 'velocity_suspect')", name=op.f('ck_consumer_verifications_outcome_valid')),
    sa.ForeignKeyConstraint(['stamp_id'], ['stamps.id'], name=op.f('fk_consumer_verifications_stamp_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consumer_verifications'))
    )
    op.create_index('ix_consumer_verifications_client', 'consumer_verifications', ['client_fingerprint', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_consumer_verifications_occurred_at'), 'consumer_verifications', ['occurred_at'], unique=False)
    op.create_index('ix_consumer_verifications_serial', 'consumer_verifications', ['serial_presented', 'occurred_at'], unique=False)
    # Every verification recorded before this migration was taken by a field device.
    op.add_column(
        'verifications',
        sa.Column('channel', sa.String(length=16), nullable=False, server_default='field_device'),
    )
    op.alter_column('verifications', 'channel', server_default=None)
    op.create_check_constraint(
        op.f('ck_verifications_channel_valid'),
        'verifications',
        "channel IN ('field_device', 'consumer', 'offline_device')",
    )

    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION taxstamp_reject_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION taxstamp_reject_mutation()"
        )



def downgrade() -> None:
    # Enforcement records and consumer checks are evidence that may be relied on in
    # proceedings, so the downgrade refuses rather than deleting them.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM enforcement_cases) "
        "OR EXISTS (SELECT 1 FROM seizures) "
        "OR EXISTS (SELECT 1 FROM custody_transfers) "
        "OR EXISTS (SELECT 1 FROM consumer_verifications) THEN "
        "RAISE EXCEPTION 'enforcement or consumer verification records exist; archive "
        "them before downgrading'; "
        "END IF; END $$;"
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table}")
    op.drop_constraint(op.f('ck_verifications_channel_valid'), 'verifications', type_='check')
    op.drop_column('verifications', 'channel')
    op.drop_index('ix_consumer_verifications_serial', table_name='consumer_verifications')
    op.drop_index(op.f('ix_consumer_verifications_occurred_at'), table_name='consumer_verifications')
    op.drop_index('ix_consumer_verifications_client', table_name='consumer_verifications')
    op.drop_table('consumer_verifications')
    op.drop_index(op.f('ix_custody_transfers_created_at'), table_name='custody_transfers')
    op.drop_table('custody_transfers')
    op.drop_index(op.f('ix_seizures_created_at'), table_name='seizures')
    op.drop_index('ix_seizures_case', table_name='seizures')
    op.drop_table('seizures')
    op.drop_index(op.f('ix_case_evidence_created_at'), table_name='case_evidence')
    op.drop_table('case_evidence')
    op.drop_index(op.f('ix_offline_scan_batches_created_at'), table_name='offline_scan_batches')
    op.drop_table('offline_scan_batches')
    op.drop_index(op.f('ix_offline_bundles_created_at'), table_name='offline_bundles')
    op.drop_table('offline_bundles')
    op.drop_index('ix_enforcement_cases_status', table_name='enforcement_cases')
    op.drop_index(op.f('ix_enforcement_cases_created_at'), table_name='enforcement_cases')
    op.drop_index('ix_enforcement_cases_company', table_name='enforcement_cases')
    op.drop_table('enforcement_cases')

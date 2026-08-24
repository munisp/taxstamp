"""Supply-chain traceability, customs consignments, transparency and exports.

Facilities and trade units carry the physical geometry of the chain, trace events are
the append-only movement history, consignments record what customs was told, anomalies
are the deterministic findings, checkpoints publish the audit log's root, and data
exports record what left the platform and to whom.

Revision ID: 7cac4f6a5000
Revises: 9c4b7e15a2d8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7cac4f6a5000"
down_revision: str | None = "9c4b7e15a2d8"
branch_labels: None = None
depends_on: None = None

#: Movement history and published proofs are evidence, so the database refuses edits.
APPEND_ONLY_TABLES = ("trace_events", "transparency_checkpoints", "data_exports", "anomalies")


def upgrade() -> None:
    op.create_table('facilities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('facility_code', sa.String(length=64), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('state', sa.String(length=64), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('latitude_e7', sa.BigInteger(), nullable=False),
    sa.Column('longitude_e7', sa.BigInteger(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('factory', 'warehouse', 'distribution_centre', 'retail', 'port', 'free_zone', 'duty_free_outlet', 'destruction_site')", name=op.f('ck_facilities_kind_valid')),
    sa.CheckConstraint('char_length(country) = 2', name=op.f('ck_facilities_country_iso2')),
    sa.CheckConstraint('latitude_e7 BETWEEN -900000000 AND 900000000', name=op.f('ck_facilities_latitude_range')),
    sa.CheckConstraint('longitude_e7 BETWEEN -1800000000 AND 1800000000', name=op.f('ck_facilities_longitude_range')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_facilities_company_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_facilities')),
    sa.UniqueConstraint('facility_code', name=op.f('uq_facilities_facility_code'))
    )
    op.create_index('ix_facilities_company', 'facilities', ['company_id', 'kind'], unique=False)
    op.create_index(op.f('ix_facilities_created_at'), 'facilities', ['created_at'], unique=False)
    op.create_table('data_exports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('export_ref', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('record_count', sa.BigInteger(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('signature', sa.String(length=64), nullable=False),
    sa.Column('requested_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(kind <> 'portability') OR (company_id IS NOT NULL)", name=op.f('ck_data_exports_portability_requires_company')),
    sa.CheckConstraint("kind IN ('portability', 'regulator')", name=op.f('ck_data_exports_kind_valid')),
    sa.CheckConstraint('record_count >= 0', name=op.f('ck_data_exports_record_count_non_negative')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_data_exports_company_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['requested_by'], ['principals.id'], name=op.f('fk_data_exports_requested_by'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_exports')),
    sa.UniqueConstraint('export_ref', name=op.f('uq_data_exports_export_ref'))
    )
    op.create_index(op.f('ix_data_exports_created_at'), 'data_exports', ['created_at'], unique=False)
    op.create_table('trade_units',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('unit_code', sa.String(length=64), nullable=False),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('parent_unit_id', sa.UUID(), nullable=True),
    sa.Column('product_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('stamp_count', sa.BigInteger(), nullable=False),
    sa.Column('facility_id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("level IN ('case', 'pallet', 'container')", name=op.f('ck_trade_units_level_valid')),
    sa.CheckConstraint("status IN ('closed', 'in_transit', 'delivered', 'exported', 'destroyed', 'disaggregated')", name=op.f('ck_trade_units_status_valid')),
    sa.CheckConstraint('parent_unit_id IS NULL OR parent_unit_id <> id', name=op.f('ck_trade_units_parent_not_self')),
    sa.CheckConstraint('stamp_count > 0', name=op.f('ck_trade_units_stamp_count_positive')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_trade_units_company_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['principals.id'], name=op.f('fk_trade_units_created_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['facility_id'], ['facilities.id'], name=op.f('fk_trade_units_facility_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_unit_id'], ['trade_units.id'], name=op.f('fk_trade_units_parent_unit_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_trade_units_product_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trade_units')),
    sa.UniqueConstraint('unit_code', name=op.f('uq_trade_units_unit_code'))
    )
    op.create_index('ix_trade_units_company_status', 'trade_units', ['company_id', 'status'], unique=False)
    op.create_index(op.f('ix_trade_units_created_at'), 'trade_units', ['created_at'], unique=False)
    op.create_index('ix_trade_units_parent', 'trade_units', ['parent_unit_id'], unique=False)
    op.create_table('transparency_checkpoints',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('checkpoint_ref', sa.String(length=64), nullable=False),
    sa.Column('tree_size', sa.BigInteger(), nullable=False),
    sa.Column('covers_to_seq', sa.BigInteger(), nullable=False),
    sa.Column('root_hash', sa.String(length=64), nullable=False),
    sa.Column('prev_root_hash', sa.String(length=64), nullable=True),
    sa.Column('signature', sa.String(length=64), nullable=False),
    sa.Column('published_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('tree_size > 0', name=op.f('ck_transparency_checkpoints_tree_size_positive')),
    sa.ForeignKeyConstraint(['published_by'], ['principals.id'], name=op.f('fk_transparency_checkpoints_published_by'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transparency_checkpoints')),
    sa.UniqueConstraint('checkpoint_ref', name=op.f('uq_transparency_checkpoints_checkpoint_ref')),
    sa.UniqueConstraint('tree_size', name=op.f('uq_transparency_checkpoints_tree_size'))
    )
    op.create_index(op.f('ix_transparency_checkpoints_created_at'), 'transparency_checkpoints', ['created_at'], unique=False)
    op.create_table('consignments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('consignment_ref', sa.String(length=64), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('regime', sa.String(length=32), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=False),
    sa.Column('declared_quantity', sa.BigInteger(), nullable=False),
    sa.Column('customs_declaration_reference', sa.String(length=128), nullable=False),
    sa.Column('origin_country', sa.String(length=2), nullable=False),
    sa.Column('entry_facility_id', sa.UUID(), nullable=False),
    sa.Column('order_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('status_reason', sa.String(length=500), nullable=False),
    sa.Column('declared_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status <> 'released') OR (released_at IS NOT NULL)", name=op.f('ck_consignments_released_requires_timestamp')),
    sa.CheckConstraint("regime IN ('import_duty_paid', 'free_zone', 'transit', 'duty_free')", name=op.f('ck_consignments_regime_valid')),
    sa.CheckConstraint("status IN ('declared', 'stamps_linked', 'released', 'rejected')", name=op.f('ck_consignments_status_valid')),
    sa.CheckConstraint('char_length(origin_country) = 2', name=op.f('ck_consignments_origin_country_iso2')),
    sa.CheckConstraint('declared_quantity > 0', name=op.f('ck_consignments_declared_quantity_positive')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_consignments_company_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['declared_by'], ['principals.id'], name=op.f('fk_consignments_declared_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['entry_facility_id'], ['facilities.id'], name=op.f('fk_consignments_entry_facility_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_consignments_order_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_consignments_product_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consignments')),
    sa.UniqueConstraint('consignment_ref', name=op.f('uq_consignments_consignment_ref')),
    sa.UniqueConstraint('order_id', name=op.f('uq_consignments_order_id'))
    )
    op.create_index('ix_consignments_company_status', 'consignments', ['company_id', 'status'], unique=False)
    op.create_index(op.f('ix_consignments_created_at'), 'consignments', ['created_at'], unique=False)
    op.create_table('trace_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('event_ref', sa.String(length=64), nullable=False),
    sa.Column('event_type', sa.String(length=16), nullable=False),
    sa.Column('trade_unit_id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('origin_facility_id', sa.UUID(), nullable=False),
    sa.Column('destination_facility_id', sa.UUID(), nullable=True),
    sa.Column('consignment_id', sa.UUID(), nullable=True),
    sa.Column('observed_stamp_count', sa.BigInteger(), nullable=False),
    sa.Column('transport_reference', sa.String(length=128), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('recorded_by', sa.UUID(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("event_type IN ('dispatch', 'arrival', 'transload', 'export', 'destruction')", name=op.f('ck_trace_events_event_type_valid')),
    sa.CheckConstraint('destination_facility_id IS NULL OR destination_facility_id <> origin_facility_id', name=op.f('ck_trace_events_destination_differs')),
    sa.CheckConstraint('observed_stamp_count > 0', name=op.f('ck_trace_events_observed_count_positive')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_trace_events_company_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['consignment_id'], ['consignments.id'], name=op.f('fk_trace_events_consignment_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['destination_facility_id'], ['facilities.id'], name=op.f('fk_trace_events_destination_facility_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['origin_facility_id'], ['facilities.id'], name=op.f('fk_trace_events_origin_facility_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recorded_by'], ['principals.id'], name=op.f('fk_trace_events_recorded_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['trade_unit_id'], ['trade_units.id'], name=op.f('fk_trace_events_trade_unit_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trace_events')),
    sa.UniqueConstraint('event_ref', name=op.f('uq_trace_events_event_ref'))
    )
    op.create_index('ix_trace_events_company_occurred', 'trace_events', ['company_id', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_trace_events_recorded_at'), 'trace_events', ['recorded_at'], unique=False)
    op.create_index('ix_trace_events_unit', 'trace_events', ['trade_unit_id', 'occurred_at'], unique=False)
    op.create_table('anomalies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('dedupe_key', sa.String(length=200), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('stamp_id', sa.UUID(), nullable=True),
    sa.Column('trade_unit_id', sa.UUID(), nullable=True),
    sa.Column('rule_version', sa.String(length=32), nullable=False),
    sa.Column('explanation', sa.String(length=500), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('impossible_travel', 'quantity_not_conserved', 'duplicate_scan_divergence', 'market_diversion')", name=op.f('ck_anomalies_kind_valid')),
    sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name=op.f('ck_anomalies_severity_valid')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_anomalies_company_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stamp_id'], ['stamps.id'], name=op.f('fk_anomalies_stamp_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['trade_unit_id'], ['trade_units.id'], name=op.f('fk_anomalies_trade_unit_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_anomalies')),
    sa.UniqueConstraint('dedupe_key', name=op.f('uq_anomalies_dedupe_key'))
    )
    op.create_index(op.f('ix_anomalies_detected_at'), 'anomalies', ['detected_at'], unique=False)
    op.create_index('ix_anomalies_kind_detected', 'anomalies', ['kind', 'detected_at'], unique=False)
    op.create_table('consignment_stamps',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('consignment_id', sa.UUID(), nullable=False),
    sa.Column('stamp_id', sa.UUID(), nullable=False),
    sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('linked_by', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['consignment_id'], ['consignments.id'], name=op.f('fk_consignment_stamps_consignment_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['linked_by'], ['principals.id'], name=op.f('fk_consignment_stamps_linked_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stamp_id'], ['stamps.id'], name=op.f('fk_consignment_stamps_stamp_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consignment_stamps')),
    sa.UniqueConstraint('stamp_id', name=op.f('uq_consignment_stamps_stamp_id'))
    )
    op.create_index('ix_consignment_stamps_consignment', 'consignment_stamps', ['consignment_id'], unique=False)
    op.create_index(op.f('ix_consignment_stamps_linked_at'), 'consignment_stamps', ['linked_at'], unique=False)
    op.create_table('unit_memberships',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('trade_unit_id', sa.UUID(), nullable=False),
    sa.Column('stamp_id', sa.UUID(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['stamp_id'], ['stamps.id'], name=op.f('fk_unit_memberships_stamp_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['trade_unit_id'], ['trade_units.id'], name=op.f('fk_unit_memberships_trade_unit_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_unit_memberships'))
    )
    op.create_index(op.f('ix_unit_memberships_added_at'), 'unit_memberships', ['added_at'], unique=False)
    op.create_index('ix_unit_memberships_unit', 'unit_memberships', ['trade_unit_id'], unique=False)
    op.create_index('uq_unit_memberships_active_stamp', 'unit_memberships', ['stamp_id'], unique=True, postgresql_where=sa.text('removed_at IS NULL'))

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
    # Movement history, customs declarations and published checkpoints are regulatory
    # evidence, so the downgrade refuses rather than deleting them.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM trace_events) "
        "OR EXISTS (SELECT 1 FROM consignments) "
        "OR EXISTS (SELECT 1 FROM transparency_checkpoints) "
        "OR EXISTS (SELECT 1 FROM data_exports) THEN "
        "RAISE EXCEPTION 'traceability, customs or transparency records exist; archive "
        "them before downgrading'; "
        "END IF; END $$;"
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table}")
    op.drop_index('uq_unit_memberships_active_stamp', table_name='unit_memberships', postgresql_where=sa.text('removed_at IS NULL'))
    op.drop_index('ix_unit_memberships_unit', table_name='unit_memberships')
    op.drop_index(op.f('ix_unit_memberships_added_at'), table_name='unit_memberships')
    op.drop_table('unit_memberships')
    op.drop_index(op.f('ix_consignment_stamps_linked_at'), table_name='consignment_stamps')
    op.drop_index('ix_consignment_stamps_consignment', table_name='consignment_stamps')
    op.drop_table('consignment_stamps')
    op.drop_index('ix_anomalies_kind_detected', table_name='anomalies')
    op.drop_index(op.f('ix_anomalies_detected_at'), table_name='anomalies')
    op.drop_table('anomalies')
    op.drop_index('ix_trace_events_unit', table_name='trace_events')
    op.drop_index(op.f('ix_trace_events_recorded_at'), table_name='trace_events')
    op.drop_index('ix_trace_events_company_occurred', table_name='trace_events')
    op.drop_table('trace_events')
    op.drop_index(op.f('ix_consignments_created_at'), table_name='consignments')
    op.drop_index('ix_consignments_company_status', table_name='consignments')
    op.drop_table('consignments')
    op.drop_index(op.f('ix_transparency_checkpoints_created_at'), table_name='transparency_checkpoints')
    op.drop_table('transparency_checkpoints')
    op.drop_index('ix_trade_units_parent', table_name='trade_units')
    op.drop_index(op.f('ix_trade_units_created_at'), table_name='trade_units')
    op.drop_index('ix_trade_units_company_status', table_name='trade_units')
    op.drop_table('trade_units')
    op.drop_index(op.f('ix_data_exports_created_at'), table_name='data_exports')
    op.drop_table('data_exports')
    op.drop_index(op.f('ix_facilities_created_at'), table_name='facilities')
    op.drop_index('ix_facilities_company', table_name='facilities')
    op.drop_table('facilities')

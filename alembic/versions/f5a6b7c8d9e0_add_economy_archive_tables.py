"""add immutable economy archive tables

Revision ID: f5a6b7c8d9e0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f5a6b7c8d9e0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "economy_ledger_archive",
        sa.Column("archive_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("amount_microcredits", sa.BigInteger(), nullable=False),
        sa.Column("delta_microcredits", sa.BigInteger(), nullable=False),
        sa.Column("balance_before_microcredits", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_microcredits", sa.BigInteger(), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("mission_id", sa.String(128), nullable=True),
        sa.Column("idempotency_key_hash", sa.CHAR(64), nullable=False),
        sa.Column("request_hash", sa.CHAR(64), nullable=False),
        sa.Column("outbox_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("record_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_sha256", sa.CHAR(64), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_microcredits > 0", name="ck_econ_archive_amount_positive"),
        sa.CheckConstraint("outbox_sequence > 0", name="ck_econ_archive_sequence_positive"),
        sa.CheckConstraint("schema_version = 1", name="ck_econ_archive_schema_v1"),
        sa.PrimaryKeyConstraint("archive_id"),
        sa.UniqueConstraint("transaction_id"),
        sa.UniqueConstraint("tenant_id", "outbox_sequence", name="uq_econ_archive_tenant_seq"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key_hash", name="uq_econ_archive_tenant_idem"
        ),
    )
    op.create_index(
        "ix_econ_archive_tenant_created_seq",
        "economy_ledger_archive",
        ["tenant_id", sa.text("created_at DESC"), sa.text("outbox_sequence DESC")],
    )
    op.create_index(
        "ix_econ_archive_tenant_agent_created_seq",
        "economy_ledger_archive",
        ["tenant_id", "agent_id", sa.text("created_at DESC"), sa.text("outbox_sequence DESC")],
    )
    op.create_index(
        "ix_econ_archive_tenant_mission_created",
        "economy_ledger_archive",
        ["tenant_id", "mission_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("mission_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION reject_economy_archive_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'economy_ledger_archive is append-only'
                USING ERRCODE = '55000';
        END;
        $$;
        CREATE TRIGGER economy_archive_append_only
        BEFORE UPDATE OR DELETE ON economy_ledger_archive
        FOR EACH ROW EXECUTE FUNCTION reject_economy_archive_mutation();
        """
    )
    op.create_table(
        "economy_archive_checkpoint",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("last_outbox_stream_id", sa.String(64), nullable=False),
        sa.Column("last_outbox_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_record_sha256", sa.CHAR(64), nullable=False),
        sa.Column("archived_count", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "economy_topup_operation",
        sa.Column("topup_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key_hash", sa.CHAR(64), nullable=False),
        sa.Column("request_hash", sa.CHAR(64), nullable=False),
        sa.Column("amount_microcredits", sa.BigInteger(), nullable=False),
        sa.Column("allocation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_microcredits > 0", name="ck_econ_topup_amount_positive"),
        sa.CheckConstraint("target_count > 0", name="ck_econ_topup_target_positive"),
        sa.CheckConstraint(
            "completed_count >= 0 AND completed_count <= target_count",
            name="ck_econ_topup_completed_range",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'completed', 'failed')",
            name="ck_econ_topup_state",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key_hash", name="uq_econ_topup_tenant_idem"),
    )


def downgrade() -> None:
    op.drop_table("economy_topup_operation")
    op.drop_table("economy_archive_checkpoint")
    op.execute("DROP TRIGGER economy_archive_append_only ON economy_ledger_archive")
    op.execute("DROP FUNCTION reject_economy_archive_mutation()")
    op.drop_index("ix_econ_archive_tenant_mission_created", table_name="economy_ledger_archive")
    op.drop_index("ix_econ_archive_tenant_agent_created_seq", table_name="economy_ledger_archive")
    op.drop_index("ix_econ_archive_tenant_created_seq", table_name="economy_ledger_archive")
    op.drop_table("economy_ledger_archive")

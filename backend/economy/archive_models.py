"""PostgreSQL persistence models for the immutable economy ledger archive."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


class EconomyLedgerArchive(Base):
    """One append-only, checksum-protected economy transaction."""

    __tablename__ = "economy_ledger_archive"
    __table_args__ = (
        CheckConstraint("amount_microcredits > 0", name="ck_econ_archive_amount_positive"),
        CheckConstraint("outbox_sequence > 0", name="ck_econ_archive_sequence_positive"),
        CheckConstraint("schema_version = 1", name="ck_econ_archive_schema_v1"),
        UniqueConstraint("tenant_id", "outbox_sequence", name="uq_econ_archive_tenant_seq"),
        UniqueConstraint("tenant_id", "idempotency_key_hash", name="uq_econ_archive_tenant_idem"),
        Index(
            "ix_econ_archive_tenant_created_seq",
            "tenant_id",
            text("created_at DESC"),
            text("outbox_sequence DESC"),
        ),
        Index(
            "ix_econ_archive_tenant_agent_created_seq",
            "tenant_id",
            "agent_id",
            text("created_at DESC"),
            text("outbox_sequence DESC"),
        ),
        Index(
            "ix_econ_archive_tenant_mission_created",
            "tenant_id",
            "mission_id",
            text("created_at DESC"),
            postgresql_where=text("mission_id IS NOT NULL"),
        ),
    )

    archive_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_before_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    mission_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outbox_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    record_json: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class EconomyArchiveCheckpoint(Base):
    """Last contiguously committed archive position for one tenant."""

    __tablename__ = "economy_archive_checkpoint"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_outbox_stream_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_outbox_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EconomyTopupOperation(Base):
    """Durable progress record for an idempotent multi-agent top-up."""

    __tablename__ = "economy_topup_operation"
    __table_args__ = (
        CheckConstraint("amount_microcredits > 0", name="ck_econ_topup_amount_positive"),
        CheckConstraint("target_count > 0", name="ck_econ_topup_target_positive"),
        CheckConstraint(
            "completed_count >= 0 AND completed_count <= target_count",
            name="ck_econ_topup_completed_range",
        ),
        CheckConstraint(
            "state IN ('pending', 'running', 'completed', 'failed')",
            name="ck_econ_topup_state",
        ),
        UniqueConstraint("tenant_id", "idempotency_key_hash", name="uq_econ_topup_tenant_idem"),
    )

    topup_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_microcredits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allocation_json: Mapped[dict[str, int]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

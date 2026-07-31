"""Idempotent Redis-outbox to PostgreSQL economy ledger archiver."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Sequence
from uuid import UUID

from redis.exceptions import NoScriptError, ResponseError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.economy.archive_models import EconomyArchiveCheckpoint, EconomyLedgerArchive
from backend.economy.contracts import LedgerOperation, LedgerTransaction, canonical_json_bytes
from backend.economy.keyspace import EconomyKeyspace


ARCHIVE_CONSUMER_GROUP = "economy-archive-v1"
_TRANSACTION_FIELDS = frozenset(
    {
        "transaction_id",
        "tenant_id",
        "agent_id",
        "operation",
        "amount_microcredits",
        "delta_microcredits",
        "balance_before_microcredits",
        "balance_after_microcredits",
        "resource_type",
        "reason",
        "mission_id",
        "idempotency_key_hash",
        "request_hash",
        "outbox_sequence",
        "created_at",
        "schema_version",
    }
)
_IMMUTABLE_FIELDS = (
    "transaction_id",
    "tenant_id",
    "agent_id",
    "operation",
    "amount_microcredits",
    "delta_microcredits",
    "balance_before_microcredits",
    "balance_after_microcredits",
    "resource_type",
    "reason",
    "mission_id",
    "idempotency_key_hash",
    "request_hash",
    "outbox_sequence",
    "created_at",
    "schema_version",
    "record_json",
    "record_sha256",
)


class ArchiveCorruption(RuntimeError):
    """Raised when outbox or archive data violates an immutable invariant."""


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """A validated Redis stream entry ready for durable insertion."""

    stream_id: str
    transaction: LedgerTransaction
    record_json: str
    record_sha256: str
    payload: dict[str, object]


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise ArchiveCorruption("Redis outbox contains a non-text value")


def _validate_entry(tenant_id: str, stream_id: Any, fields: dict[Any, Any]) -> OutboxRecord:
    normalized = {_text(key): _text(value) for key, value in fields.items()}
    if frozenset(normalized) != {"sequence", "transaction_id", "record_json"}:
        raise ArchiveCorruption("Redis outbox entry has unexpected fields")
    record_json = normalized["record_json"]
    try:
        payload = json.loads(record_json)
        if not isinstance(payload, dict) or frozenset(payload) != _TRANSACTION_FIELDS:
            raise ValueError("unexpected transaction fields")
        if not isinstance(payload["created_at"], str) or not payload["created_at"].endswith("Z"):
            raise ValueError("non-canonical transaction timestamp")
        created_at = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
        transaction = LedgerTransaction(
            transaction_id=payload["transaction_id"],
            tenant_id=payload["tenant_id"],
            agent_id=payload["agent_id"],
            operation=LedgerOperation(payload["operation"]),
            amount_microcredits=payload["amount_microcredits"],
            delta_microcredits=payload["delta_microcredits"],
            balance_before_microcredits=payload["balance_before_microcredits"],
            balance_after_microcredits=payload["balance_after_microcredits"],
            resource_type=payload["resource_type"],
            reason=payload["reason"],
            mission_id=payload["mission_id"],
            idempotency_key_hash=payload["idempotency_key_hash"],
            request_hash=payload["request_hash"],
            outbox_sequence=payload["outbox_sequence"],
            created_at=created_at,
            schema_version=payload["schema_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ArchiveCorruption("Redis outbox record is invalid") from exc
    if canonical_json_bytes(payload).decode() != record_json:
        raise ArchiveCorruption("Redis outbox record is not canonical JSON")
    if transaction.tenant_id != tenant_id:
        raise ArchiveCorruption("Redis outbox record crossed a tenant boundary")
    if normalized["sequence"] != str(transaction.outbox_sequence):
        raise ArchiveCorruption("Redis outbox sequence disagrees with its record")
    if normalized["transaction_id"] != transaction.transaction_id:
        raise ArchiveCorruption("Redis outbox transaction ID disagrees with its record")
    return OutboxRecord(
        stream_id=_text(stream_id),
        transaction=transaction,
        record_json=record_json,
        record_sha256=hashlib.sha256(record_json.encode()).hexdigest(),
        payload=payload,
    )


class EconomyArchiver:
    """Archive bounded, contiguous tenant batches and acknowledge after commit."""

    def __init__(
        self,
        redis_client: Any,
        sessions: async_sessionmaker[AsyncSession],
        *,
        consumer_name: str,
        max_records: int = 100,
        max_bytes: int = 1_048_576,
    ) -> None:
        if not consumer_name or max_records < 1 or max_bytes < 1:
            raise ValueError("consumer_name and positive batch bounds are required")
        self._redis, self._sessions = redis_client, sessions
        self.consumer_name, self.max_records, self.max_bytes = consumer_name, max_records, max_bytes
        self._ack_sha: str | None = None

    async def ensure_group(self, tenant_id: str) -> None:
        keys = EconomyKeyspace.for_tenant(tenant_id)
        try:
            await self._redis.xgroup_create(
                keys.outbox, ARCHIVE_CONSUMER_GROUP, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_batch(self, tenant_id: str) -> list[OutboxRecord]:
        await self.ensure_group(tenant_id)
        keys = EconomyKeyspace.for_tenant(tenant_id)
        claimed = await self._redis.xautoclaim(
            keys.outbox,
            ARCHIVE_CONSUMER_GROUP,
            self.consumer_name,
            min_idle_time=0,
            start_id="0-0",
            count=self.max_records,
        )
        entries = claimed[1]
        if not entries:
            response = await self._redis.xreadgroup(
                ARCHIVE_CONSUMER_GROUP,
                self.consumer_name,
                {keys.outbox: ">"},
                count=self.max_records,
                block=1,
            )
            entries = response[0][1] if response else []
        records: list[OutboxRecord] = []
        used = 0
        for stream_id, fields in entries:
            record = _validate_entry(tenant_id, stream_id, fields)
            size = len(record.record_json.encode("utf-8"))
            if records and used + size > self.max_bytes:
                break
            if size > self.max_bytes:
                raise ArchiveCorruption("one outbox record exceeds the archive byte limit")
            records.append(record)
            used += size
        return records

    async def _quarantine(self, tenant_id: str, reason: str) -> None:
        keys = EconomyKeyspace.for_tenant(tenant_id)
        await self._redis.hset(
            keys.meta, mapping={"archive_state": "corrupt", "archive_error": reason[:128]}
        )

    @staticmethod
    def _row_values(record: OutboxRecord) -> dict[str, object]:
        transaction = record.transaction
        return {
            "transaction_id": UUID(transaction.transaction_id),
            "tenant_id": transaction.tenant_id,
            "agent_id": transaction.agent_id,
            "operation": transaction.operation.value,
            "amount_microcredits": transaction.amount_microcredits,
            "delta_microcredits": transaction.delta_microcredits,
            "balance_before_microcredits": transaction.balance_before_microcredits,
            "balance_after_microcredits": transaction.balance_after_microcredits,
            "resource_type": transaction.resource_type,
            "reason": transaction.reason,
            "mission_id": transaction.mission_id,
            "idempotency_key_hash": transaction.idempotency_key_hash,
            "request_hash": transaction.request_hash,
            "outbox_sequence": transaction.outbox_sequence,
            "created_at": transaction.created_at,
            "schema_version": transaction.schema_version,
            "record_json": record.payload,
            "record_sha256": record.record_sha256,
        }

    async def archive_batch(self, tenant_id: str, records: Sequence[OutboxRecord]) -> int:
        if not records:
            return 0
        ordered = sorted(records, key=lambda item: item.transaction.outbox_sequence)
        if list(records) != ordered:
            raise ArchiveCorruption("archive batch is not sequence ordered")
        if any(
            current.transaction.outbox_sequence != previous.transaction.outbox_sequence + 1
            for previous, current in zip(ordered, ordered[1:])
        ):
            raise ArchiveCorruption("archive batch contains a sequence gap")
        try:
            keys = EconomyKeyspace.for_tenant(tenant_id)
            ack_text = await self._redis.hget(keys.meta, "archive_ack_sequence")
            if ack_text is None:
                raise ArchiveCorruption("archive acknowledgement state is missing")
            ack_prior = int(_text(ack_text))
            if ordered[0].transaction.outbox_sequence != ack_prior + 1:
                raise ArchiveCorruption(
                    "archive batch is not contiguous with Redis acknowledgement"
                )
            async with self._sessions() as session, session.begin():
                checkpoint = await session.get(
                    EconomyArchiveCheckpoint, tenant_id, with_for_update=True
                )
                database_prior = checkpoint.last_outbox_sequence if checkpoint else 0
                if database_prior < ack_prior:
                    raise ArchiveCorruption("PostgreSQL checkpoint trails Redis acknowledgement")
                for record in ordered:
                    values = self._row_values(record)
                    result = await session.execute(
                        insert(EconomyLedgerArchive).values(**values).on_conflict_do_nothing()
                    )
                    if result.rowcount == 0:
                        existing = await session.scalar(
                            select(EconomyLedgerArchive).where(
                                EconomyLedgerArchive.transaction_id == values["transaction_id"]
                            )
                        )
                        if existing is None or any(
                            getattr(existing, field) != values[field] for field in _IMMUTABLE_FIELDS
                        ):
                            raise ArchiveCorruption("conflicting immutable archive record")
                last = ordered[-1]
                now = datetime.now(timezone.utc)
                newly_checkpointed = max(0, last.transaction.outbox_sequence - database_prior)
                if checkpoint is None:
                    session.add(
                        EconomyArchiveCheckpoint(
                            tenant_id=tenant_id,
                            last_outbox_stream_id=last.stream_id,
                            last_outbox_sequence=last.transaction.outbox_sequence,
                            last_record_sha256=last.record_sha256,
                            archived_count=len(ordered),
                            updated_at=now,
                        )
                    )
                else:
                    if last.transaction.outbox_sequence > database_prior:
                        checkpoint.last_outbox_stream_id = last.stream_id
                        checkpoint.last_outbox_sequence = last.transaction.outbox_sequence
                        checkpoint.last_record_sha256 = last.record_sha256
                        checkpoint.archived_count += newly_checkpointed
                        checkpoint.updated_at = now
            await self._ack(tenant_id, ack_prior, ordered)
            return len(ordered)
        except ArchiveCorruption as exc:
            await self._quarantine(tenant_id, str(exc))
            raise

    async def _ack(self, tenant_id: str, prior: int, records: Sequence[OutboxRecord]) -> None:
        source = resources.files("backend.economy.lua").joinpath("ack_archive_v1.lua").read_text()
        if self._ack_sha is None:
            self._ack_sha = _text(await self._redis.script_load(source))
        keys = EconomyKeyspace.for_tenant(tenant_id)
        args = [ARCHIVE_CONSUMER_GROUP, str(prior), str(records[-1].transaction.outbox_sequence)]
        args.extend(record.stream_id for record in records)
        try:
            response = await self._redis.evalsha(self._ack_sha, 2, keys.meta, keys.outbox, *args)
        except NoScriptError:
            self._ack_sha = _text(await self._redis.script_load(source))
            response = await self._redis.evalsha(self._ack_sha, 2, keys.meta, keys.outbox, *args)
        status = _text(response[0])
        if status not in {"ACKNOWLEDGED", "REPLAYED"}:
            raise ArchiveCorruption(f"archive acknowledgement rejected: {_text(response[1])}")

    async def run_once(self, tenant_id: str) -> int:
        """Read, durably archive, and acknowledge at most one bounded batch."""
        records = await self.read_batch(tenant_id)
        return await self.archive_batch(tenant_id, records)

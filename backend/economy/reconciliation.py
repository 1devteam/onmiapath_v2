"""Pure validation and recovery planning for v2 ledger archives."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, cast

from backend.economy.contracts import LedgerOperation, LedgerTransaction, canonical_json_bytes
from backend.economy.keyspace import LEDGER_SCHEMA_VERSION, EconomyKeyspace


@dataclass(frozen=True, slots=True)
class ReconciliationMismatch:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    tenant_id: str
    transaction_count: int
    last_sequence: int
    last_record_sha256: str | None
    balances: dict[str, int]
    mismatches: tuple[ReconciliationMismatch, ...]

    @property
    def verified(self) -> bool:
        return not self.mismatches


def reconcile_transactions(
    tenant_id: str,
    records: Iterable[tuple[LedgerTransaction, str]],
    *,
    expected_count: int | None = None,
    expected_last_sha256: str | None = None,
) -> ReconciliationReport:
    """Verify ordered archive records and produce a safe replay state plan."""
    ordered = list(records)
    mismatches: list[ReconciliationMismatch] = []
    balances: dict[str, int] = {}
    last_sha: str | None = None
    seen_ids: set[str] = set()
    seen_idempotency: set[str] = set()
    opening_agents: set[str] = set()
    pending_opening: LedgerTransaction | None = None
    for ordinal, (transaction, supplied_sha) in enumerate(ordered, start=1):
        payload = {field: getattr(transaction, field) for field in transaction.__dataclass_fields__}
        payload["operation"] = transaction.operation.value
        payload["created_at"] = transaction.created_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        calculated_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if transaction.tenant_id != tenant_id:
            mismatches.append(ReconciliationMismatch("tenant_mismatch", transaction.transaction_id))
        if transaction.outbox_sequence != ordinal:
            mismatches.append(
                ReconciliationMismatch(
                    "sequence_gap", f"expected {ordinal}, found {transaction.outbox_sequence}"
                )
            )
        if supplied_sha != calculated_sha:
            mismatches.append(
                ReconciliationMismatch("checksum_mismatch", transaction.transaction_id)
            )
        if transaction.transaction_id in seen_ids:
            mismatches.append(
                ReconciliationMismatch("duplicate_transaction", transaction.transaction_id)
            )
        if transaction.idempotency_key_hash in seen_idempotency:
            mismatches.append(
                ReconciliationMismatch("duplicate_idempotency", transaction.idempotency_key_hash)
            )
        if pending_opening is not None and (
            transaction.agent_id != pending_opening.agent_id
            or transaction.operation is LedgerOperation.OPENING_GRANT
        ):
            mismatches.append(
                ReconciliationMismatch("orphan_opening_grant", pending_opening.transaction_id)
            )
        pending_opening = None
        if transaction.operation is LedgerOperation.OPENING_GRANT:
            if transaction.agent_id in opening_agents:
                mismatches.append(
                    ReconciliationMismatch("duplicate_opening_grant", transaction.transaction_id)
                )
            opening_agents.add(transaction.agent_id)
            pending_opening = transaction
        expected_before = balances.get(transaction.agent_id, 0)
        if transaction.balance_before_microcredits != expected_before:
            mismatches.append(
                ReconciliationMismatch("balance_chain_mismatch", transaction.transaction_id)
            )
        balances[transaction.agent_id] = transaction.balance_after_microcredits
        seen_ids.add(transaction.transaction_id)
        seen_idempotency.add(transaction.idempotency_key_hash)
        last_sha = supplied_sha
    if pending_opening is not None:
        mismatches.append(
            ReconciliationMismatch("orphan_opening_grant", pending_opening.transaction_id)
        )
    if expected_count is not None and expected_count != len(ordered):
        mismatches.append(
            ReconciliationMismatch(
                "count_mismatch", f"expected {expected_count}, found {len(ordered)}"
            )
        )
    if expected_last_sha256 is not None and expected_last_sha256 != last_sha:
        mismatches.append(
            ReconciliationMismatch("checkpoint_mismatch", "last archive checksum differs")
        )
    return ReconciliationReport(
        tenant_id, len(ordered), len(ordered), last_sha, balances, tuple(mismatches)
    )


def _transaction_payload(transaction: LedgerTransaction) -> dict[str, object]:
    payload: dict[str, object] = {
        field: getattr(transaction, field) for field in transaction.__dataclass_fields__
    }
    payload["operation"] = transaction.operation.value
    payload["created_at"] = transaction.created_at.isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    return payload


async def restore_archive_to_empty_tenant(
    redis_client: Any,
    tenant_id: str,
    records: Iterable[tuple[LedgerTransaction, str]],
    *,
    lock_owner: str,
) -> ReconciliationReport:
    """Atomically replay a verified archive into an otherwise empty tenant keyspace.

    The caller must already own the fenced migration lock. Existing v2 data is
    never overwritten and this function deliberately contains no delete path.
    """
    if not lock_owner:
        raise ValueError("lock_owner is required")
    materialized = list(records)
    report = reconcile_transactions(tenant_id, materialized)
    if not report.verified:
        raise ValueError("archive reconciliation failed; refusing recovery write")
    keys = EconomyKeyspace.for_tenant(tenant_id)
    if await redis_client.get(keys.migration_lock) != lock_owner:
        raise ValueError("caller does not own the tenant migration lock")

    cursor: int | str = 0
    existing: set[str] = set()
    while True:
        cursor, batch = await redis_client.scan(cursor=cursor, match=f"{keys.prefix}:*", count=500)
        existing.update(
            item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in batch
        )
        if int(cursor) == 0:
            break
    allowed = {keys.migration_lock, keys.migration_journal}
    if existing - allowed:
        raise ValueError("target v2 tenant keyspace is not empty")

    per_agent: dict[str, dict[str, object]] = {}
    pending_openings: dict[str, LedgerTransaction] = {}
    pipeline = redis_client.pipeline(transaction=True)
    for transaction, _ in materialized:
        payload = _transaction_payload(transaction)
        record_json = canonical_json_bytes(payload).decode("utf-8")
        fields = {
            "sequence": str(transaction.outbox_sequence),
            "transaction_id": transaction.transaction_id,
            "record_json": record_json,
        }
        stream_id = f"{transaction.outbox_sequence}-0"
        pipeline.xadd(keys.tenant_ledger, fields, id=stream_id)
        pipeline.xadd(keys.agent_ledger(transaction.agent_id), fields, id=stream_id)
        if transaction.operation is LedgerOperation.OPENING_GRANT:
            pending_openings[transaction.agent_id] = transaction
        else:
            opening = pending_openings.pop(transaction.agent_id, None)
            result_json = canonical_json_bytes(
                {
                    "balance_microcredits": transaction.balance_after_microcredits,
                    "opening_transaction": (
                        None if opening is None else _transaction_payload(opening)
                    ),
                    "transaction": payload,
                }
            ).decode("utf-8")
            pipeline.hset(
                keys.idempotency_from_digest(transaction.idempotency_key_hash),
                mapping={
                    "schema_version": str(LEDGER_SCHEMA_VERSION),
                    "request_hash": transaction.request_hash,
                    "transaction_id": transaction.transaction_id,
                    "transaction_sequence": str(transaction.outbox_sequence),
                    "opening_transaction_id": "" if opening is None else opening.transaction_id,
                    "result_json": result_json,
                    "created_at": payload["created_at"],
                },
            )
        state = per_agent.setdefault(
            transaction.agent_id, {"earned": 0, "spent": 0, "last": transaction}
        )
        total_field = "spent" if transaction.operation is LedgerOperation.CHARGE else "earned"
        state[total_field] = cast(int, state[total_field]) + transaction.amount_microcredits
        state["last"] = transaction

    for agent_id, state in per_agent.items():
        last = cast(LedgerTransaction, state["last"])
        pipeline.hset(
            keys.balance(agent_id),
            mapping={
                "schema_version": str(LEDGER_SCHEMA_VERSION),
                "agent_id": agent_id,
                "agent_type": "unknown",
                "balance_microcredits": str(last.balance_after_microcredits),
                "total_earned_microcredits": str(state["earned"]),
                "total_spent_microcredits": str(state["spent"]),
                "last_sequence": str(last.outbox_sequence),
                "last_transaction_id": last.transaction_id,
                "updated_at": _transaction_payload(last)["created_at"],
            },
        )
        pipeline.sadd(keys.agents, agent_id)
    pipeline.hset(
        keys.meta,
        mapping={
            "schema_version": str(LEDGER_SCHEMA_VERSION),
            "next_sequence": str(report.last_sequence + 1),
            "archive_ack_sequence": str(report.last_sequence),
            "unarchived_count": "0",
            "oldest_unarchived_at_ms": "",
            "archive_state": "healthy",
            "archive_error": "",
            "memory_state": "healthy",
            "circuit_observed_at_ms": "0",
        },
    )
    pipeline.hset(
        keys.migration_journal,
        mapping={
            "state": "restored",
            "lock_owner_sha256": hashlib.sha256(lock_owner.encode("utf-8")).hexdigest(),
            "transaction_count": str(report.transaction_count),
            "last_record_sha256": report.last_record_sha256 or "",
        },
    )
    await pipeline.execute()
    return report

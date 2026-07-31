"""Live Redis recovery tests for fenced migration lock ownership."""

from __future__ import annotations

import os
import hashlib

import pytest
import redis.asyncio as redis

from backend.economy.keyspace import EconomyKeyspace
from backend.economy.contracts import (
    LedgerMutation,
    LedgerOperation,
    MutationDisposition,
    canonical_json_bytes,
)
from backend.economy.migration import MigrationLock
from backend.economy.reconciliation import restore_archive_to_empty_tenant
from backend.economy.redis_ledger import RedisEconomyLedger


pytestmark = [pytest.mark.integration, pytest.mark.economy]


@pytest.mark.asyncio
async def test_migration_lock_cannot_be_stolen_or_released_by_stale_owner():
    url = os.getenv("ECONOMY_TEST_REDIS_URL")
    if not url:
        pytest.skip("ECONOMY_TEST_REDIS_URL is required for live Redis migration tests")
    client = redis.from_url(url, decode_responses=True)
    await client.flushdb()
    try:
        lock = MigrationLock(client)
        assert await lock.acquire("migration-tenant", "owner-a", 30_000) == "ACQUIRED"
        assert await lock.acquire("migration-tenant", "owner-a", 30_000) == "RENEWED"
        assert await lock.acquire("migration-tenant", "owner-b", 30_000) == "BUSY"
        assert not await lock.release("migration-tenant", "owner-b")
        key = EconomyKeyspace.for_tenant("migration-tenant").migration_lock
        assert await client.get(key) == "owner-a"
        assert await lock.release("migration-tenant", "owner-a")
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.mark.asyncio
async def test_verified_archive_replay_reconstructs_empty_redis_and_refuses_overwrite():
    url = os.getenv("ECONOMY_TEST_REDIS_URL")
    if not url:
        pytest.skip("ECONOMY_TEST_REDIS_URL is required for live Redis migration tests")
    client = redis.from_url(url, decode_responses=True)
    await client.flushdb()
    try:
        ledger = RedisEconomyLedger(client)
        command = LedgerMutation(
            tenant_id="restore-tenant",
            agent_id="agent-1",
            operation=LedgerOperation.CHARGE,
            amount_microcredits=10_000_000,
            resource_type="llm_call",
            reason="resource_usage",
            idempotency_key="archive-restore-test-key-0001",
            mission_id="mission-1",
            agent_type="commander",
        )
        await ledger.mutate(command)
        transactions = list(reversed(await ledger.read_transactions("restore-tenant", limit=10)))
        records = []
        for transaction in transactions:
            payload = {
                field: getattr(transaction, field) for field in transaction.__dataclass_fields__
            }
            payload["operation"] = transaction.operation.value
            payload["created_at"] = transaction.created_at.isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z")
            records.append((transaction, hashlib.sha256(canonical_json_bytes(payload)).hexdigest()))

        await client.flushdb()
        lock = MigrationLock(client)
        assert await lock.acquire("restore-tenant", "restore-owner", 30_000) == "ACQUIRED"
        report = await restore_archive_to_empty_tenant(
            client, "restore-tenant", records, lock_owner="restore-owner"
        )
        restored = RedisEconomyLedger(client)
        assert report.verified
        assert [
            item.transaction_id
            for item in await restored.read_transactions("restore-tenant", limit=10)
        ] == [item.transaction_id for item in reversed(transactions)]
        assert (await restored.read_balance("restore-tenant", "agent-1"))[
            "balance_microcredits"
        ] == 990_000_000

        with pytest.raises(ValueError, match="not empty"):
            await restore_archive_to_empty_tenant(
                client, "restore-tenant", records, lock_owner="restore-owner"
            )
        assert await lock.release("restore-tenant", "restore-owner")
        replayed = await restored.mutate(command)
        assert replayed.disposition is MutationDisposition.REPLAYED
        assert replayed.opening_transaction == transactions[0]
    finally:
        await client.flushdb()
        await client.aclose()

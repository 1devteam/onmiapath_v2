"""PostgreSQL 15 and Redis 7 integration tests for economy archival."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import redis.asyncio as redis
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.economy.archive_models import EconomyArchiveCheckpoint, EconomyLedgerArchive
from backend.economy.archiver import ArchiveCorruption, EconomyArchiver
from backend.economy.contracts import LedgerMutation, LedgerOperation
from backend.economy.keyspace import EconomyKeyspace
from backend.economy.redis_ledger import RedisEconomyLedger


pytestmark = [pytest.mark.integration, pytest.mark.economy]


@pytest_asyncio.fixture
async def archive_services() -> AsyncIterator[tuple[redis.Redis, async_sessionmaker[AsyncSession]]]:
    redis_url = os.getenv("ECONOMY_TEST_REDIS_URL")
    postgres_url = os.getenv("ECONOMY_TEST_POSTGRES_URL")
    if not redis_url or not postgres_url:
        pytest.skip("live Redis and PostgreSQL URLs are required for archive tests")
    client = redis.from_url(redis_url, decode_responses=True)
    engine = create_async_engine(postgres_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await client.flushdb()
    async with sessions() as session, session.begin():
        await session.execute(delete(EconomyArchiveCheckpoint))
        await session.execute(text("TRUNCATE economy_ledger_archive RESTART IDENTITY"))
    try:
        yield client, sessions
    finally:
        await client.flushdb()
        async with sessions() as session, session.begin():
            await session.execute(delete(EconomyArchiveCheckpoint))
            await session.execute(text("TRUNCATE economy_ledger_archive RESTART IDENTITY"))
        await client.aclose()
        await engine.dispose()


async def _create_outbox(client: redis.Redis, tenant_id: str = "archive-tenant") -> None:
    ledger = RedisEconomyLedger(client)
    await ledger.mutate(
        LedgerMutation(
            tenant_id=tenant_id,
            agent_id="agent-1",
            operation=LedgerOperation.CHARGE,
            amount_microcredits=10_000_000,
            resource_type="llm_call",
            reason="resource_usage",
            idempotency_key="archive-integration-key-0001",
            mission_id="mission-1",
            agent_type="commander",
        )
    )


@pytest.mark.asyncio
async def test_archives_contiguous_batch_and_acknowledges_exactly_once(archive_services):
    client, sessions = archive_services
    await _create_outbox(client)
    archiver = EconomyArchiver(client, sessions, consumer_name="worker-1")

    assert await archiver.run_once("archive-tenant") == 2
    keys = EconomyKeyspace.for_tenant("archive-tenant")
    meta = await client.hgetall(keys.meta)
    assert meta["archive_ack_sequence"] == "2"
    assert meta["unarchived_count"] == "0"
    assert meta["oldest_unarchived_at_ms"] == ""
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(EconomyLedgerArchive)) == 2
        checkpoint = await session.get(EconomyArchiveCheckpoint, "archive-tenant")
        assert checkpoint is not None
        assert checkpoint.last_outbox_sequence == 2
        assert checkpoint.archived_count == 2


@pytest.mark.asyncio
async def test_ack_failure_redelivers_without_duplicate_rows(archive_services, monkeypatch):
    client, sessions = archive_services
    await _create_outbox(client)
    first = EconomyArchiver(client, sessions, consumer_name="worker-failed")

    async def fail_ack(*args, **kwargs):
        raise ConnectionError("injected acknowledgement failure")

    monkeypatch.setattr(first, "_ack", fail_ack)
    with pytest.raises(ConnectionError):
        await first.run_once("archive-tenant")

    recovered = EconomyArchiver(client, sessions, consumer_name="worker-recovery")
    assert await recovered.run_once("archive-tenant") == 2
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(EconomyLedgerArchive)) == 2
        checkpoint = await session.get(EconomyArchiveCheckpoint, "archive-tenant")
        assert checkpoint.archived_count == 2


@pytest.mark.asyncio
async def test_partial_batches_advance_oldest_pending_timestamp(archive_services):
    client, sessions = archive_services
    await _create_outbox(client)
    keys = EconomyKeyspace.for_tenant("archive-tenant")
    entries = await client.xrange(keys.outbox)
    second_stream_epoch = entries[1][0].split("-", 1)[0]
    archiver = EconomyArchiver(client, sessions, consumer_name="worker-1", max_records=1)

    assert await archiver.run_once("archive-tenant") == 1
    meta = await client.hgetall(keys.meta)
    assert meta["archive_ack_sequence"] == "1"
    assert meta["unarchived_count"] == "1"
    assert meta["oldest_unarchived_at_ms"] == second_stream_epoch
    assert await archiver.run_once("archive-tenant") == 1
    assert (await client.hgetall(keys.meta))["unarchived_count"] == "0"


@pytest.mark.asyncio
async def test_conflicting_duplicate_quarantines_tenant(archive_services):
    client, sessions = archive_services
    await _create_outbox(client)
    archiver = EconomyArchiver(client, sessions, consumer_name="worker-1")
    assert await archiver.run_once("archive-tenant") == 2

    keys = EconomyKeyspace.for_tenant("archive-tenant")
    original = (await client.xrange(keys.outbox))[1][1]
    payload = json.loads(original["record_json"])
    payload["outbox_sequence"] = 3
    payload["amount_microcredits"] += 1
    payload["delta_microcredits"] -= 1
    payload["balance_after_microcredits"] -= 1
    record_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    await client.xadd(
        keys.outbox,
        {"sequence": "3", "transaction_id": payload["transaction_id"], "record_json": record_json},
    )
    await client.hset(keys.meta, mapping={"next_sequence": "4", "unarchived_count": "1"})

    with pytest.raises(ArchiveCorruption):
        await EconomyArchiver(client, sessions, consumer_name="worker-2").run_once("archive-tenant")
    assert await client.hget(keys.meta, "archive_state") == "corrupt"


@pytest.mark.asyncio
async def test_archive_table_rejects_update_and_delete(archive_services):
    client, sessions = archive_services
    await _create_outbox(client)
    await EconomyArchiver(client, sessions, consumer_name="worker-1").run_once("archive-tenant")
    async with sessions() as session:
        with pytest.raises(Exception):
            async with session.begin():
                await session.execute(text("UPDATE economy_ledger_archive SET reason = 'tampered'"))
        await session.rollback()
        with pytest.raises(Exception):
            async with session.begin():
                await session.execute(text("DELETE FROM economy_ledger_archive"))

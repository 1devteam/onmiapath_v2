"""Live Redis 7 tests for the atomic economy mutation boundary."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError

from backend.economy.contracts import (
    AccountQuarantined,
    ArchiveLagLimit,
    CorruptLedgerState,
    IdempotencyConflict,
    InsufficientFunds,
    LedgerIntegerOverflow,
    LedgerMutation,
    LedgerOperation,
    LedgerSchemaMismatch,
    MigrationLocked,
    MutationDisposition,
    MutationOutcomeUnknown,
)
from backend.economy.keyspace import EconomyKeyspace
from backend.economy.redis_ledger import (
    RedisEconomyLedger,
    RedisLedgerCompatibilityFacade,
)


pytestmark = [pytest.mark.integration, pytest.mark.economy]


def _test_idempotency_key(label: str) -> str:
    """Build a deterministic non-secret retry identity for test cases."""
    return f"test:{label}:0000000000000000"


def mutation(
    *,
    tenant_id: str = "redis-ledger-tenant",
    agent_id: str = "agent-1",
    operation: LedgerOperation = LedgerOperation.CHARGE,
    amount: int = 10_000_000,
    idempotency_key: str = "redis-ledger-test-key-0001",
) -> LedgerMutation:
    return LedgerMutation(
        tenant_id=tenant_id,
        agent_id=agent_id,
        operation=operation,
        amount_microcredits=amount,
        resource_type="llm_call" if operation is LedgerOperation.CHARGE else "mission_reward",
        reason="resource_usage" if operation is LedgerOperation.CHARGE else "earned_reward",
        idempotency_key=idempotency_key,
        mission_id="mission-1",
        agent_type="commander",
    )


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[redis.Redis]:
    url = os.getenv("ECONOMY_TEST_REDIS_URL")
    if not url:
        pytest.skip("ECONOMY_TEST_REDIS_URL is required for live Redis ledger tests")
    client = redis.from_url(url, decode_responses=True)
    try:
        await client.ping()
    except RedisConnectionError:
        await client.aclose()
        pytest.fail(f"configured live Redis is unavailable: {url}")
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.mark.asyncio
async def test_read_unknown_balance_is_virtual_and_does_not_write(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    state = await ledger.read_balance("redis-ledger-tenant", "unknown-agent")

    assert state["balance_microcredits"] == 1_000_000_000
    assert state["materialized"] is False
    assert await redis_client.dbsize() == 0


@pytest.mark.asyncio
async def test_first_mutation_commits_opening_and_requested_records_atomically(
    redis_client: redis.Redis,
):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation()
    result = await ledger.mutate(command)
    keys = EconomyKeyspace.for_tenant(command.tenant_id)

    assert result.disposition is MutationDisposition.COMMITTED
    assert result.opening_transaction is not None
    assert result.opening_transaction.outbox_sequence == 1
    assert result.transaction.outbox_sequence == 2
    assert result.balance_microcredits == 990_000_000

    balance = await redis_client.hgetall(keys.balance(command.agent_id))
    assert balance["balance_microcredits"] == "990000000"
    assert balance["total_earned_microcredits"] == "1000000000"
    assert balance["total_spent_microcredits"] == "10000000"
    assert await redis_client.smembers(keys.agents) == {command.agent_id}

    tenant_records = await redis_client.xrange(keys.tenant_ledger)
    agent_records = await redis_client.xrange(keys.agent_ledger(command.agent_id))
    outbox_records = await redis_client.xrange(keys.outbox)
    assert len(tenant_records) == len(agent_records) == len(outbox_records) == 2
    for tenant_entry, agent_entry, outbox_entry in zip(
        tenant_records, agent_records, outbox_records
    ):
        assert tenant_entry[1] == agent_entry[1] == outbox_entry[1]
        assert (
            json.dumps(
                json.loads(tenant_entry[1]["record_json"]),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            == tenant_entry[1]["record_json"]
        )

    meta = await redis_client.hgetall(keys.meta)
    assert meta["next_sequence"] == "3"
    assert meta["unarchived_count"] == "2"
    assert meta["oldest_unarchived_at_ms"]


@pytest.mark.asyncio
async def test_same_key_replays_original_result_and_different_request_conflicts(
    redis_client: redis.Redis,
):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation()
    committed = await ledger.mutate(command)
    replayed = await ledger.mutate(command)

    assert replayed.disposition is MutationDisposition.REPLAYED
    assert replayed.transaction == committed.transaction
    assert replayed.opening_transaction == committed.opening_transaction

    conflicting = mutation(amount=20_000_000)
    with pytest.raises(IdempotencyConflict):
        await ledger.mutate(conflicting)

    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    assert await redis_client.xlen(keys.tenant_ledger) == 2
    assert (await ledger.read_balance(command.tenant_id, command.agent_id))[
        "balance_microcredits"
    ] == 990_000_000


@pytest.mark.asyncio
async def test_concurrent_same_key_has_one_commit_and_stable_replays(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation(idempotency_key="concurrent-identical-key-0001")
    results = await asyncio.gather(*(ledger.mutate(command) for _ in range(50)))

    assert sum(result.disposition is MutationDisposition.COMMITTED for result in results) == 1
    assert sum(result.disposition is MutationDisposition.REPLAYED for result in results) == 49
    assert len({result.transaction.transaction_id for result in results}) == 1
    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    assert await redis_client.xlen(keys.tenant_ledger) == 2


@pytest.mark.asyncio
async def test_concurrent_unique_charges_cannot_overdraw(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    first = mutation(amount=1_000_000, idempotency_key="opening-charge-key-000001")
    await ledger.mutate(first)

    commands = [
        mutation(
            amount=10_000_000,
            idempotency_key=f"concurrent-charge-key-{index:04d}",
        )
        for index in range(100)
    ]
    results = await asyncio.gather(
        *(ledger.mutate(command) for command in commands),
        return_exceptions=True,
    )

    committed = [result for result in results if not isinstance(result, Exception)]
    rejected = [result for result in results if isinstance(result, InsufficientFunds)]
    assert len(committed) == 99
    assert len(rejected) == 1
    state = await ledger.read_balance(first.tenant_id, first.agent_id)
    assert state["balance_microcredits"] == 9_000_000
    assert state["total_spent_microcredits"] == 991_000_000


@pytest.mark.asyncio
async def test_insufficient_funds_rejects_without_any_partial_write(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation(amount=1_000_000_001)

    with pytest.raises(InsufficientFunds):
        await ledger.mutate(command)

    assert await redis_client.dbsize() == 0


@pytest.mark.asyncio
async def test_overflow_rejects_before_first_write(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(
        redis_client,
        opening_grant_microcredits=9_223_372_036_854_775_807,
    )
    command = mutation(
        operation=LedgerOperation.REWARD,
        amount=1,
        idempotency_key="overflow-reward-key-000001",
    )

    with pytest.raises(LedgerIntegerOverflow):
        await ledger.mutate(command)

    assert await redis_client.dbsize() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key_index", range(9))
async def test_every_wrong_key_type_is_preflighted_without_adjacent_writes(
    redis_client: redis.Redis,
    key_index: int,
):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation()
    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    mutation_keys = keys.mutation_keys(command.agent_id, command.idempotency_key)
    if key_index == 8:
        await redis_client.hset(mutation_keys[key_index], "wrong", "type")
    else:
        await redis_client.set(mutation_keys[key_index], "wrong-type")

    with pytest.raises(CorruptLedgerState):
        await ledger.mutate(command)

    assert await redis_client.dbsize() == 1


@pytest.mark.asyncio
async def test_corrupt_balance_equation_rejects_without_mutation(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation()
    await ledger.mutate(command)
    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    before_stream_length = await redis_client.xlen(keys.tenant_ledger)
    await redis_client.hset(
        keys.balance(command.agent_id),
        "balance_microcredits",
        "123",
    )

    second = mutation(idempotency_key=_test_idempotency_key("corrupt-balance"))
    with pytest.raises(CorruptLedgerState):
        await ledger.mutate(second)

    assert await redis_client.xlen(keys.tenant_ledger) == before_stream_length
    assert (await redis_client.hgetall(keys.balance(command.agent_id)))[
        "balance_microcredits"
    ] == "123"


@pytest.mark.asyncio
async def test_idempotent_replay_precedes_mutable_balance_validation(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation()
    committed = await ledger.mutate(command)
    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    await redis_client.hset(
        keys.balance(command.agent_id),
        "balance_microcredits",
        "123",
    )

    replayed = await ledger.mutate(command)

    assert replayed.disposition is MutationDisposition.REPLAYED
    assert replayed.transaction == committed.transaction
    assert await redis_client.xlen(keys.tenant_ledger) == 2


@pytest.mark.asyncio
async def test_exact_int64_reward_boundary_then_overflow_is_stable(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client, opening_grant_microcredits=1)
    boundary = mutation(
        operation=LedgerOperation.REWARD,
        amount=9_223_372_036_854_775_806,
        idempotency_key=_test_idempotency_key("int64-boundary"),
    )
    result = await ledger.mutate(boundary)
    assert result.balance_microcredits == 9_223_372_036_854_775_807

    overflow = mutation(
        operation=LedgerOperation.REWARD,
        amount=1,
        idempotency_key="int64-overflow-reward-0001",
    )
    with pytest.raises(LedgerIntegerOverflow):
        await ledger.mutate(overflow)

    state = await ledger.read_balance(boundary.tenant_id, boundary.agent_id)
    assert state["balance_microcredits"] == 9_223_372_036_854_775_807


@pytest.mark.asyncio
async def test_charge_can_reach_exact_zero_without_overdraft(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation(amount=1_000_000_000)
    result = await ledger.mutate(command)

    assert result.balance_microcredits == 0
    state = await ledger.read_balance(command.tenant_id, command.agent_id)
    assert state["balance_microcredits"] == 0
    assert state["total_spent_microcredits"] == 1_000_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "error_type"),
    [
        ("migration", MigrationLocked),
        ("quarantine", AccountQuarantined),
        ("archive", ArchiveLagLimit),
        ("memory", ArchiveLagLimit),
    ],
)
async def test_operational_guards_reject_without_mutation(
    redis_client: redis.Redis,
    setup: str,
    error_type: type[Exception],
):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation()
    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    if setup == "migration":
        await redis_client.set(keys.migration_lock, "owner-token")
    elif setup == "quarantine":
        await redis_client.hset(keys.quarantine, command.agent_id, "investigate")
    else:
        await redis_client.hset(
            keys.meta,
            mapping={
                "schema_version": "1",
                "next_sequence": "1",
                "archive_ack_sequence": "0",
                "unarchived_count": "0",
                "oldest_unarchived_at_ms": "",
                "archive_state": "stopped" if setup == "archive" else "healthy",
                "memory_state": "stopped" if setup == "memory" else "healthy",
                "circuit_observed_at_ms": "0",
            },
        )

    with pytest.raises(error_type):
        await ledger.mutate(command)

    assert await redis_client.exists(keys.balance(command.agent_id)) == 0
    assert await redis_client.exists(keys.tenant_ledger) == 0
    assert await redis_client.exists(keys.idempotency(command.idempotency_key)) == 0


@pytest.mark.asyncio
async def test_archive_record_and_age_limits_fail_closed(redis_client: redis.Redis):
    command = mutation()
    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    base_meta = {
        "schema_version": "1",
        "next_sequence": "11",
        "archive_ack_sequence": "0",
        "unarchived_count": "10",
        "oldest_unarchived_at_ms": "1",
        "archive_state": "healthy",
        "memory_state": "healthy",
        "circuit_observed_at_ms": "0",
    }
    await redis_client.hset(keys.meta, mapping=base_meta)
    ledger = RedisEconomyLedger(
        redis_client,
        archive_hard_records=10,
        archive_hard_age_ms=9_223_372_036_854_775_807,
    )
    with pytest.raises(ArchiveLagLimit):
        await ledger.mutate(command)

    await redis_client.hset(
        keys.meta,
        mapping={**base_meta, "next_sequence": "2", "unarchived_count": "1"},
    )
    age_ledger = RedisEconomyLedger(
        redis_client,
        archive_hard_records=100,
        archive_hard_age_ms=1,
    )
    with pytest.raises(ArchiveLagLimit):
        await age_ledger.mutate(command)

    assert await redis_client.exists(keys.balance(command.agent_id)) == 0


@pytest.mark.asyncio
async def test_projected_opening_records_cannot_cross_archive_limit(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client, archive_hard_records=1)
    command = mutation()

    with pytest.raises(ArchiveLagLimit):
        await ledger.mutate(command)

    assert await redis_client.dbsize() == 0


@pytest.mark.asyncio
async def test_schema_mismatch_and_corrupt_idempotency_fail_before_balance_write(
    redis_client: redis.Redis,
):
    ledger = RedisEconomyLedger(redis_client)
    command = mutation()
    keys = EconomyKeyspace.for_tenant(command.tenant_id)
    await redis_client.hset(
        keys.meta,
        mapping={
            "schema_version": "2",
            "next_sequence": "1",
            "archive_ack_sequence": "0",
            "unarchived_count": "0",
            "oldest_unarchived_at_ms": "",
            "archive_state": "healthy",
            "memory_state": "healthy",
            "circuit_observed_at_ms": "0",
        },
    )
    with pytest.raises(LedgerSchemaMismatch):
        await ledger.mutate(command)
    assert await redis_client.exists(keys.balance(command.agent_id)) == 0

    await redis_client.flushdb()
    await redis_client.hset(
        keys.idempotency(command.idempotency_key),
        mapping={
            "schema_version": "1",
            "request_hash": command.request_hash,
            "result_json": "{}",
        },
    )
    with pytest.raises(CorruptLedgerState):
        await ledger.mutate(command)
    assert await redis_client.exists(keys.balance(command.agent_id)) == 0


@pytest.mark.asyncio
async def test_noscript_reload_reuses_command_safely(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    first = mutation(idempotency_key="noscript-first-key-000001")
    await ledger.mutate(first)
    await redis_client.script_flush()

    second = mutation(idempotency_key="noscript-second-key-00001")
    result = await ledger.mutate(second)

    assert result.disposition is MutationDisposition.COMMITTED
    assert result.transaction.outbox_sequence == 3


@pytest.mark.asyncio
async def test_tenants_use_isolated_hash_slots_and_state(redis_client: redis.Redis):
    ledger = RedisEconomyLedger(redis_client)
    left = mutation(tenant_id="tenant-left", idempotency_key="tenant-left-key-000001")
    right = mutation(tenant_id="tenant-right", idempotency_key="tenant-right-key-00001")
    await asyncio.gather(ledger.mutate(left), ledger.mutate(right))

    left_keys = EconomyKeyspace.for_tenant(left.tenant_id)
    right_keys = EconomyKeyspace.for_tenant(right.tenant_id)
    assert left_keys.hash_tag != right_keys.hash_tag
    assert await redis_client.xlen(left_keys.tenant_ledger) == 2
    assert await redis_client.xlen(right_keys.tenant_ledger) == 2


@pytest.mark.asyncio
async def test_compatibility_facade_preserves_legacy_transaction_shape(redis_client: redis.Redis):
    facade = RedisLedgerCompatibilityFacade(RedisEconomyLedger(redis_client))
    transaction = await facade.reward(
        "redis-ledger-tenant",
        "agent-1",
        25,
        "mission_reward",
        idempotency_key=_test_idempotency_key("compatibility-reward"),
        mission_id="mission-1",
        agent_type="commander",
    )

    assert set(transaction) == {
        "id",
        "agent_id",
        "type",
        "amount",
        "resource_type",
        "mission_id",
        "timestamp",
    }
    assert transaction["type"] == "reward"
    assert transaction["amount"] == 25.0
    balance = await facade.get_balance("redis-ledger-tenant", "agent-1")
    assert balance["balance"] == 1025.0
    history = await facade.get_transactions("redis-ledger-tenant")
    assert [record["type"] for record in history] == ["reward", "opening_grant"]


class _UnavailableRedis:
    async def script_load(self, source: str) -> str:
        raise RedisConnectionError("unavailable")


@pytest.mark.asyncio
async def test_transport_failure_never_falls_back_to_memory():
    ledger = RedisEconomyLedger(_UnavailableRedis())
    with pytest.raises(MutationOutcomeUnknown, match="retry the identical idempotency key"):
        await ledger.mutate(mutation())

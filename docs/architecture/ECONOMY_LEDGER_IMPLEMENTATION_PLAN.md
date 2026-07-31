# Economy Ledger Implementation Plan

**Status:** Ready for implementation

**Governing contract:** `docs/architecture/ECONOMY_LEDGER_CONTRACT.md`

**Target:** Slice 2 — Atomic Economy Ledger

**Repository:** `github.com/1devteam/onmiapath_v2`

**Branch:** `recovery/v7.1.5-canonical`

**Date:** 2026-07-30

## Purpose

This plan translates the approved economy-ledger policy into executable
interfaces, storage layouts, failure codes, migrations, caller identities,
tests, and deployment gates. It does not change runtime behavior by itself.

The implementation must remain one cohesive Slice 2 change. It must not add
true credit reservations, transfers, debt-producing adjustments, real-money
value, or synchronous Redis/PostgreSQL consistency.

## Verified Implementation Boundary

The current mutation surface is:

- `ResourceMarketplace.charge()`;
- `ResourceMarketplace.reward()`;
- `ResourceMarketplace.add_tenant_credits()`;
- mission charges and rewards in `MissionExecutor`;
- reservation-like charge/refund and agent-allocation workflows in
  `MissionExecutionSaga` and `AgentCreationSaga`; and
- tests and fixtures that call marketplace mutations directly.

The economy routes expose top-up as the only direct HTTP mutation. Mission
routes, the scheduler, workforce coordination, social-media sagas, and
integration tests reach economy mutations through `MissionExecutor`.

The existing `MissionExecutionSaga` does not hold funds. It immediately charges
the estimated amount, then compensates or settles with later transactions.
Slice 2 preserves and names that behavior accurately. Slice 3 owns real
reservation, settlement, and release semantics.

The supported deployment definitions currently use Redis 7 and PostgreSQL 15.
Production and staging enable Redis AOF. Development compose does not explicitly
enable AOF and must not be presented as durable.

## Code Structure

Implement the slice in these units:

```text
backend/economy/
├── amount.py                 # Decimal-to-microcredit parsing and formatting
├── contracts.py              # Typed commands, results, enums, and errors
├── keyspace.py               # Canonical identifier encoding and Redis keys
├── lua/
│   └── mutate_v1.lua         # Atomic opening grant + requested mutation
├── resource_marketplace.py   # Public compatibility facade
├── redis_ledger.py           # Script loading, execution, reads, circuit breaker
├── archive_models.py         # PostgreSQL archive ORM models
├── archiver.py               # Idempotent Redis-outbox consumer
├── reconciliation.py         # Ledger equation and archive verification
└── migration.py              # Inventory, conversion, cutover, and rollback CLI
```

Do not embed a growing Lua string in `resource_marketplace.py`. Package the
script as a source file, load it with `importlib.resources`, cache its SHA, and
recover from `NOSCRIPT` by loading and retrying once with the same command.

## Public Python Contracts

### Amounts

```python
MICROCREDITS_PER_CREDIT = 1_000_000
MIN_AMOUNT_MICROCREDITS = 1
MAX_INT64 = 9_223_372_036_854_775_807

def parse_credit_amount(value: Decimal | int | str) -> int:
    """Return exact positive microcredits or raise InvalidCreditAmount."""

def format_credit_amount(amount_microcredits: int) -> Decimal:
    """Return an exact six-place credit value for API serialization."""
```

Public mutation APIs must stop accepting `float` as a supported contract.
During caller migration, a compatibility adapter may accept a finite float only
by converting `str(value)` through `Decimal`; it emits a deprecation metric and
is removed before Slice 2 acceptance. Booleans are always rejected.

### Mutation command

```python
@dataclass(frozen=True, slots=True)
class LedgerMutation:
    tenant_id: str
    agent_id: str
    operation: LedgerOperation
    amount_microcredits: int
    resource_type: str
    reason: str
    idempotency_key: str
    mission_id: str | None = None
    agent_type: str = "unknown"
```

`LedgerOperation` initially contains `CHARGE`, `REWARD`, and
`OPENING_GRANT`. Callers cannot directly submit `OPENING_GRANT`; the mutation
script materializes it when the balance does not exist.

### Mutation result

```python
@dataclass(frozen=True, slots=True)
class LedgerMutationResult:
    disposition: MutationDisposition
    transaction: LedgerTransaction
    opening_transaction: LedgerTransaction | None
    balance_microcredits: int
```

`MutationDisposition` is `COMMITTED` or `REPLAYED`. Rejections are typed
exceptions mapped from Lua result codes. Existing `charge()` and `reward()`
return mappings remain compatible at their boundary, including `id`, `type`,
`amount`, `resource_type`, `mission_id`, and `timestamp`.

### Required mutation signatures

```python
async def charge(
    tenant_id: str,
    agent_id: str,
    amount: Decimal | int | str,
    resource_type: str,
    *,
    idempotency_key: str,
    mission_id: str | None = None,
    agent_type: str = "unknown",
    reason: str = "resource_usage",
) -> dict[str, object]: ...

async def reward(
    tenant_id: str,
    agent_id: str,
    amount: Decimal | int | str,
    resource_type: str,
    *,
    idempotency_key: str,
    mission_id: str | None = None,
    agent_type: str = "unknown",
    reason: str = "earned_reward",
) -> dict[str, object]: ...
```

`idempotency_key` is keyword-only and required. There is no random default.

## Canonicalization and Hashing

Identifiers are normalized as follows:

1. require Unicode string input;
2. normalize with NFC;
3. reject leading or trailing whitespace rather than trimming it;
4. require 1–128 UTF-8 bytes for tenant, agent, mission, and workflow IDs;
5. reject ASCII controls, DEL, NUL, `/`, `\`, `{`, and `}`; and
6. preserve case because current identifiers are case-sensitive.

Key components use unpadded base64url of the normalized UTF-8 bytes. This
encoding is reversible for migration diagnostics and prevents delimiter or
Redis Cluster hash-tag injection.

The idempotency storage component is:

```text
hex(SHA-256(normalized idempotency_key UTF-8))
```

The full key is never logged. Logs may include the first 12 hexadecimal hash
characters.

The canonical request hash is SHA-256 over RFC 8785-style canonical JSON with
these exact fields:

```json
{
  "agent_id": "...",
  "amount_microcredits": 1,
  "mission_id": null,
  "operation": "charge",
  "reason": "resource_usage",
  "resource_type": "llm_call",
  "schema_version": 1,
  "tenant_id": "..."
}
```

The idempotency key, transaction ID, timestamps, agent display type, and retry
metadata are excluded. The key selects the retry identity; the request hash
proves that identity was not reused for different economic meaning.

## Redis v2 Keyspace

For encoded tenant token `T` and agent token `A`, every key shares the exact
Redis Cluster hash tag `{econ:T}`:

| Purpose | Key | Type |
| --- | --- | --- |
| Schema and circuit state | `op:econ:v2:{econ:T}:meta` | hash |
| Tenant agent directory | `op:econ:v2:{econ:T}:agents` | set |
| Agent balance | `op:econ:v2:{econ:T}:balance:A` | hash |
| Tenant active ledger | `op:econ:v2:{econ:T}:ledger` | stream |
| Agent active ledger | `op:econ:v2:{econ:T}:agent-ledger:A` | stream |
| Archival outbox | `op:econ:v2:{econ:T}:outbox` | stream |
| Idempotency result | `op:econ:v2:{econ:T}:idem:H` | hash |
| Quarantined agents | `op:econ:v2:{econ:T}:quarantine` | hash |
| Migration lock | `op:econ:v2:{econ:T}:migration-lock` | string |

`H` is the idempotency-key digest. Redis keys contain no raw tenant, agent, or
idempotency values.

### Meta hash

| Field | Value |
| --- | --- |
| `schema_version` | `1` |
| `next_sequence` | next tenant sequence as canonical decimal text |
| `archive_ack_sequence` | greatest contiguously archived sequence |
| `unarchived_count` | exact unacknowledged record count |
| `oldest_unarchived_at_ms` | epoch milliseconds or empty |
| `archive_state` | `healthy`, `warning`, `stopped`, or `corrupt` |
| `memory_state` | `healthy` or `stopped` |
| `circuit_observed_at_ms` | last monitor observation |

Lua derives the effective circuit state from these fields and the configured
threshold arguments. Only the mutation and acknowledgement scripts change
sequence or unarchived-count fields. A bounded monitor updates memory state and
archive recovery hysteresis with a compare-and-set script.

### Balance hash

| Field | Value |
| --- | --- |
| `schema_version` | `1` |
| `agent_id` | canonical identifier |
| `agent_type` | validated type |
| `balance_microcredits` | signed base-10 integer |
| `total_earned_microcredits` | non-negative base-10 integer |
| `total_spent_microcredits` | non-negative base-10 integer |
| `last_sequence` | last tenant outbox sequence applied |
| `last_transaction_id` | last transaction UUID |
| `updated_at` | UTC RFC 3339 with microseconds and `Z` |

All stored integers are parsed strictly. Missing fields, unsupported schema
versions, non-decimal syntax, or broken ledger equations quarantine the agent;
the implementation never supplies defaults over corrupt persisted state.

### Streams

The tenant ledger, agent ledger, and outbox use Redis Stream IDs generated by
Redis. Each entry contains:

```text
sequence
transaction_id
record_json
```

`sequence` is a tenant-monotonic signed 64-bit integer held in `meta` and
incremented by Lua. `record_json` is identical canonical JSON in all three
streams. The PostgreSQL archiver computes and verifies `record_sha256` from
those immutable bytes before insertion.

The opening grant and requested mutation are separate records. On first
mutation the script assigns consecutive sequences, appends both records to all
applicable streams, and returns both. Reads of an unknown agent remain virtual
and write nothing.

The opening record uses the internal stable identity
`opening-grant:{agent_id}:v1`, not the triggering request's idempotency
identity. Balance absence plus the atomic script is its immediate uniqueness
guard; its hashed identity is archived so eager and lazy initialization can be
reconciled to the same single grant.

Streams are not capped with `MAXLEN` in Slice 2. Trimming is a later,
archive-verified maintenance operation and is disabled at initial release.

### Idempotency hash

| Field | Value |
| --- | --- |
| `schema_version` | `1` |
| `request_hash` | lowercase SHA-256 hex |
| `transaction_id` | requested mutation UUID |
| `transaction_sequence` | requested mutation sequence |
| `opening_transaction_id` | UUID or empty |
| `result_json` | canonical replay result |
| `created_at` | server UTC timestamp |

No TTL is set. Slice 2 does not delete Redis idempotency records, even after
PostgreSQL archival. A later compaction design must prove replay safety before
changing this rule.

## Lua Mutation ABI

`mutate_v1.lua` receives only prevalidated keys in `KEYS`:

```text
1 meta
2 agents
3 balance
4 tenant ledger
5 agent ledger
6 outbox
7 idempotency
8 quarantine
9 migration lock
```

`ARGV` contains fixed-position ASCII or canonical JSON values:

```text
1 schema_version
2 operation
3 amount_microcredits
4 agent_id
5 agent_type
6 request_hash
7 transaction_id
8 transaction_created_at
9 transaction_record_json_prefix
10 transaction_record_json_suffix
11 opening_transaction_id
12 opening_created_at
13 opening_record_json_prefix
14 opening_record_json_suffix
15 opening_grant_microcredits
16 archive_hard_limit_records
17 archive_hard_limit_age_ms
18 now_epoch_ms
```

Each JSON prefix ends immediately before the decimal `outbox_sequence` value,
and its suffix begins immediately after that value. Lua concatenates prefix,
checked integer sequence, and suffix. This avoids `cjson` and Lua-number
serialization, which cannot preserve every signed 64-bit integer. All other
record fields and both fragments are canonicalized by the application and
validated before script execution.

Lua must not use `tonumber` for balances, amounts, totals, or sequences.
`mutate_v1.lua` implements bounded signed-decimal string validation,
comparison, addition, and subtraction, including explicit `INT64_MIN` and
`INT64_MAX` checks. Redis hashes store the resulting canonical decimal strings.
This is necessary because Redis Lua numeric values cannot exactly represent the
entire signed 64-bit range.

The application verifies every `KEYS` value contains the same hash tag before
execution. Lua repeats defensive checks for schema, strict integer syntax,
overflow, quarantine, migration lock, archive circuit state, idempotency, and
overdraft before its first write.

### Stable return codes

The script returns an array; position 1 is one of:

| Code | Meaning | Mutation |
| --- | --- | --- |
| `COMMITTED` | New mutation committed | Yes |
| `REPLAYED` | Same key and request; stored result returned | No |
| `IDEMPOTENCY_CONFLICT` | Same key, different request | No |
| `INSUFFICIENT_FUNDS` | Charge would overdraw | No |
| `ARCHIVE_LAG_LIMIT` | Outbox hard limit crossed | No |
| `MIGRATION_LOCKED` | Tenant cutover lock is active | No |
| `ACCOUNT_QUARANTINED` | Agent is quarantined | No |
| `CORRUPT_STATE` | Stored state failed validation | No; quarantine only |
| `INTEGER_OVERFLOW` | Delta, balance, total, or sequence overflow | No |
| `SCHEMA_MISMATCH` | Unsupported persisted schema | No |
| `INVALID_ARGUMENT` | Defensive Lua validation failed | No |

For `COMMITTED` and `REPLAYED`, position 2 is canonical result JSON. For a
rejection, position 2 is a bounded machine-readable reason and position 3 may
contain a redacted correlation value. Raw stored data is never returned through
the API.

Lua runtime errors, connection loss, and timeouts are not converted into a
definite rejection. They produce `MutationOutcomeUnknown`; the caller retries
the identical command and idempotency key.

### Atomic execution order

1. Reject mismatched schema, migration lock, quarantine, or archive hard limit.
2. Read the idempotency hash.
3. Return `REPLAYED` or `IDEMPOTENCY_CONFLICT` before reading mutable balance.
4. Parse and validate existing balance state, if present.
5. Calculate the optional opening grant and requested mutation using checked
   integer arithmetic.
6. Reject overdraft or overflow before the first write.
7. Allocate one sequence per record.
8. Write balance and tenant agent directory.
9. Append immutable records to tenant ledger, agent ledger, and outbox.
10. increment `unarchived_count` and set `oldest_unarchived_at_ms` when the
    prior count was zero.
11. Write the idempotency replay result.
12. Return the requested transaction plus optional opening transaction.

Redis Lua execution is atomic. An explicit script error before completion rolls
back no individual Redis command, so every error-prone conversion and invariant
check must occur before the first write. Commands after the first write must use
prevalidated arguments and compatible key types. Integration tests deliberately
inject wrong key types to confirm the preflight rejects them before mutation.

## PostgreSQL Archive Schema

Add a dedicated Alembic revision and ORM models. The canonical table is:

```sql
CREATE TABLE economy_ledger_archive (
    archive_id BIGSERIAL PRIMARY KEY,
    transaction_id UUID NOT NULL UNIQUE,
    tenant_id VARCHAR(128) NOT NULL,
    agent_id VARCHAR(128) NOT NULL,
    operation VARCHAR(32) NOT NULL,
    amount_microcredits BIGINT NOT NULL CHECK (amount_microcredits > 0),
    delta_microcredits BIGINT NOT NULL,
    balance_before_microcredits BIGINT NOT NULL,
    balance_after_microcredits BIGINT NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    reason VARCHAR(128) NOT NULL,
    mission_id VARCHAR(128),
    idempotency_key_hash CHAR(64) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    outbox_sequence BIGINT NOT NULL CHECK (outbox_sequence > 0),
    created_at TIMESTAMPTZ NOT NULL,
    schema_version SMALLINT NOT NULL CHECK (schema_version = 1),
    record_json JSONB NOT NULL,
    record_sha256 CHAR(64) NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_id, outbox_sequence),
    UNIQUE (tenant_id, idempotency_key_hash)
);
```

Required indexes:

```text
(tenant_id, created_at DESC, outbox_sequence DESC)
(tenant_id, agent_id, created_at DESC, outbox_sequence DESC)
(tenant_id, mission_id, created_at DESC) WHERE mission_id IS NOT NULL
```

The archive stores only the idempotency-key hash, never the raw key. A trigger
rejects `UPDATE` and `DELETE` on `economy_ledger_archive`. The runtime archive
role receives only `SELECT` and `INSERT` on that table, plus `SELECT`, `INSERT`,
and `UPDATE` on its checkpoint table; it receives no `DELETE` or `TRUNCATE`.
Migration ownership remains a separate role.

Add `economy_archive_checkpoint`:

```sql
CREATE TABLE economy_archive_checkpoint (
    tenant_id VARCHAR(128) PRIMARY KEY,
    last_outbox_stream_id VARCHAR(64) NOT NULL,
    last_outbox_sequence BIGINT NOT NULL,
    last_record_sha256 CHAR(64) NOT NULL,
    archived_count BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

Checkpoint advancement and archive inserts occur in one PostgreSQL transaction.
The checkpoint may advance across rows already present only after their hashes,
tenant IDs, and sequences match.

Add `economy_topup_operation` for resumable group distribution:

```sql
CREATE TABLE economy_topup_operation (
    topup_id UUID PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL,
    idempotency_key_hash CHAR(64) NOT NULL,
    request_hash CHAR(64) NOT NULL,
    amount_microcredits BIGINT NOT NULL CHECK (amount_microcredits > 0),
    allocation_json JSONB NOT NULL,
    target_count INTEGER NOT NULL CHECK (target_count > 0),
    completed_count INTEGER NOT NULL DEFAULT 0
        CHECK (completed_count >= 0 AND completed_count <= target_count),
    state VARCHAR(16) NOT NULL
        CHECK (state IN ('pending', 'running', 'completed', 'failed')),
    created_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, idempotency_key_hash)
);
```

`allocation_json` is an immutable, canonically ordered map of agent IDs to
microcredits. Child completion is proven from archived ledger transaction IDs,
not trusted from `completed_count` alone.

## Archiver Contract

One logical consumer group, `economy-archive-v1`, consumes each tenant outbox.
Multiple workers may claim abandoned pending entries. Processing is bounded by
both record count and serialized byte size.

For each batch:

1. read entries in ascending stream order;
2. validate canonical JSON, checksum, tenant, sequence, and schema;
3. insert with `ON CONFLICT (transaction_id) DO NOTHING`;
4. on conflict, fetch and compare every immutable field;
5. update the checkpoint in the same PostgreSQL transaction;
6. commit PostgreSQL;
7. call an atomic Redis acknowledgement script that advances the acknowledged
   sequence, decrements `unarchived_count` exactly once, updates or clears
   `oldest_unarchived_at_ms`, and acknowledges stream entries; and
8. update lag metrics.

A conflicting duplicate is corruption, not success. It stops that tenant's
archiver, quarantines further mutations through `meta`, and alerts.

Acknowledgement failure after PostgreSQL commit is safe: redelivery verifies the
existing row, advances or confirms the checkpoint, and acknowledges again.
PostgreSQL failure leaves entries pending and unacknowledged.

Archival for one tenant is sequence-ordered. The acknowledgement script accepts
the prior and new sequence high-water marks and changes lag counters only when
the stored mark equals the prior mark. A repeated acknowledgement is therefore
a no-op. `unarchived_count` and `oldest_unarchived_at_ms`, not `XLEN`, drive
lag thresholds because acknowledged entries remain in the untrimmed stream.
The script finds the next oldest unarchived timestamp from the first entry after
the new high-water mark.

### Initial operational limits

These defaults are configuration values and must be validated by load testing:

| State | Record lag | Oldest age | Behavior |
| --- | ---: | ---: | --- |
| Healthy | `< 10,000` | `< 5 minutes` | Mutations enabled |
| Warning | `>= 10,000` | `>= 5 minutes` | Alert; mutations enabled |
| Hard stop | `>= 100,000` | `>= 15 minutes` | New production mutations rejected |

Either threshold activates its state. Recovery requires both values below the
warning thresholds for two consecutive one-minute observations, preventing
rapid enable/disable oscillation.

The hard-stop decision must use Redis-local outbox metadata maintained by the
archiver and read by Lua. It cannot depend on a PostgreSQL or metrics network
call inside mutation execution. Reads and archiver recovery remain enabled.

Also reject new production mutations when Redis reports at least 85% of its
configured `maxmemory`, unless an approved capacity override is active.
Production must configure a finite `maxmemory` and a no-eviction policy for the
ledger Redis deployment; shared cache eviction is not acceptable.

## Caller Idempotency Matrix

Keys below are logical forms. The storage key hashes them before Redis use.
Every ordinal comes from persisted workflow input or deterministic list order,
never process-local timing or a new retry-time UUID.

| Caller | Economic event | Stable idempotency identity |
| --- | --- | --- |
| Specialized mission agent | base invocation charge | `mission:{mission_id}:specialized:{agent_id}:base:v1` |
| Specialized mission agent | reasoning/tool charge | `mission:{mission_id}:specialized:{agent_id}:usage:v1` |
| Standard mission step | per-step charge | `mission:{mission_id}:standard-step:{step_index}:agent:{agent_id}:v1` |
| Swarm mission step | per-step charge | `mission:{mission_id}:swarm-step:{step_index}:agent:{agent_id}:v1` |
| Mission rewards | per participant occurrence | `mission:{mission_id}:reward:{participant_index}:agent:{agent_id}:v1` |
| Mission saga | estimated-cost charge | `saga:{saga_id}:step:reserve_credits:charge:v1` |
| Mission saga | reservation compensation | `saga:{saga_id}:step:reserve_credits:compensate:v1` |
| Mission saga | settlement refund | `saga:{saga_id}:step:deduct_cost:refund:v1` |
| Mission saga | settlement overage | `saga:{saga_id}:step:deduct_cost:overage:v1` |
| Mission saga | settlement compensation | `saga:{saga_id}:step:deduct_cost:compensate:v1` |
| Agent creation saga | initial allocation | `saga:{saga_id}:step:allocate_initial_budget:reward:v1` |
| Agent creation saga | allocation revocation | `saga:{saga_id}:step:allocate_initial_budget:compensate:v1` |
| Tenant top-up | per-agent reward | `tenant-topup:{topup_id}:agent:{agent_id}:v1` |
| Test/direct internal call | explicit test/workflow operation | Caller supplies a deterministic fixture or workflow identity |

Current swarm agent IDs are random inside execution. Before ledger cutover,
derive them from `{mission_id, step_index}` so a retry reaches the same account
and idempotency identity.

Repeated agent names in `agents_used` are economically distinct participant
occurrences. The participant index is therefore included. Rewards are computed
in microcredits; deterministic remainder microcredits go to ascending
`(agent_id, participant_index)` order.

### HTTP top-up

The top-up endpoint must accept an `Idempotency-Key` header and create a
persisted top-up operation ID before distributing child rewards. The parent
record contains tenant, amount, canonical target-agent snapshot, allocation
map, state, creator, and timestamps.

The endpoint returns:

- `201` when the distribution completes on first execution;
- `200` when a completed operation is replayed;
- `202` when a prior partial operation is safely resuming;
- `409` when the key is reused with different tenant-scoped request content;
- `422` for invalid amount; and
- `503` for definite Redis unavailability or active archive circuit breaker.

Connection loss with unknown outcome returns `503` with a retry-safe error code;
the client must resend the same header.

## Read Model and API Compatibility

`get_balance()` returns a virtual opening balance for an absent agent without
writing. `get_tenant_balances()` lists materialized agents from the tenant agent
set; it must not use Redis `KEYS`.

Tenant and agent transaction reads use their corresponding streams with
`XREVRANGE`, giving deterministic newest-first ordering. Pagination uses an
opaque cursor containing stream ID and schema version. Existing offset
parameters remain temporarily supported by bounded traversal, emit a
deprecation metric, and reject offsets beyond a configured maximum.

API credit amounts are serialized from `Decimal`, then converted only at the
legacy response boundary required by current Pydantic models. Internal
calculation, storage, reconciliation, and archive logic never use floats.

Tenant statistics must move away from scanning a fixed 10,000-record window.
Slice 2 maintains exact tenant and per-agent cumulative totals in balance state
and derives daily/mission aggregates from the PostgreSQL archive. Until the
archive catches up, responses include only fields whose exactness is known;
they do not silently label partial active-window values as complete.

## Configuration

Add validated settings:

```text
ECONOMY_LEDGER_MODE=legacy|shadow|v2
ECONOMY_ALLOW_NON_DURABLE_FALLBACK=false
ECONOMY_OPENING_GRANT_MICROCREDITS=1000000000
ECONOMY_ARCHIVER_ENABLED=true
ECONOMY_ARCHIVER_BATCH_RECORDS=500
ECONOMY_ARCHIVER_BATCH_BYTES=4194304
ECONOMY_ARCHIVE_WARN_RECORDS=10000
ECONOMY_ARCHIVE_WARN_AGE_SECONDS=300
ECONOMY_ARCHIVE_HARD_RECORDS=100000
ECONOMY_ARCHIVE_HARD_AGE_SECONDS=900
ECONOMY_REDIS_MEMORY_HARD_PERCENT=85
ECONOMY_LEGACY_ROLLBACK_HOURS=72
```

Production validation requires `v2`, disabled fallback, enabled archiver,
`rediss://` when the deployment supports TLS, finite Redis `maxmemory`,
`maxmemory-policy noeviction`, and AOF. Shadow and non-durable fallback modes
are forbidden in production.

## Migration Plan

Use tenant-batched offline conversion with shadow verification. Do not dual
write legacy and v2 ledgers; two independent mutations cannot be made atomic.

### Phase 0 — Build with no behavior change

- Add types, parser, key builder, Lua runner, archive schema, archiver,
  reconciliation, and migration tooling.
- Keep `ECONOMY_LEDGER_MODE=legacy`.
- Add unit, live Redis, PostgreSQL, and failure-injection tests.

### Phase 1 — Inventory and quarantine

- Use `SCAN`, never `KEYS`, to inventory all legacy
  `economy:{tenant}:balance:{agent}` and `economy:{tenant}:txns` keys.
- Record key type, cardinality, encoding validity, value checksum, and Redis
  memory use in a signed migration manifest.
- Parse every balance and transaction exactly through `Decimal`.
- Reconstruct each agent ledger in chronological order and verify current
  balance, earned, and spent totals.
- Quarantine non-finite, over-precision, malformed, truncated, or
  irreconcilable histories. Do not synthesize repairs.

Legacy lists may already have lost records because they were trimmed to 10,000.
A balance whose complete equation cannot be proven is quarantined even when its
current numeric value parses.

### Phase 2 — PostgreSQL archive preparation

- Apply the Alembic revision.
- Verify append-only trigger and least-privilege grants.
- Import verified legacy transactions with deterministic UUIDv5 transaction
  IDs derived from tenant, legacy record checksum, and chronological ordinal.
- Mark imported records with `reason=legacy_import` in record metadata while
  preserving their economic operation.
- Import an explicit opening grant only when the full ledger proves it.
- Verify per-tenant count, checksum chain, balance equation, and restore.

### Phase 3 — Tenant cutover

For each tenant:

1. acquire a fenced migration lock with owner token and expiry;
2. reject mutations while allowing reads;
3. re-inventory legacy state and compare the Phase 1 manifest;
4. write v2 balances, streams, sequence, archive checkpoint, and idempotency
   seeds using a resumable migration journal;
5. verify v2 Redis against legacy and PostgreSQL;
6. enable shadow reads and compare every read result;
7. switch that tenant to v2 only after zero mismatches; and
8. release the fenced lock.

The lock release script compares the owner token. An expired lock cannot be
blindly deleted by another migration process.

### Phase 4 — Caller cutover

- Add persisted step/call ordinals where missing.
- Make idempotency keys required at every caller.
- Replace random swarm identity with deterministic identity.
- Change top-up into a resumable parent/child workflow.
- Remove float arithmetic from mutation and reward distribution paths.
- Run compatibility tests for routes, scheduler, workforce, missions, and
  sagas.

### Phase 5 — Observation

- Keep legacy keys read-only for 72 hours.
- Reconcile Redis v2, outbox, PostgreSQL, and API reads continuously.
- Require zero unexplained mismatches, zero corrupt duplicates, healthy archive
  lag, and a successful restore drill.
- After owner approval, remove legacy-read code in a separate commit. Legacy
  data deletion is a separate destructive operation and is not authorized by
  this plan.

## Rollback and Forward Repair

Before a tenant's first v2 mutation, rollback may switch reads to untouched
legacy keys.

After any v2-only mutation, legacy state is stale. Rollback must not point
writes or authoritative reads back to legacy. The safe response is:

1. disable mutations for the tenant;
2. keep v2 reads available if verified;
3. repair the v2 path or restore Redis from the verified AOF/backup;
4. replay PostgreSQL archive records into a clean v2 keyspace;
5. reconcile balances and checksums; and
6. re-enable writes only after verification.

Deployment rollback and data rollback are separate decisions. Reverting code
without proving data compatibility is forbidden.

## Test and Failure-Injection Plan

### Pure unit tests

- every accepted and rejected amount syntax;
- identifier normalization, encoding, collision resistance, and hash-tag
  injection;
- canonical request hashes and idempotency-key redaction;
- exact top-up/reward remainders;
- Lua result-to-exception mapping;
- API legacy serialization.

### Redis 7 integration

- opening grant plus mutation creates two complete records atomically;
- hundreds of concurrent charges cannot overdraw;
- same-key concurrency produces one commit and stable replays;
- different-payload reuse conflicts;
- wrong key types, corrupt hashes, overflow, and unsupported schema versions
  produce no partial write;
- connection loss before, during, and after `EVALSHA` is retry-safe;
- `NOSCRIPT` reload is safe;
- tenant and agent streams match the outbox checksums;
- migration lock and archive circuit breaker reject without mutation;
- Redis restart with configured AOF preserves committed results.

### PostgreSQL 15 and archiver integration

- schema constraints and append-only trigger reject mutation/deletion;
- redelivery creates no duplicate rows;
- conflicting transaction IDs stop archival and quarantine the tenant;
- checkpoint and rows commit together;
- acknowledgement failure after commit recovers safely;
- PostgreSQL outage accumulates outbox entries;
- lag warning, hard stop, and hysteresis behave exactly;
- archive replay reconstructs Redis balances and ordering.

### Caller and full-system tests

- every row in the caller matrix has a deterministic identity test;
- mission, scheduler, workforce, and saga retries do not duplicate movement;
- repeated participant names receive the intended number of rewards;
- top-up interruption resumes without double rewarding;
- production Redis failure never mutates fallback memory;
- non-durable fallback is explicit and labeled in development;
- existing economy response fields remain compatible;
- full test suite, lint, type checks, security scans, production container build,
  production-style smoke, backup, and restore all pass.

## Observability and Runbooks

Metrics use operation, result, environment, and bounded tenant class labels.
Tenant, agent, mission, transaction, and idempotency identifiers belong in
structured logs and traces, not metric labels.

Required alerts:

- archive warning and hard-stop thresholds;
- oldest pending outbox age;
- corrupt duplicate or checksum mismatch;
- quarantined agent or tenant;
- Redis AOF write failure;
- Redis memory hard threshold;
- unknown mutation outcomes;
- reconciliation mismatch; and
- archiver checkpoint stalled while outbox grows.

Runbooks must cover:

- retrying unknown outcomes;
- PostgreSQL outage and archiver recovery;
- circuit-breaker recovery;
- quarantine investigation;
- Redis AOF restore;
- PostgreSQL archive-to-Redis rebuild;
- migration lock recovery; and
- checksum/count/tenant-boundary verification before any retention action.

## Implementation Commit Sequence

Each commit must leave the branch testable:

1. contracts, exact amount parser, keyspace, and unit tests;
2. Lua script, Redis adapter, compatibility facade, and live Redis tests;
3. PostgreSQL models, Alembic migration, archiver, and integration tests;
4. migration/reconciliation tooling and recovery tests;
5. mission, saga, top-up, scheduler, and workforce caller migration;
6. configuration, metrics, alerts, runbooks, and production smoke;
7. final Slice 2 reconciliation report and acceptance evidence.

Do not combine untested behavioral patches merely to reduce commit count. Do
not push a commit whose required local gate is failing.

Commit 1 status: **Complete on 2026-07-30.** The recovery branch now contains:

- exact, context-independent `Decimal`/integer/text conversion to signed 64-bit
  microcredits;
- immutable mutation, transaction, result, operation, disposition, and Lua
  rejection contracts;
- canonical NFC identifier and idempotency handling;
- unpadded base64url key components, SHA-256 retry identities, and redacted log
  references;
- guarded tenant keyspace construction and the exact nine-key mutation ABI; and
- focused unit coverage for numeric boundaries, unsupported syntax, Unicode,
  collision and injection attempts, request hashing, transaction equations,
  immutability, UTC timestamps, and typed Lua error mapping.

This commit does not connect the new primitives to `ResourceMarketplace` and
therefore does not change live economy reads or mutations.

## Implementation Acceptance Checklist

- [ ] Exact integer amounts are used from API boundary through archive.
- [ ] Every mutation caller supplies a stable idempotency identity.
- [ ] Lua preflight can reject all known failures before its first write.
- [ ] Opening grant, requested mutation, all indexes, outbox, and idempotency
      commit atomically.
- [ ] Redis outage cannot fall through to production memory mutation.
- [ ] Archive rows are tenant-scoped, append-only, checksummed, and replayable.
- [ ] Outbox redelivery and PostgreSQL acknowledgement failure are harmless.
- [ ] Lag and memory circuit breakers fail closed and recover with hysteresis.
- [ ] Legacy conversion quarantines incomplete or irreconcilable histories.
- [ ] Top-up is exactly distributed and resumable.
- [ ] Mission and saga retries cannot duplicate charges, rewards, or refunds.
- [ ] Slice 2 does not claim true reservation semantics.
- [ ] Rollback never discards v2-only economic movement.
- [ ] Redis 7, PostgreSQL 15, full suite, security, container, smoke, backup, and
      restore gates pass.

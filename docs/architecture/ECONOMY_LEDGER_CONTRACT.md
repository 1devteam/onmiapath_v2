# Economy Ledger Contract

**Status:** Approved by Obex Blackvault

**Target:** Slice 2 — Atomic Economy Ledger

**Repository:** `github.com/1devteam/onmiapath_v2`

**Branch:** `recovery/v7.1.5-canonical`

**Date:** 2026-07-30

## Purpose

This contract defines the correctness boundary for agent credit mutations before
ledger implementation begins. It covers balances, overdraft behavior,
idempotency, transaction retention, atomicity, failure handling, tenant
isolation, compatibility, observability, and migration.

Credits are internal operating units. They are not fiat currency, cryptocurrency,
or a stored-value payment instrument. A future change to that classification
requires a separate financial, legal, accounting, and security review.

## Verified Current State

The recovery branch currently has three economic state representations:

1. `ResourceMarketplace` stores operational balances in Redis hashes and
   transaction records in a tenant Redis list, with an in-process fallback.
2. CQRS and event-sourcing code can derive balances from PostgreSQL economy
   events, but marketplace `charge()` and `reward()` do not write those events.
3. The SQLAlchemy `Agent.credit_balance` column is written during agent CRUD but
   is not synchronized by marketplace mutations.

The Slice 2 implementation must not present these three stores as strongly
consistent. Until a later reconciliation explicitly replaces that boundary:

- The Redis marketplace ledger is the authority for economy API balances and
  marketplace mutations.
- PostgreSQL CQRS economy events are an eventual audit/read-model integration,
  not part of the Redis atomic commit.
- `Agent.credit_balance` is a legacy agent snapshot and must not authorize or
  reject marketplace spending.

Current failure modes that this contract closes:

- Balance mutation and transaction append are separate Redis operations.
- Redis failures can fall through to unrelated in-memory state after a partial
  remote write.
- Charges accept negative, zero, non-finite, and overdrawing amounts.
- Rewards accept negative, zero, and non-finite amounts.
- Reads can create starting balances without an explicit ledger entry.
- Retry requests have no idempotency identity.
- The transaction list is trimmed at 10,000 records whether or not records were
  archived.
- Agent-filtered reads scan the entire retained tenant list.

## Approved Owner Decisions

Obex Blackvault approved these policies on 2026-07-30.

| Decision | Proposed contract | Rationale |
| --- | --- | --- |
| Numeric representation | Signed 64-bit integer microcredits; `1 credit = 1,000,000 microcredits` | Avoids floating-point drift while preserving six decimal places. |
| Normal overdraft | Disabled; a charge that would make the available balance negative is rejected atomically | Prevents unbounded cost and race-based overspend. |
| Administrative debt | Not part of normal `charge()`; only a separately authorized adjustment command may create debt | Keeps operational spending fail-closed and makes exceptions auditable. |
| Idempotency requirement | Required for every mutation, including opening grants, charges, rewards, top-ups, transfers, refunds, and adjustments | Makes retries safe and prevents duplicate credit movement. |
| Idempotency retention | Retain at least as long as the corresponding ledger record; never expire independently | An expired key must not permit replay while its transaction remains authoritative. |
| Ledger retention | No destructive trim until a verified archive contains the record; archived records retained indefinitely by default | Audit history must not disappear because a Redis list reached a size cap. |
| Archive target | Append-only PostgreSQL economy archive populated through an atomic Redis archival outbox | Uses the durable primary database without pretending Redis and PostgreSQL share one transaction. |
| Redis outage in production | Fail closed; do not mutate in-memory fallback state | Avoids split-brain balances and duplicate mutations after recovery. |
| Development fallback | Allowed only when explicitly configured; clearly marked non-durable and isolated from Redis-backed state | Preserves local testability without disguising fallback as production persistence. |

## Core Invariants

Every successful mutation must satisfy all invariants:

1. `tenant_id` and `agent_id` are non-empty canonical identifiers.
2. The mutation amount is finite, strictly positive, and representable as an
   integer number of microcredits.
3. Tenant, agent, balance, ledger, and idempotency keys cannot collide with
   another tenant.
4. The balance delta, earned/spent totals, ledger record, archival outbox entry,
   and idempotency result commit in one Redis Lua execution.
5. A successful balance change cannot exist without exactly one ledger record.
6. A ledger record cannot exist without its corresponding balance change,
   except a zero-delta informational event type explicitly added by a future
   contract.
7. The post-mutation balance recorded in the ledger equals the balance stored in
   the balance hash.
8. Normal charges never produce a balance below zero.
9. A rejected mutation changes no balance, totals, ledger, idempotency, or
   metrics state.
10. A repeated idempotency key with the same canonical request returns the
    original result and creates no new mutation.
11. A repeated idempotency key with different request content returns a
    conflict and creates no mutation.
12. Reads never create balances or ledger records.
13. Metrics are emitted only after a committed result and are not part of the
    correctness decision.
14. Timestamps are UTC and transaction ordering has a deterministic tie-breaker.

## Balance Contract

### Opening balance

- The default opening grant remains `1000` credits for compatibility.
- An agent with no stored balance may be reported as having a virtual opening
  balance, but the read must not write state.
- The first mutation creates the balance and an explicit `opening_grant`
  ledger record in the same atomic operation before applying the requested
  mutation.
- Agent creation may eagerly issue the same opening grant when it supplies a
  stable idempotency key. Eager and lazy initialization must converge to one
  opening grant.
- `total_earned` includes the opening grant. `total_spent` starts at zero.

### Available balance

For Slice 2, there are no pending reservations:

```text
available_balance = posted_balance
```

Slice 3 may add reservations. It must extend this contract rather than
reinterpreting `posted_balance`.

### Numeric boundaries

- External numeric input is parsed through decimal text, never binary-float
  arithmetic.
- More than six fractional credit digits is rejected rather than rounded.
- Zero, negative, NaN, infinity, booleans, and numeric strings with unsupported
  syntax are rejected.
- Every delta and resulting balance must fit signed 64-bit microcredits.
- API responses retain existing credit-denominated fields. Internal
  microcredits are not exposed unless a versioned API explicitly adds them.

## Mutation Command Contract

Every mutation has this canonical request identity:

```text
tenant_id
agent_id
operation
amount_microcredits
resource_type or reason
mission_id (nullable)
idempotency_key
```

The canonical request hash includes every field that changes economic meaning.
Caller-supplied timestamps and transaction identifiers are not accepted.

### Charge

- Applies a negative delta.
- Requires sufficient available balance.
- Returns `insufficient_funds` without mutation when the balance is too low.
- Records `total_spent += amount`.

### Reward

- Applies a positive delta.
- Records `total_earned += amount`.
- A governance penalty must not be represented as a negative reward; it is a
  charge or authorized adjustment with its own reason.

### Tenant top-up

- A top-up is a group operation composed of one idempotent reward per target
  agent.
- Slice 2 does not claim all-agent atomicity. The group operation needs a stable
  parent operation ID and deterministic child idempotency keys so interrupted
  distribution can safely resume.
- The distributed microcredit total must equal the requested top-up exactly.
  Integer division remainder is assigned deterministically by canonical
  `agent_id` order.
- If the tenant has no agents, the existing default-agent behavior remains, but
  its opening grant and top-up are separate ledger records.

### Transfer and administrative adjustment

The current public marketplace has no transfer implementation even though older
specification and CQRS code mention one. Slice 2 must not expose transfer or
debt-producing adjustment behavior without:

- authorization rules;
- paired debit/credit atomicity;
- deterministic idempotency;
- explicit reason and actor identity;
- tests for tenant isolation, insufficient funds, replay, and partial failure.

## Transaction Record Contract

Each committed ledger record contains:

| Field | Contract |
| --- | --- |
| `transaction_id` | Server-generated immutable UUID |
| `tenant_id` | Canonical tenant identity |
| `agent_id` | Canonical agent identity |
| `operation` | Versioned enum such as `opening_grant`, `charge`, or `reward` |
| `amount_microcredits` | Positive integer magnitude |
| `delta_microcredits` | Signed applied delta |
| `balance_before_microcredits` | Posted balance before the mutation |
| `balance_after_microcredits` | Posted balance after the mutation |
| `resource_type` | Validated resource/reward category |
| `reason` | Validated machine-readable reason |
| `mission_id` | Optional canonical mission identity |
| `idempotency_key` | Caller-supplied stable retry identity |
| `request_hash` | Hash of the canonical economic request |
| `outbox_sequence` | Monotonic tenant archival sequence assigned atomically |
| `created_at` | Server-generated UTC timestamp |
| `schema_version` | Ledger schema version, starting at `1` |

Transaction records are immutable. Corrections use compensating transactions;
records are never edited in place.

## Idempotency Contract

- Keys are scoped to a tenant and mutation namespace.
- Keys must be opaque, 16–128 UTF-8 characters, and must not contain secrets.
- The atomic script checks the stored request hash before any mutation.
- Same key and same request hash returns the original transaction and balance.
- Same key and different request hash returns an idempotency conflict.
- Concurrent identical requests have one winner and identical replay results.
- A timeout after submission is an unknown outcome; clients retry with the same
  key and never generate a replacement key for the same intended mutation.
- Internal callers derive keys from stable workflow identities, for example:

```text
mission:{mission_id}:attempt:{attempt_id}:llm:{call_id}:charge
mission:{mission_id}:completion:{outcome_id}:reward
tenant-topup:{topup_id}:agent:{agent_id}
saga:{saga_id}:step:{step_name}:compensation
```

## Atomic Redis Contract

The Lua mutation executes on keys that share one Redis Cluster hash tag derived
from an encoded tenant identity. The script:

1. validates the idempotency record;
2. loads or materializes the opening balance;
3. validates amount, overflow, and overdraft constraints;
4. computes before/after values and totals;
5. writes the balance hash;
6. appends the immutable transaction record;
7. appends the same immutable record to the tenant archival outbox;
8. writes the replayable idempotency result; and
9. returns the committed transaction and balance.

No network call, PostgreSQL write, NATS publish, or metrics emission occurs
inside this atomic boundary.

The outbox entry is part of the correctness boundary. A mutation is not
committed if its outbox entry cannot be written atomically with the balance and
ledger record.

Existing keys must not be silently reinterpreted. Implementation requires a
versioned key schema plus one of:

- an offline migration with verified rollback; or
- dual-read/single-write migration with reconciliation metrics.

The exact key encoding and migration choice belong in the implementation plan
after policy approval.

## Failure Contract

| Condition | Result |
| --- | --- |
| Invalid amount or identifier | Reject before Redis; no mutation |
| Insufficient funds | Atomic rejection; no mutation |
| Idempotency conflict | Atomic conflict; no mutation |
| Redis unavailable before submission | Service unavailable; no fallback mutation in production |
| Connection lost during submission | Unknown outcome; retry the same idempotency key |
| Metrics or NATS unavailable after commit | Return committed result; queue/retry secondary publication and alert |
| PostgreSQL archiver unavailable after commit | Preserve the Redis outbox entry, retry idempotently, measure lag, and apply the archive-lag circuit breaker |
| Corrupt balance or ledger data | Quarantine the account, reject mutation, and alert; never guess a repair |
| Development fallback enabled | Use one process-local atomic lock and identify responses/logs as non-durable |

Compensation is a new idempotent transaction. It never deletes the original
charge or reward.

## Retention and Archival Contract

- The active ledger is not trimmed merely because it reaches a count threshold.
- The same atomic script that commits a mutation appends its immutable record to
  a tenant-scoped Redis archival outbox.
- An idempotent archiver copies outbox records to an append-only PostgreSQL
  economy archive using `transaction_id` as a uniqueness boundary.
- PostgreSQL archive records include canonical `tenant_id`; existing CQRS
  economy events that omit tenant identity do not satisfy this archive contract.
- Outbox acknowledgement occurs only after the PostgreSQL transaction commits.
- Redelivery of an acknowledged or partially processed record is harmless.
- Archival operates on immutable transaction records and records a verified
  high-water mark.
- A record is eligible for active-store removal only after archive durability,
  checksum, count, restore, and tenant-boundary verification pass.
- Idempotency records remain replay-safe for every retained or archived
  transaction.
- Aggregate statistics must not depend only on the active retention window.
- Retention is tenant-aware and legal-hold capable.
- Archive failure stops deletion and raises an operational alert.
- The implementation plan must define a bounded archive-lag threshold in both
  record count and elapsed time. Crossing either threshold disables new
  production mutations while reads and archiver recovery remain available.

PostgreSQL archive retention is indefinite by default. A future deletion
schedule requires an approved data-retention policy, legal-hold behavior, and a
verified restore test before it can replace this default.

## API Compatibility Contract

- Existing `GET /api/v1/economy/balance`, `/transactions`, and `/stats` response
  fields remain compatible.
- Existing `charge()` and `reward()` return transaction mappings.
- Mutation callers must migrate to provide idempotency keys; a temporary adapter
  may derive keys only from stable workflow identities, never random retry-time
  values.
- Pagination remains deterministic and newest-first at the API boundary.
- Invalid or corrupt backend data returns a generic server error without
  exposing raw Redis values.
- Tenant identity always comes from authenticated context, never request data.

## Observability Contract

Required counters:

- committed mutations by operation;
- idempotent replays;
- idempotency conflicts;
- insufficient-funds rejections;
- invalid mutation rejections;
- Redis failures and unknown outcomes;
- post-commit publication failures;
- reconciliation mismatches;
- fallback-mode mutations, labeled by environment.

Required histograms and gauges:

- mutation latency;
- active ledger size by tenant using bounded-cardinality tenant classification;
- archive lag;
- outbox depth and oldest unarchived record age;
- reconciliation lag;
- quarantined account count.

Logs and traces include transaction, tenant, agent, mission, and idempotency
correlation identifiers where permitted, but never credentials or raw secret
material. High-cardinality identifiers do not become Prometheus labels.

## Security Contract

- All mutations are tenant-scoped and authorized before Redis execution.
- Administrative adjustments require a distinct permission and actor identity.
- Resource types and reasons use bounded validated enums or constrained strings.
- Idempotency keys are validated and never logged in full when caller-controlled.
- Redis uses authenticated encrypted transport in production where supported.
- Lua scripts receive keys through `KEYS` and values through `ARGV`; identifiers
  are encoded before key construction.
- Transaction history is treated as sensitive tenant business data.

## Migration and Rollback Contract

Before enabling writes:

1. Inventory every current Redis balance and transaction key.
2. Validate finite values and reconcile:

   ```text
   opening grants + rewards - charges = current balance
   ```

3. Quarantine mismatches rather than synthesizing corrections.
4. Convert legacy credit values to microcredits only when exact at six decimal
   places.
5. Preserve legacy keys during a defined rollback window.
6. Shadow-read and compare legacy and v2 balances before cutover.
7. Stop writes or use a verified migration lock during final cutover.
8. Seed the PostgreSQL archive and Redis outbox high-water mark without
   generating duplicate opening grants or mutations.

Rollback disables v2 writes and returns to legacy reads only if no v2-only
mutation would be lost. Otherwise, forward repair is required.

## Required Test Matrix

### Unit

- amount syntax, precision, bounds, and non-finite values;
- opening grant creation and read purity;
- overdraft rejection;
- same-key replay and different-payload conflict;
- exact earned/spent totals;
- deterministic top-up remainder;
- transaction schema and redacted errors.

### Concurrent Redis integration

- hundreds of concurrent charges cannot overspend;
- identical concurrent idempotency keys create one transaction;
- mixed rewards and charges preserve the ledger equation;
- interruption before, during, and after script execution is retry-safe;
- script behavior is correct against the supported Redis version;
- tenant keys cannot collide or cross-read.

### Migration and recovery

- exact legacy conversion;
- corrupt and non-finite legacy values are quarantined;
- dual-read mismatches alert;
- Redis restart with AOF preserves committed mutations according to the
  documented durability mode;
- PostgreSQL outage accumulates outbox entries without losing mutations;
- archiver restart and redelivery create no duplicate archive rows;
- archive-lag circuit breaker disables and later safely restores writes;
- archive/restore reproduces balances and transaction counts.

### Full-system

- mission and saga retries do not double-charge or double-refund;
- metrics represent committed mutations only;
- economy API compatibility remains intact;
- full suite, container build, and production-style smoke pass.

## Slice 2 Acceptance Criteria

Implementation is complete only when:

1. Every approved owner decision remains represented in implementation and
   tests.
2. One atomic Redis operation commits balance, totals, ledger, archival outbox,
   and idempotency.
3. No successful balance mutation can exist without its ledger record.
4. Duplicate idempotency keys cannot double-charge, double-reward, or
   double-refund.
5. Normal concurrent charges cannot overdraw an account.
6. Production Redis failure cannot silently mutate fallback state.
7. Retention cannot delete unarchived transactions.
8. Legacy migration and rollback are tested.
9. Existing economy, mission, saga, metrics, and API contracts remain compatible
   or have an explicitly approved migration.
10. Live Redis integration and the complete repository validation suite pass.

## Explicit Non-Goals

- Durable mission cost reservations; that is Slice 3.
- Public transfer or administrative-adjustment APIs.
- Treating `Agent.credit_balance` as the marketplace authority.
- Claiming synchronous consistency between Redis and PostgreSQL projections.
- Rewriting mission, saga, CQRS, or event-store architecture.
- Converting credits into real-world money or payment value.

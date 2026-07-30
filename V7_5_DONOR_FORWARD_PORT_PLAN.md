# v7.5.0-prod Donor Forward-Port Plan

Date: 2026-07-30
Repository: `github.com/1devteam/onmiapath_v2`
Recovery branch: `recovery/v7.1.5-canonical`
Recovery commit: `ff56da4fea0a3310fc1fb34e5636359505ed76f5`
Shared base: `f592f971bc81a1432a5f343e1381aeff49626d8e`
Donor tag commit: `3e90d807cf0ea99dc30cc3bb0568b76044b3b480`
Remote-main comparison commit: `cd07968d3061e02147caba1ece40b4203f1acd51`

## Decision

Do not merge or cherry-pick `v7.5.0-prod` as a release unit.

The donor and recovery lines are sibling descendants of v7.1.5. The donor
contains useful requirements and regression tests, but its broad stabilization
commits remove working architecture and introduce interface drift. Forward-port
only behavior that survives component-level review, adapting it to the verified
recovery interfaces and validating each slice independently.

## Evidence

- The recovery branch passed 935 tests with 2 intentional skips and passed live
  PostgreSQL, Redis, NATS, MCP, Jaeger, Prometheus, and container smoke gates.
- The remote-main donor candidate collected 892 tests. Its interrupted run
  reached 665 passes, 54 failures, and 31 errors, with 142 tests not completed.
- Donor failure groups included mandatory Redis startup, authentication import
  drift, mission-executor drift, compliance model drift, stale absolute paths,
  and web-reader drift.
- The donor tag contains formatting defects. Two commits after the tag addressed
  formatting and then marked broad async failure groups as expected failures.
- The donor declares `7.5.0-prod` while runtime settings report older versions.
- The donor production environment and deployment guide contain a committed,
  non-placeholder value labeled as an OpenAI API key.
- The shared-base inventory contains 84 donor-changed files: 39 overlap files
  also changed by recovery and 45 donor-only files. Recovery has 18 additional
  changed files not present in the donor delta.

## Credential Incident

Treat the provider credential committed in the donor history as exposed.

Required response:

1. Revoke or rotate the associated provider credential.
2. Review provider usage and billing logs from the first commit containing the
   value through the rotation time.
3. Replace committed production environment files with sanitized templates.
4. Use an external secret manager or deployment-injected environment variables.
5. Consider history rewriting only after rotation. Rewriting Git history does
   not revoke a credential and may disrupt existing clones and tags.

The exposed value is intentionally omitted from this plan and must not be copied
into the recovery branch.

## Component Disposition

### 1. Authentication and authorization

Donor files:

- `backend/api/routes/auth.py`
- `backend/middleware/auth/auth_middleware.py`
- `backend/security/auth_utils.py`
- `backend/models/domain/user.py`
- `tests/unit/test_auth.py`
- `tests/unit/test_auth_routes_compat.py`

Disposition: **Reject implementation; forward-port selected security tests.**

Rationale:

- The donor replaces database-backed users and token revocation with an
  in-memory user dictionary and token reconstruction.
- New users receive an administrative role by default.
- The donor adds a static `admin-token` bypass outside production.
- The recovery baseline already has database-backed authentication, stored-token
  revocation checks, tenant claims, and RFC-compliant missing-token responses.
- The useful donor requirement is that any development bypass must fail closed
  in production. The stronger recovery outcome is to keep the bypass absent and
  add a regression test proving `admin-token` is rejected.
- JSON/form login compatibility should be assessed against the existing
  `/api/v1/auth` contract without importing the donor authentication stack.

### 2. Agent economy and API normalization

Donor files:

- `backend/economy/resource_marketplace.py`
- `backend/api/routes/economy.py`
- `tests/unit/test_economy.py`
- `tests/unit/test_economy_normalization.py`
- `tests/integration/test_pr1_regressions.py`

Disposition: **Forward-port requirements; do not copy implementation.**

Rationale:

- Redis persistence already exists in the recovery baseline and has passed live
  charge, reward, balance, and transaction validation.
- The donor reduces the marketplace from 544 lines to 253, removes metrics,
  removes the tested in-memory fallback, changes the starting balance, and
  stores balance mutation and transaction history in separate transactions.
- The donor's claim of atomic economy updates is incomplete: balance mutation
  and transaction recording can diverge if the second operation fails.
- The donor does not validate positive finite amounts before mutation and can
  write negative or non-finite accounting values.
- The donor normalization tests disagree with the implementation's actual error
  message and were later placed under broad xfail handling.

Forward-port:

- Canonical agent-ID enforcement at the API boundary.
- Explicit validation of structured marketplace payloads.
- Compatibility handling for legacy numeric payloads only if a verified caller
  still emits them.
- Tests for malformed values, non-finite numbers, missing fields, and tenant
  isolation.
- A new atomic Redis operation that changes a balance and records its transaction
  together, with idempotency and insufficient-balance behavior defined first.

### 3. Compliance rate and cost controls

Donor files:

- `backend/agents/compliance/rate_limiter.py`
- `backend/agents/compliance/rules.py`
- Remaining `backend/agents/compliance/*.py` changes

Disposition: **Rebuild persistent cost tracking; reject direct port.**

Rationale:

- The donor changes the rate-limit path to async while leaving
  `RateLimitRule.check()` synchronous. At the tag, it attempts to unpack a
  coroutine. Remote main later runs the coroutine through an event loop, which
  is unsafe when called from an already-running async loop.
- The Redis rate limiter fails open on backend errors. That behavior is not safe
  for cost, abuse, or compliance enforcement without an explicit policy.
- The cost-limit implementation performs read-then-increment in separate
  operations, allowing concurrent requests to exceed the budget.
- Cost keys omit tenant identity, creating cross-tenant collision risk.
- Daily keys use naive UTC timestamps and a rolling 24-hour expiration rather
  than a clearly defined accounting window.
- Reset uses `KEYS`, which can block Redis at scale.

Rebuild requirements:

- One async compliance contract end to end.
- Tenant-scoped keys.
- Atomic check-and-increment using a Lua script or equivalent transaction.
- Positive, finite decimal values and deterministic currency precision.
- Configurable fail-closed/fail-open behavior by rule class.
- `SCAN`-based administrative cleanup, not `KEYS`.
- Tests for concurrency, tenant isolation, Redis failure, day rollover, reset,
  and exact-limit behavior.

### 4. Mission orchestration and persistence

Donor files:

- `backend/orchestration/mission_executor.py`
- `backend/api/routes/missions_v45.py`
- `backend/models/domain/mission.py`
- `backend/core/saga/saga_orchestrator.py`

Disposition: **Reject replacement; assess only the state-query requirement.**

Rationale:

- The donor reduces the mission executor from 741 lines to 175 and removes
  NATS events, specialized-agent execution, Guardian validation, Commander
  planning abstractions, Archivist handling, swarm execution, event sourcing,
  metrics, traces, and reward policy.
- Recovery already wires mission events to the event store and has live NATS and
  lifecycle evidence.
- Donor Redis mission hashes expire while tenant mission-set entries do not,
  leaving stale indexes.
- Donor list operations fetch every mission before sorting and pagination.
- `BackgroundTasks` provides no durable queue, retry, cancellation, or worker
  ownership guarantees.

Potential forward-port:

- A read-model endpoint for durable mission status and tenant listings, built on
  the existing event store/CQRS projection rather than a second Redis source of
  truth.
- Deterministic cursor pagination and tenant-isolation tests.

### 5. Application composition and observability

Donor files:

- `backend/main.py`
- `backend/integrations/observability/telemetry.py`
- `backend/api/routes/metrics.py`
- `monitoring/prometheus.yml`
- `monitoring/grafana-datasources.yml`
- `docker-compose.v3.yml`

Disposition: **Reject deletions; retain recovered implementation.**

Rationale:

- The donor removes NATS exporter coverage, Loki configuration, dashboard
  provisioning, structured lifecycle startup, and most telemetry setup.
- The donor Prometheus configuration attempts to scrape the NATS monitoring port
  directly as Prometheus metrics after removing the exporter.
- The recovered baseline passed live trace export and Prometheus scrape/storage
  validation.
- Any deployment-compose change must be reconstructed from the actual target
  topology and tested as a complete stack.

### 6. Configuration and dependency policy

Donor files:

- `backend/config/settings.py`
- `.env.example`
- `.env.production`
- `requirements.txt`
- `pytest.ini`

Disposition: **Forward-port production validation concept only.**

Rationale:

- Fail-fast validation for unsafe production URLs is desirable.
- The donor defaults `ENVIRONMENT` to production while embedding service-specific
  defaults, and uses field-order-sensitive validation.
- A raw substring test for `localhost` is not a complete URL/host validation
  policy.
- The donor dependency file removes packages still imported by retained
  recovery components.
- The donor later uses collection hooks to xfail broad test groups instead of
  resolving failures.
- Production secrets must never be committed.

Forward-port:

- Pydantic-v2 model-level production validation after all settings are loaded.
- Parsed hostname validation for database, Redis, NATS, OTLP, and local-model
  endpoints.
- Explicit exceptions for approved container/service DNS names.
- Tests for development, staging, production, malformed URLs, IPv4/IPv6 loopback,
  missing secrets, and placeholder secrets.

### 7. Agent, governance, tools, and domain-model edits

Donor files:

- `backend/agents/base/base_agent_v3.py`
- `backend/agents/implementations/*.py`
- `backend/agents/governance/*.py`
- `backend/agents/tools/*.py`
- `backend/integrations/tools/*.py`
- `backend/integrations/llm/*.py`
- `backend/models/domain/agent.py`
- Associated phase and tool tests

Disposition: **Reject bulk changes; review only against a named defect.**

Rationale:

- Most edits are formatter changes, interface simplifications, or deletions
  coupled to the donor's reduced application architecture.
- Recovery repairs already cover active-interpreter MCP startup, safe file
  boundaries, calculator evaluation, current LLM interfaces, and specialized
  agent test behavior.
- No component should be ported without an identified missing behavior and a
  failing recovery-side regression test.

### 8. Remaining API, database, scheduler, and workflow edits

Donor files:

- `backend/api/routes/agents.py`
- `backend/api/routes/campaigns.py`
- `backend/api/routes/performance.py`
- `backend/api/routes/revenue.py`
- `backend/api/routes/scheduler.py`
- `backend/api/routes/vault.py`
- `backend/api/routes/workforces.py`
- `backend/core/scheduler/scheduler_service.py`
- `backend/database/models.py`
- `backend/database/session.py`
- `backend/orchestration/lead_generation_workflow.py`
- `backend/orchestration/revenue_agent.py`
- `backend/orchestration/workforce_coordinator.py`
- Associated integration, staging, phase, and persistence tests

Disposition: **Retain recovery versions; review by explicit contract only.**

Rationale:

- These files overlap recovery fixes for portable databases, lifecycle ordering,
  workforce/revenue dependencies, API behavior, and live-service operation.
- Most donor changes are formatting, constructor adaptation, or compatibility
  edits required by its reduced main application.
- The recovered versions passed the complete isolated suite and the applicable
  live lifecycle tests.
- A donor hunk is eligible only when a recovery-side failing test demonstrates
  a missing contract. It must not reintroduce stale constructor signatures or
  absolute paths.

### 9. Documentation and deployment

Donor files:

- `HETZNER_DEPLOYMENT_GUIDE.md`
- `OMNIPATH_V2_CAPABILITIES_WHITEPAPER.md`
- `OMNIPATH_V2_STABILIZATION_REPORT.md`
- `PROJECT_SPEC.md`
- `README.md`
- `README.v3.md`
- `CHANGELOG.md`
- `VERSION`

Disposition: **Quarantine and reconcile; do not copy claims or secrets.**

Rationale:

- The documents claim production readiness despite failing and incomplete test
  evidence.
- Version declarations disagree with runtime configuration.
- The deployment material contains an exposed provider credential and
  environment-specific values.
- Useful operational topics—TLS, firewalling, backups, monitoring, and rollback—
  should be reconstructed after the target host, DNS, secret store, and backup
  destination are verified.
- Marketing claims require feature-by-feature verification against the promoted
  build.

## Dependency-Ordered Forward-Port Slices

### Slice 0: Credential containment

Owner action is required at the provider:

- Rotate the exposed credential.
- Review usage and billing logs.
- Confirm whether the deployment address and domain remain authoritative.

Code-side action:

- Add automated secret scanning to CI.
- Add sanitized production environment templates.

### Slice 1: Contract hardening

- Add economy payload normalization and validation tests against the recovery
  API.
- Add authentication tests proving static bypass tokens are rejected.
- Add production settings validation tests.
- Make no persistence changes in this slice.

Acceptance:

- Full isolated suite remains green.
- Auth failures remain HTTP 401 with `WWW-Authenticate: Bearer`.
- No database-backed auth behavior is replaced.

### Slice 2: Atomic economy ledger

- Define balance, overdraft, idempotency, and transaction-retention policy.
- Implement one atomic Redis mutation for balance totals and transaction record.
- Preserve metrics and the tested development fallback.
- Add concurrency, replay, malformed amount, and Redis-failure tests.

Acceptance:

- No balance mutation can exist without its ledger entry.
- Duplicate idempotency keys cannot double-charge or double-reward.
- Live Redis integration and full suite pass.

### Slice 3: Durable cost governance

- Introduce an async, tenant-scoped cost-budget store.
- Atomically reserve cost before execution and support explicit settlement or
  release semantics.
- Keep rule failure policy configurable and observable.

Acceptance:

- Concurrent reservations cannot exceed the configured limit.
- Tenant keys cannot collide.
- Redis outage behavior matches the configured safety policy.

### Slice 4: Mission read model

- Determine whether the existing CQRS mission projection already satisfies
  status and tenant-list requirements.
- If gaps remain, extend the projection and routes rather than introducing
  Redis mission hashes as a competing source of truth.

Acceptance:

- Durable status survives application restart.
- Tenant isolation and deterministic pagination pass.
- Event replay reproduces the read model.

### Slice 5: Production configuration and deployment

- Reconcile version sources.
- Build a sanitized, target-specific deployment guide.
- Verify TLS, firewall, backup/restore, monitoring, alerting, and rollback.
- Run production-style smoke and recovery tests.

Acceptance:

- No committed credentials or placeholder secrets are accepted at startup.
- Backup and restore are demonstrated.
- Monitoring and alert delivery are demonstrated.
- Runtime, API, image, and documentation versions agree.

## Validation Required for Every Slice

1. Read all touched implementation and test files completely.
2. Add recovery-side regression tests before changing behavior.
3. Run targeted unit and integration tests.
4. Run Black, Flake8, Ruff, Bandit, compilation, and dependency checks.
5. Run the complete test suite.
6. Run the applicable live-service test.
7. Build and smoke-test the production image.
8. Inspect the diff for secrets, unrelated changes, and interface deletions.
9. Commit each slice independently on the recovery branch.
10. Push only after local validation, then require remote CI success.

## Rollback

- Each slice is a separate commit and can be reverted independently.
- Do not change existing Redis key formats without a migration and dual-read or
  rollback plan.
- Do not remove existing event, metric, or API contracts during forward-porting.
- Keep `main` unchanged until all donor, deployment, and provenance gates pass.

## Recommended First Implementation

Begin with the code-side portion of Slice 0 by adding secret scanning and
sanitized environment templates, while the exposed provider credential is
rotated outside the repository. Then execute Slice 1 contract hardening. These
changes have the smallest operational blast radius and establish the gates
needed to prevent the donor's credential, authentication, economy, and settings
regressions before persistence code changes.

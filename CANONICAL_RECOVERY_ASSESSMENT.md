# Omnipath Canonical Recovery Assessment

Date: 2026-07-31
Status: Recovery branch candidate; ledger Slices 1–4 and CI baselines verified
Source: `/home/inmoa/projects/omnipath_v2`
Source commit: `f592f971bc81a1432a5f343e1381aeff49626d8e`
Source remote: `git@github.com:1devteam/onmiapath_v2.git` (verified as a real,
independent GitHub repository)

## Decision

This Omnipath v7.1.5 tree is the most complete recovered implementation and is
the canonical **recovery candidate** on branch
`recovery/v7.1.5-canonical`. Its isolated and live-service
integration baselines are clean. It is not being declared the production
repository until version reconciliation, deployment review, and repository
provenance gates are complete.

The repository also contains a direct descendant `v7.5.0-prod` release and a
newer remote-main commit. A separately staged comparison found that line is not
a clean replacement baseline: its interrupted run reached 665 passes, 54
failures, and 31 errors, with 142 tests not completed. It is therefore treated
as a hardening/feature donor for selective forward-porting.

The smaller `/home/inmoa/projects/omnipath` repository remains the verified,
current minimal baseline. Its 23 tests pass, its latest commit is newer
(`c48540fa3b92cc03779aefa63b003befae9c78ce`), and its GitHub remote is
`https://github.com/1devteam/omnipath.git`.

These roles prevent a broad but partially broken recovery from overwriting a
smaller working implementation.

### 2026-07-31 checkpoint

The canonical recovery branch has advanced through economy-ledger Commit 4 at
`3e1066f6b9de070362fd5e5562d7b27667c13c04`. Slices 1–4 provide exact amount
and mutation contracts, atomic Redis state, append-only PostgreSQL archival,
signed legacy inventory, reconciliation, fenced migration locking, and guarded
archive-to-Redis recovery. The full local suite reports 1,138 passed and 2
skipped with 64% aggregate coverage. GitHub Actions run `30642709117` passed all
quality, security, test, Docker-build, and image-scan jobs.

This checkpoint does not promote the branch or switch runtime callers. Economy
caller migration (Commit 5), production configuration/operations (Commit 6),
and final acceptance evidence (Commit 7) remain incomplete and are deferred
while Obex Blackvault defines the project pivot.

## Preservation

- The source repositories were not modified.
- Git history was intentionally excluded from this staged copy.
- Generated caches and temporary test data were excluded.
- The final recovery scan indexed 356 staged files after temporary test
  databases were removed.
- Reproducible scan outputs are stored under
  `/home/inmoa/recovery/operations/2026-07-30/canonical-v7.1.5`.

## Staging Corrections

Corrections were applied only to this copied tree:

1. Added `backend/__init__.py` to establish the local `backend` package boundary
   and prevent collision with an unrelated installed Python package.
2. Corrected SQLite URL selection in `backend/database/session.py` so synchronous
   and asynchronous engines receive compatible drivers while retaining the
   local-development SQLite fallback.
3. Made migration-test paths portable and scoped database overrides so test
   state cannot leak across modules.
4. Aligned authentication and saga tests with the implemented interfaces.
5. Made MCP shutdown idempotent, selected the active Python interpreter for
   built-in servers, and corrected capability discovery.
6. Corrected application startup dependency order for workforce and revenue
   orchestration services.
7. Corrected `ResourceMarketplace` configuration loading so live Redis uses
   `Settings.REDIS_URL` rather than silently falling back to localhost.
8. Restored the pinned OpenTelemetry instrumentation by constraining
   `setuptools<81`, which retains its required `pkg_resources` interface.
9. Made telemetry initialization settings-driven and idempotent. Import-time
   tracer and meter access now uses OpenTelemetry proxies without locking in
   default provider settings.
10. Disabled unsupported OTLP metric export by default. Prometheus remains the
    default metrics backend; deployments with an OTLP metrics collector may
    opt in with `OTEL_METRICS_ENABLED=true`.
11. Isolated a fresh event loop per asynchronous test so synchronous API-client
    shutdown cannot invalidate later async tests.
12. Pinned `pyreqwest-impersonate==0.5.3`, the compatible Python 3.12 wheel
    required by the retained legacy search dependency.

## Validation

- Python source compilation: passed.
- Test discovery: 921 tests collected.
- Complete isolated test run:
  - 919 passed
  - 2 skipped
  - 0 failed
  - 0 errors
  - Runtime: 222.49 seconds in the clean branch environment
- The isolated suite explicitly disabled NATS and OpenTelemetry through
  application settings. Those integrations were validated separately against
  live disposable services.
- Ruff checks for all changed Python files: passed.
- Python compilation for `backend` and `tests`: passed.
- Dependency constraints include the compatibility bounds required by the
  pinned OpenTelemetry and Langfuse releases and a Python 3.12 wheel constraint
  for `pyreqwest-impersonate`.
- A clean, branch-local Python 3.12 virtual environment was built from
  `requirements.txt`; `pip check` reported no broken requirements.
- CI hardening validation:
  - 937 tests collected; 935 passed, 2 skipped, 0 failed.
  - Black, Flake8, Ruff, Bandit, Python compilation, and Actionlint passed.
  - GitHub Actions run `30588958061` passed all four jobs on the exact
    hardened commit: Code Quality, Static Security, Python Tests, and Docker
    Build.
  - Workflow dependencies use current Node 24-compatible release tags; the
    container scanner is pinned to a versioned Trivy action rather than a
    mutable branch.
  - The protected-branch release preparer produced a correct 7.1.5 to 7.1.6
    patch release in a disposable Git repository without committing, tagging,
    or pushing.
  - The Python 3.12 production image built successfully and ran as a non-root
    user. Its `/health` and `/version` endpoints both reported 7.1.5.

### Live-service validation

- PostgreSQL 15:
  - Alembic reported one head: `d4e5f6a7b8c9`.
  - Upgrade from base to head succeeded and created 26 public tables.
  - Full downgrade to base succeeded.
  - Re-upgrade to head succeeded.
- Redis 7:
  - The marketplace connected through the configured URL.
  - Charge, reward, balance, and persisted transaction behavior passed.
- NATS 2.10 with JetStream:
  - Subscription and publication delivered a real event.
  - Streams `AGENTS`, `ECONOMY`, `LEARNING`, `MISSIONS`, and `SYSTEM` were
    created and verified.
- Application lifecycle:
  - The complete API integration module passed against live PostgreSQL, Redis,
    and NATS: 23 passed in 74.32 seconds.
  - All five built-in MCP subprocesses started and exposed their tools.
- OpenTelemetry and Jaeger:
  - The application exported 4 traces containing 12 spans during validation.
  - Recorded operations covered health, OpenAPI, and metrics requests.
  - Shutdown flushed traces cleanly.
- Prometheus:
  - An isolated Prometheus 2.48.1 server scraped the application successfully.
  - Target health was `up`, the `up` sample equaled `1`, and Omnipath HTTP
    request series were stored.

## Recovered Failure Groups

1. Migration tests now derive portable paths from the repository root.
2. Integration tests create the required application schema.
3. The lifecycle test database override is scoped and cleaned up, preventing
   cross-module contamination.
4. Authentication tests now enforce the implemented RFC-compliant HTTP 401
   response for missing bearer credentials.
5. Social-posting tests verify the current immutable `EventStore.append`
   interface.
6. MCP shutdown is idempotent and tolerates already-exited subprocesses, with
   regression coverage.
7. Application startup now constructs `WorkforceCoordinator` with all required
   dependencies before constructing its dependent `RevenueAgent`.
8. Built-in MCP servers use the active application interpreter and successfully
   expose all five registered tools through protocol discovery.

## Remaining Integration Gaps

1. Two specialized-agent tests remain intentionally skipped.
2. Grafana dashboards, alert delivery, backup/restore, load targets, and a
   production-style deployment have not been exercised in this recovery pass.
3. Historical version declarations remain in older planning documents. The
   recovery candidate and root README now identify version 7.1.5; repository
   identity remains resolved, and `onmiapath_v2` and `omnipath` must remain
   distinct.
4. Deployment dependencies must continue to be installed in a dedicated
   Omnipath virtual environment or container rather than the shared utilities
   environment.
5. A release pull request created with the repository `GITHUB_TOKEN` may require
   a maintainer to approve its CI run. Fully unattended release PR validation
   requires a separately configured GitHub App or narrowly scoped automation
   token; no broader credential was introduced during recovery.

## Promotion Gate

Do not replace either source repository with this candidate. Promotion requires:

1. Reconciling the conflicting README, project specification, and version
   declarations.
2. Reviewing the deployment manifests and running a production-style smoke test,
   including Grafana and alerting configuration.
3. Reviewing and promoting the recovery branch through a pull request without
   force-pushing or replacing either repository history.

## Next Engineering Order

1. Record and approve the new project direction before starting additional
   behavioral work. Economy-ledger caller cutover remains paused at the clean
   Commit 4 boundary.
2. Rotate and audit the provider credential exposed in the donor's committed
   deployment material; do not copy that material into the recovery line.
3. If the economy-ledger program resumes, continue with Commit 5 caller
   migration, then Commits 6–7 operational and acceptance gates; do not skip
   directly to production cutover.
4. Review deployment manifests and run production-style monitoring, alerting,
   backup, restore, and smoke-test gates.
5. Configure a GitHub App token if unattended release-PR CI approval is desired.
6. Open a provenance-preserving pull request only after the donor forward-port
   and deployment gates are complete.

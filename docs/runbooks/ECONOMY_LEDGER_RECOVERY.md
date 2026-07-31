# Economy Ledger Migration and Recovery

The Slice 4 tooling is fail-closed. It does not delete, trim, or overwrite
legacy or v2 ledger data. Caller cutover remains disabled until Slice 5.

## Legacy inventory

Set a secret of at least 32 bytes in `ECONOMY_MIGRATION_MANIFEST_SECRET`, then
run `scripts/economy_ledger_inventory.py` with `--redis-url`, `--tenant`, and a
non-secret `--signature-key-id`. The command uses Redis `SCAN`, emits the signed
manifest to standard output, exits `0` only when all discovered agents are
proven, and exits `2` when anything is quarantined.

Never migrate a manifest containing quarantine findings. A transaction list at
the legacy 10,000-record cap is always quarantined because complete history can
no longer be proven.

## Cutover fence

`MigrationLock` acquires or renews a tenant lock only for the same owner token.
Release compares the stored owner. A stale process cannot release a successor's
lock. Generate a new high-entropy token for each migration execution and never
write the raw token to logs or the migration journal.

Immediately before cutover, create a second signed inventory and use
`manifests_match` to compare it with the approved inventory. Any difference
stops the cutover.

## Archive recovery

`restore_archive_to_empty_tenant` first reconciles tenant identity, contiguous
sequence, canonical record checksums, transaction and idempotency uniqueness,
and each agent balance chain. It then requires ownership of the migration lock
and refuses any target containing v2 data. The Redis transaction rebuilds the
tenant and agent streams, exact balances, idempotency records, archive metadata,
and a recovery journal. It contains no cleanup path.

Keep the migration lock after replay. Verify API shadow reads and PostgreSQL
counts/checkpoints before releasing it. Legacy deletion requires separate owner
authorization after the observation period; this runbook does not authorize it.

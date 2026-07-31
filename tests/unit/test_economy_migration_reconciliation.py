"""Tests for fail-closed legacy inventory and archive reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.economy.contracts import (
    LedgerOperation,
    LedgerTransaction,
    canonical_json_bytes,
)
from backend.economy.migration import (
    deterministic_legacy_transaction_id,
    inventory_legacy_tenant,
    manifests_match,
    verify_manifest,
)
from backend.economy.reconciliation import reconcile_transactions


pytestmark = [pytest.mark.unit, pytest.mark.economy]
SECRET = b"migration-manifest-test-secret-32-bytes-minimum"


class FakeLegacyRedis:
    """Minimal async Redis surface that records whether SCAN was used."""

    def __init__(self, hashes=None, lists=None, other=None):
        self.hashes = hashes or {}
        self.lists = lists or {}
        self.other = other or {}
        self.scan_calls = 0

    async def scan(self, *, cursor, match, count):
        self.scan_calls += 1
        keys = sorted([*self.hashes, *self.lists, *self.other])
        return 0, keys

    async def type(self, key):
        if key in self.hashes:
            return "hash"
        if key in self.lists:
            return "list"
        return "string"

    async def memory_usage(self, key):
        return 128

    async def hgetall(self, key):
        return self.hashes[key]

    async def lrange(self, key, start, end):
        return self.lists[key]


def legacy_transaction(agent_id="agent-1", operation="charge", amount=10.0):
    return json.dumps(
        {
            "id": "legacy-1",
            "agent_id": agent_id,
            "type": operation,
            "amount": amount,
            "resource_type": "llm_call",
            "mission_id": "mission-1",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_inventory_scans_signs_and_proves_complete_legacy_equation():
    client = FakeLegacyRedis(
        hashes={
            "economy:tenant-1:balance:agent-1": {
                "type": "commander",
                "balance": "990.0",
                "total_earned": "1000.0",
                "total_spent": "10.0",
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        },
        lists={"economy:tenant-1:txns": [legacy_transaction()]},
    )

    manifest = await inventory_legacy_tenant(
        client,
        "tenant-1",
        signing_secret=SECRET,
        signature_key_id="test-key-1",
    )

    assert client.scan_calls == 1
    assert verify_manifest(manifest, SECRET)
    assert len(manifest.verified_agents) == 1
    assert not manifest.quarantine
    assert manifest.verified_agents[0].balance_microcredits == 990_000_000

    reobserved = replace(manifest, generated_at="2026-01-02T00:00:00.000000Z", signature="")
    unsigned = reobserved.unsigned_payload()
    reobserved = replace(
        reobserved,
        signature=hmac.new(SECRET, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest(),
    )
    assert manifests_match(manifest, reobserved, expected_secret=SECRET, observed_secret=SECRET)
    assert not manifests_match(
        manifest,
        replace(reobserved, keys=()),
        expected_secret=SECRET,
        observed_secret=SECRET,
    )


@pytest.mark.asyncio
async def test_inventory_quarantines_irreconcilable_and_malformed_history():
    client = FakeLegacyRedis(
        hashes={
            "economy:tenant-1:balance:agent-1": {
                "type": "commander",
                "balance": "999.0",
                "total_earned": "1000.0",
                "total_spent": "10.0",
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        },
        lists={"economy:tenant-1:txns": ["not-json"]},
    )

    manifest = await inventory_legacy_tenant(
        client, "tenant-1", signing_secret=SECRET, signature_key_id="test-key-1"
    )

    assert not manifest.verified_agents
    assert {finding.code for finding in manifest.quarantine} == {
        "malformed_transaction",
        "irreconcilable_balance",
    }


@pytest.mark.asyncio
async def test_inventory_quarantines_history_at_legacy_trim_limit():
    transaction = legacy_transaction(amount=0.000001)
    client = FakeLegacyRedis(
        hashes={
            "economy:tenant-1:balance:agent-1": {
                "type": "worker",
                "balance": "999.99",
                "total_earned": "1000.0",
                "total_spent": "0.01",
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        },
        lists={"economy:tenant-1:txns": [transaction] * 10_000},
    )

    manifest = await inventory_legacy_tenant(
        client, "tenant-1", signing_secret=SECRET, signature_key_id="test-key-1"
    )

    assert not manifest.verified_agents
    assert "truncated_history" in {finding.code for finding in manifest.quarantine}


def transaction(sequence, *, tenant_id="tenant-1", before=0, after=10):
    item = LedgerTransaction(
        transaction_id=str(uuid4()),
        tenant_id=tenant_id,
        agent_id="agent-1",
        operation=LedgerOperation.OPENING_GRANT if sequence == 1 else LedgerOperation.REWARD,
        amount_microcredits=after - before,
        delta_microcredits=after - before,
        balance_before_microcredits=before,
        balance_after_microcredits=after,
        resource_type="opening_grant" if sequence == 1 else "mission_reward",
        reason="opening_grant" if sequence == 1 else "earned_reward",
        mission_id=None,
        idempotency_key_hash=hashlib.sha256(f"idem-{sequence}".encode()).hexdigest(),
        request_hash=hashlib.sha256(f"request-{sequence}".encode()).hexdigest(),
        outbox_sequence=sequence,
        created_at=datetime(2026, 1, sequence, tzinfo=timezone.utc),
    )
    payload = {field: getattr(item, field) for field in item.__dataclass_fields__}
    payload["operation"] = item.operation.value
    payload["created_at"] = item.created_at.isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    return item, hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def test_reconciliation_proves_order_checksums_and_balance_chain():
    first = transaction(1, before=0, after=10)
    second = transaction(2, before=10, after=15)

    report = reconcile_transactions(
        "tenant-1", [first, second], expected_count=2, expected_last_sha256=second[1]
    )

    assert report.verified
    assert report.balances == {"agent-1": 15}


def test_reconciliation_reports_tenant_sequence_checksum_and_checkpoint_mismatches():
    item, _ = transaction(2, tenant_id="wrong-tenant", before=9, after=10)
    report = reconcile_transactions(
        "tenant-1", [(item, "0" * 64)], expected_count=2, expected_last_sha256="1" * 64
    )

    assert {mismatch.code for mismatch in report.mismatches} == {
        "tenant_mismatch",
        "sequence_gap",
        "checksum_mismatch",
        "balance_chain_mismatch",
        "count_mismatch",
        "checkpoint_mismatch",
    }


def test_deterministic_import_identity_is_stable_and_ordinal_scoped():
    checksum = hashlib.sha256(b"legacy-record").hexdigest()
    first = deterministic_legacy_transaction_id("tenant-1", checksum, 1)
    assert first == deterministic_legacy_transaction_id("tenant-1", checksum, 1)
    assert first != deterministic_legacy_transaction_id("tenant-1", checksum, 2)

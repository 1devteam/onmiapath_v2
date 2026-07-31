"""Fail-closed inventory and conversion primitives for legacy economy data."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import resources
from typing import Any, cast
from uuid import UUID, uuid5

from backend.economy.amount import InvalidCreditAmount, parse_credit_amount
from backend.economy.contracts import canonical_json_bytes
from backend.economy.keyspace import EconomyKeyspace, normalize_identifier


LEGACY_OPENING_GRANT_MICROCREDITS = 1_000_000_000
LEGACY_TRANSACTION_LIMIT = 10_000
MIGRATION_MANIFEST_VERSION = 1
MIGRATION_UUID_NAMESPACE = UUID("fb6e193f-87aa-5f33-99f8-a6e927b9569e")


class MigrationSafetyError(RuntimeError):
    """Raised when migration evidence cannot prove a safe operation."""


@dataclass(frozen=True, slots=True)
class InventoryKey:
    """Auditable facts about one legacy Redis key."""

    key: str
    redis_type: str
    cardinality: int
    memory_bytes: int | None
    content_sha256: str
    encoding_valid: bool


@dataclass(frozen=True, slots=True)
class QuarantineFinding:
    """One explicit reason that prevents automatic migration."""

    agent_id: str | None
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class VerifiedAgent:
    """A legacy agent whose complete balance equation was proven."""

    agent_id: str
    agent_type: str
    balance_microcredits: int
    total_earned_microcredits: int
    total_spent_microcredits: int
    transaction_count: int


@dataclass(frozen=True, slots=True)
class MigrationManifest:
    """Signed, deterministic evidence used to fence a later cutover."""

    version: int
    tenant_id: str
    generated_at: str
    keys: tuple[InventoryKey, ...]
    verified_agents: tuple[VerifiedAgent, ...]
    quarantine: tuple[QuarantineFinding, ...]
    signature_key_id: str
    signature: str

    def unsigned_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("signature")
        return payload


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise MigrationSafetyError("Redis returned a non-text legacy value")


def _content_digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes({"value": value})).hexdigest()


def _parse_nonnegative_credit(value: str) -> int:
    """Parse a legacy balance field while permitting canonical numeric zero aliases."""
    if value in ("0", "0.0", "0.000000"):
        return 0
    return parse_credit_amount(value)


def _signature(payload: dict[str, object], secret: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("manifest signing secret must contain at least 32 bytes")
    return hmac.new(secret, canonical_json_bytes(payload), hashlib.sha256).hexdigest()


def verify_manifest(manifest: MigrationManifest, secret: bytes) -> bool:
    """Verify a manifest signature using constant-time comparison."""
    return hmac.compare_digest(manifest.signature, _signature(manifest.unsigned_payload(), secret))


def manifest_state_sha256(manifest: MigrationManifest) -> str:
    """Hash migration evidence while excluding time and signature envelope fields."""
    payload = manifest.unsigned_payload()
    payload.pop("generated_at")
    payload.pop("signature_key_id")
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def manifests_match(
    expected: MigrationManifest,
    observed: MigrationManifest,
    *,
    expected_secret: bytes,
    observed_secret: bytes,
) -> bool:
    """Verify both signatures and compare the exact legacy state evidence."""
    return (
        expected.tenant_id == observed.tenant_id
        and verify_manifest(expected, expected_secret)
        and verify_manifest(observed, observed_secret)
        and manifest_state_sha256(expected) == manifest_state_sha256(observed)
    )


def deterministic_legacy_transaction_id(
    tenant_id: str, record_sha256: str, chronological_ordinal: int
) -> str:
    """Return the stable UUIDv5 assigned to an imported legacy record."""
    if chronological_ordinal < 1:
        raise ValueError("chronological ordinal must be positive")
    return str(
        uuid5(MIGRATION_UUID_NAMESPACE, f"{tenant_id}:{record_sha256}:{chronological_ordinal}")
    )


def _parse_transactions(
    raw_records: list[str],
) -> tuple[dict[str, list[dict[str, object]]], list[QuarantineFinding]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    findings: list[QuarantineFinding] = []
    required = {"id", "agent_id", "type", "amount", "resource_type", "mission_id", "timestamp"}
    for newest_index, raw in enumerate(raw_records):
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != required:
                raise ValueError("unexpected fields")
            agent_id = normalize_identifier(payload["agent_id"], field_name="agent_id")
            operation = payload["type"]
            if operation not in ("charge", "reward"):
                raise ValueError("unsupported operation")
            amount = parse_credit_amount(str(payload["amount"]))
            timestamp = payload["timestamp"]
            if not isinstance(timestamp, str):
                raise ValueError("timestamp is not text")
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timestamp is not timezone-aware")
            grouped.setdefault(agent_id, []).append(
                {**payload, "amount_microcredits": amount, "_newest_index": newest_index}
            )
        except (InvalidCreditAmount, TypeError, ValueError, json.JSONDecodeError) as exc:
            findings.append(QuarantineFinding(None, "malformed_transaction", str(exc)[:160]))
    for records in grouped.values():
        records.reverse()
    return grouped, findings


async def inventory_legacy_tenant(
    redis_client: Any,
    tenant_id: str,
    *,
    signing_secret: bytes,
    signature_key_id: str,
) -> MigrationManifest:
    """SCAN and prove one tenant's legacy state without performing writes."""
    tenant = normalize_identifier(tenant_id, field_name="tenant_id")
    normalize_identifier(signature_key_id, field_name="signature_key_id")
    prefix = f"economy:{tenant}:"
    cursor: int | str = 0
    found: list[str] = []
    while True:
        cursor, batch = await redis_client.scan(cursor=cursor, match=f"{prefix}*", count=500)
        found.extend(_text(key) for key in batch)
        if int(cursor) == 0:
            break
    found = sorted(set(found))

    inventories: list[InventoryKey] = []
    balance_payloads: dict[str, dict[str, str]] = {}
    raw_transactions: list[str] = []
    findings: list[QuarantineFinding] = []
    transaction_key = f"{prefix}txns"
    for key in found:
        key_type = _text(await redis_client.type(key))
        memory = await redis_client.memory_usage(key)
        valid = True
        value: object
        if key == transaction_key and key_type == "list":
            raw_transactions = [_text(item) for item in await redis_client.lrange(key, 0, -1)]
            value = raw_transactions
            cardinality = len(raw_transactions)
        elif key.startswith(f"{prefix}balance:") and key_type == "hash":
            agent_id = key.removeprefix(f"{prefix}balance:")
            try:
                agent_id = normalize_identifier(agent_id, field_name="agent_id")
                value = {_text(k): _text(v) for k, v in (await redis_client.hgetall(key)).items()}
                balance_payloads[agent_id] = value
                cardinality = len(value)
            except (MigrationSafetyError, ValueError, UnicodeError):
                valid, value, cardinality = False, {}, 0
        else:
            valid, value, cardinality = False, {}, 0
            findings.append(QuarantineFinding(None, "unexpected_legacy_key", key))
        inventories.append(
            InventoryKey(key, key_type, cardinality, memory, _content_digest(value), valid)
        )

    if len(raw_transactions) >= LEGACY_TRANSACTION_LIMIT:
        findings.append(
            QuarantineFinding(
                None, "truncated_history", "legacy transaction list reached its 10000-record cap"
            )
        )
    grouped, transaction_findings = _parse_transactions(raw_transactions)
    findings.extend(transaction_findings)
    verified: list[VerifiedAgent] = []
    expected_balance_fields = {"type", "balance", "total_earned", "total_spent", "last_updated"}
    for agent_id, balance in sorted(balance_payloads.items()):
        try:
            if set(balance) != expected_balance_fields:
                raise ValueError("unexpected balance fields")
            current = _parse_nonnegative_credit(balance["balance"])
            earned = parse_credit_amount(balance["total_earned"])
            spent = _parse_nonnegative_credit(balance["total_spent"])
            rewards = sum(
                cast(int, record["amount_microcredits"])
                for record in grouped.get(agent_id, [])
                if record["type"] == "reward"
            )
            charges = sum(
                cast(int, record["amount_microcredits"])
                for record in grouped.get(agent_id, [])
                if record["type"] == "charge"
            )
            if (
                earned != LEGACY_OPENING_GRANT_MICROCREDITS + rewards
                or spent != charges
                or current != earned - spent
            ):
                raise ValueError("legacy balance equation is irreconcilable")
            if any(item.code == "truncated_history" for item in findings):
                raise ValueError("complete history cannot be proven")
            verified.append(
                VerifiedAgent(
                    agent_id,
                    balance["type"],
                    current,
                    earned,
                    spent,
                    len(grouped.get(agent_id, [])),
                )
            )
        except (InvalidCreditAmount, KeyError, TypeError, ValueError) as exc:
            findings.append(QuarantineFinding(agent_id, "irreconcilable_balance", str(exc)[:160]))
    for agent_id in sorted(set(grouped) - set(balance_payloads)):
        findings.append(
            QuarantineFinding(
                agent_id, "missing_balance", "transactions exist without a balance hash"
            )
        )

    generated_at = (
        datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    unsigned = {
        "version": MIGRATION_MANIFEST_VERSION,
        "tenant_id": tenant,
        "generated_at": generated_at,
        "keys": [asdict(item) for item in inventories],
        "verified_agents": [asdict(item) for item in verified],
        "quarantine": [asdict(item) for item in findings],
        "signature_key_id": signature_key_id,
    }
    return MigrationManifest(
        MIGRATION_MANIFEST_VERSION,
        tenant,
        generated_at,
        tuple(inventories),
        tuple(verified),
        tuple(findings),
        signature_key_id,
        _signature(unsigned, signing_secret),
    )


class MigrationLock:
    """Fenced, compare-owner Redis migration lock."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @staticmethod
    def _script(name: str) -> str:
        return resources.files("backend.economy.lua").joinpath(name).read_text(encoding="utf-8")

    async def acquire(self, tenant_id: str, owner_token: str, ttl_ms: int) -> str:
        if not owner_token or not 1_000 <= ttl_ms <= 3_600_000:
            raise ValueError("owner token and lock TTL from 1000 to 3600000 ms are required")
        key = EconomyKeyspace.for_tenant(tenant_id).migration_lock
        result = await self._redis.eval(
            self._script("acquire_migration_lock_v1.lua"), 1, key, owner_token, ttl_ms
        )
        return _text(result)

    async def release(self, tenant_id: str, owner_token: str) -> bool:
        if not owner_token:
            raise ValueError("owner token is required")
        key = EconomyKeyspace.for_tenant(tenant_id).migration_lock
        return bool(
            await self._redis.eval(
                self._script("release_migration_lock_v1.lua"), 1, key, owner_token
            )
        )

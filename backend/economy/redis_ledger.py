"""Redis 7 adapter and opt-in compatibility facade for the atomic ledger."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from redis.exceptions import NoScriptError, RedisError

from backend.economy.amount import (
    MAX_INT64,
    MIN_INT64,
    CreditInput,
    format_credit_amount,
    parse_credit_amount,
)
from backend.economy.contracts import (
    EconomyLedgerError,
    LedgerMutation,
    LedgerMutationResult,
    LedgerOperation,
    LedgerTransaction,
    LuaResultCode,
    MutationDisposition,
    MutationOutcomeUnknown,
    canonical_json_bytes,
    exception_for_lua_result,
)
from backend.economy.keyspace import (
    LEDGER_SCHEMA_VERSION,
    EconomyKeyspace,
    normalize_identifier,
)


DEFAULT_OPENING_GRANT_MICROCREDITS = 1_000_000_000
DEFAULT_ARCHIVE_HARD_RECORDS = 100_000
DEFAULT_ARCHIVE_HARD_AGE_MS = 900_000
MAX_LEGACY_OFFSET = 10_000

_TRANSACTION_FIELDS = frozenset(
    {
        "transaction_id",
        "tenant_id",
        "agent_id",
        "operation",
        "amount_microcredits",
        "delta_microcredits",
        "balance_before_microcredits",
        "balance_after_microcredits",
        "resource_type",
        "reason",
        "mission_id",
        "idempotency_key_hash",
        "request_hash",
        "outbox_sequence",
        "created_at",
        "schema_version",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "balance_microcredits",
        "opening_transaction",
        "transaction",
    }
)
_BALANCE_FIELDS = frozenset(
    {
        "schema_version",
        "agent_id",
        "agent_type",
        "balance_microcredits",
        "total_earned_microcredits",
        "total_spent_microcredits",
        "last_sequence",
        "last_transaction_id",
        "updated_at",
    }
)
_CANONICAL_INT_PATTERN = re.compile(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z")
_LABEL_PATTERN = re.compile(r"[a-z][a-z0-9_.:-]*\Z")


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    """Return canonical RFC 3339 UTC text with microseconds."""
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("ledger timestamps must use UTC")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_fragment(value: str | None) -> str:
    """Return canonical JSON for one prevalidated string or null."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _decode_text(value: Any, *, field_name: str) -> str:
    """Normalize a Redis bulk response to text."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MutationOutcomeUnknown(f"invalid Redis UTF-8 in {field_name}") from exc
    if isinstance(value, str):
        return value
    raise MutationOutcomeUnknown(f"invalid Redis response type for {field_name}")


def _opening_request_hash(mutation: LedgerMutation, opening_grant: int) -> str:
    """Return the canonical request hash for the internal opening grant."""
    request = {
        "agent_id": mutation.agent_id,
        "amount_microcredits": opening_grant,
        "mission_id": None,
        "operation": LedgerOperation.OPENING_GRANT.value,
        "reason": "opening_grant",
        "resource_type": "opening_grant",
        "schema_version": LEDGER_SCHEMA_VERSION,
        "tenant_id": mutation.tenant_id,
    }
    return hashlib.sha256(canonical_json_bytes(request)).hexdigest()


def _opening_idempotency_hash(agent_id: str) -> str:
    """Hash the internal stable opening identity without exposing it."""
    identity = f"opening-grant:{agent_id}:v1"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _transaction_from_payload(payload: Any) -> LedgerTransaction:
    """Validate and construct an immutable transaction from Redis JSON."""
    if not isinstance(payload, dict) or frozenset(payload) != _TRANSACTION_FIELDS:
        raise MutationOutcomeUnknown("invalid transaction payload from Redis")
    try:
        operation = LedgerOperation(payload["operation"])
        created_at_text = payload["created_at"]
        if not isinstance(created_at_text, str) or not created_at_text.endswith("Z"):
            raise ValueError("created_at is not canonical UTC")
        created_at = datetime.fromisoformat(created_at_text.replace("Z", "+00:00"))
        return LedgerTransaction(
            transaction_id=payload["transaction_id"],
            tenant_id=payload["tenant_id"],
            agent_id=payload["agent_id"],
            operation=operation,
            amount_microcredits=payload["amount_microcredits"],
            delta_microcredits=payload["delta_microcredits"],
            balance_before_microcredits=payload["balance_before_microcredits"],
            balance_after_microcredits=payload["balance_after_microcredits"],
            resource_type=payload["resource_type"],
            reason=payload["reason"],
            mission_id=payload["mission_id"],
            idempotency_key_hash=payload["idempotency_key_hash"],
            request_hash=payload["request_hash"],
            outbox_sequence=payload["outbox_sequence"],
            created_at=created_at,
            schema_version=payload["schema_version"],
        )
    except (EconomyLedgerError, KeyError, TypeError, ValueError) as exc:
        raise MutationOutcomeUnknown("invalid transaction payload from Redis") from exc


def _parse_stored_int64(value: str, *, field_name: str, nonnegative: bool = False) -> int:
    """Parse one canonical signed-int64 Redis value without accepting aliases."""
    if not isinstance(value, str) or not _CANONICAL_INT_PATTERN.fullmatch(value):
        raise MutationOutcomeUnknown(f"invalid stored {field_name}")
    parsed = int(value)
    if not MIN_INT64 <= parsed <= MAX_INT64 or (nonnegative and parsed < 0):
        raise MutationOutcomeUnknown(f"invalid stored {field_name}")
    return parsed


def _result_from_json(
    result_json: str,
    *,
    disposition: MutationDisposition,
    mutation: LedgerMutation,
    opening_grant: int,
) -> LedgerMutationResult:
    """Parse and verify a successful canonical result returned by Lua."""
    try:
        payload = json.loads(result_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MutationOutcomeUnknown("invalid result JSON from Redis") from exc
    if not isinstance(payload, dict) or frozenset(payload) != _RESULT_FIELDS:
        raise MutationOutcomeUnknown("invalid result payload from Redis")
    if canonical_json_bytes(payload).decode("utf-8") != result_json:
        raise MutationOutcomeUnknown("non-canonical result JSON from Redis")

    transaction = _transaction_from_payload(payload["transaction"])
    opening_payload = payload["opening_transaction"]
    opening_transaction = (
        None if opening_payload is None else _transaction_from_payload(opening_payload)
    )
    try:
        result = LedgerMutationResult(
            disposition=disposition,
            transaction=transaction,
            opening_transaction=opening_transaction,
            balance_microcredits=payload["balance_microcredits"],
        )
    except EconomyLedgerError as exc:
        raise MutationOutcomeUnknown("invalid result invariants from Redis") from exc

    if (
        transaction.tenant_id != mutation.tenant_id
        or transaction.agent_id != mutation.agent_id
        or transaction.operation is not mutation.operation
        or transaction.amount_microcredits != mutation.amount_microcredits
        or transaction.resource_type != mutation.resource_type
        or transaction.reason != mutation.reason
        or transaction.mission_id != mutation.mission_id
        or transaction.request_hash != mutation.request_hash
        or transaction.idempotency_key_hash != mutation.idempotency_key_hash
    ):
        raise MutationOutcomeUnknown("Redis result does not match the requested mutation")

    if opening_transaction is not None:
        if (
            opening_transaction.amount_microcredits != opening_grant
            or opening_transaction.request_hash != _opening_request_hash(mutation, opening_grant)
            or opening_transaction.idempotency_key_hash
            != _opening_idempotency_hash(mutation.agent_id)
        ):
            raise MutationOutcomeUnknown("invalid opening transaction from Redis")
        if opening_transaction.outbox_sequence + 1 != transaction.outbox_sequence:
            raise MutationOutcomeUnknown("opening and mutation sequences are not consecutive")
    return result


class RedisEconomyLedger:
    """Execute exact, tenant-scoped mutations through one Redis Lua script."""

    def __init__(
        self,
        redis_client: Any,
        *,
        opening_grant_microcredits: int = DEFAULT_OPENING_GRANT_MICROCREDITS,
        archive_hard_records: int = DEFAULT_ARCHIVE_HARD_RECORDS,
        archive_hard_age_ms: int = DEFAULT_ARCHIVE_HARD_AGE_MS,
    ) -> None:
        for field_name, value in (
            ("opening_grant_microcredits", opening_grant_microcredits),
            ("archive_hard_records", archive_hard_records),
            ("archive_hard_age_ms", archive_hard_age_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_INT64:
                raise ValueError(f"{field_name} must be a positive int64")
        self._redis = redis_client
        self.opening_grant_microcredits = opening_grant_microcredits
        self.archive_hard_records = archive_hard_records
        self.archive_hard_age_ms = archive_hard_age_ms
        self._script_sha: str | None = None

    @staticmethod
    def _script_source() -> str:
        """Load the packaged mutation script."""
        return (
            resources.files("backend.economy.lua")
            .joinpath("mutate_v1.lua")
            .read_text(encoding="utf-8")
        )

    async def _load_script(self) -> str:
        loaded = await self._redis.script_load(self._script_source())
        self._script_sha = _decode_text(loaded, field_name="script SHA")
        return self._script_sha

    @staticmethod
    def _verify_keys(keys: Sequence[str], expected_hash_tag: str) -> None:
        """Fail before Redis when any supplied key escapes the tenant slot."""
        if len(keys) != 9:
            raise ValueError("mutation requires exactly nine Redis keys")
        if any(expected_hash_tag not in key for key in keys):
            raise ValueError("all economy mutation keys must share one tenant hash tag")

    def _arguments(
        self,
        mutation: LedgerMutation,
        *,
        transaction_id: str,
        transaction_created_at: datetime,
        opening_transaction_id: str,
        opening_created_at: datetime,
        now_epoch_ms: int,
    ) -> tuple[str, ...]:
        return (
            str(LEDGER_SCHEMA_VERSION),
            mutation.operation.value,
            str(mutation.amount_microcredits),
            mutation.agent_id,
            _json_fragment(mutation.agent_id),
            mutation.agent_type,
            _json_fragment(mutation.resource_type),
            _json_fragment(mutation.reason),
            _json_fragment(mutation.mission_id),
            _json_fragment(mutation.tenant_id),
            mutation.request_hash,
            mutation.idempotency_key_hash,
            transaction_id,
            _json_fragment(transaction_id),
            _json_fragment(_format_utc(transaction_created_at)),
            opening_transaction_id,
            _json_fragment(opening_transaction_id),
            _json_fragment(_format_utc(opening_created_at)),
            _opening_idempotency_hash(mutation.agent_id),
            _opening_request_hash(mutation, self.opening_grant_microcredits),
            str(self.opening_grant_microcredits),
            str(self.archive_hard_records),
            str(self.archive_hard_age_ms),
            str(now_epoch_ms),
        )

    async def mutate(self, mutation: LedgerMutation) -> LedgerMutationResult:
        """Commit or replay one charge/reward without a non-durable fallback."""
        if not isinstance(mutation, LedgerMutation):
            raise TypeError("mutation must be a LedgerMutation")
        if mutation.operation is LedgerOperation.OPENING_GRANT:
            raise ValueError("callers cannot submit opening grants directly")

        keyspace = EconomyKeyspace.for_tenant(mutation.tenant_id)
        keys = keyspace.mutation_keys(mutation.agent_id, mutation.idempotency_key)
        self._verify_keys(keys, keyspace.hash_tag)

        now = _utc_now()
        transaction_id = str(uuid4())
        opening_transaction_id = str(uuid4())
        now_epoch_ms = int(now.timestamp() * 1000)
        arguments = self._arguments(
            mutation,
            transaction_id=transaction_id,
            transaction_created_at=now,
            opening_transaction_id=opening_transaction_id,
            opening_created_at=now,
            now_epoch_ms=now_epoch_ms,
        )

        try:
            script_sha = self._script_sha or await self._load_script()
            try:
                response = await self._redis.evalsha(
                    script_sha,
                    len(keys),
                    *keys,
                    *arguments,
                )
            except NoScriptError:
                script_sha = await self._load_script()
                response = await self._redis.evalsha(
                    script_sha,
                    len(keys),
                    *keys,
                    *arguments,
                )
        except RedisError as exc:
            raise MutationOutcomeUnknown(
                "Redis mutation outcome is unknown; retry the identical idempotency key"
            ) from exc

        if not isinstance(response, (list, tuple)) or len(response) < 2:
            raise MutationOutcomeUnknown("invalid response from Redis mutation script")
        code_text = _decode_text(response[0], field_name="result code")
        detail = _decode_text(response[1], field_name="result detail")
        try:
            code = LuaResultCode(code_text)
        except ValueError as exc:
            raise MutationOutcomeUnknown(
                "unknown response code from Redis mutation script"
            ) from exc

        if code in (LuaResultCode.COMMITTED, LuaResultCode.REPLAYED):
            disposition = (
                MutationDisposition.COMMITTED
                if code is LuaResultCode.COMMITTED
                else MutationDisposition.REPLAYED
            )
            return _result_from_json(
                detail,
                disposition=disposition,
                mutation=mutation,
                opening_grant=self.opening_grant_microcredits,
            )
        raise exception_for_lua_result(code, detail)

    async def read_balance(self, tenant_id: str, agent_id: str) -> Mapping[str, Any]:
        """Read exact stored state, or return a non-writing virtual opening balance."""
        keyspace = EconomyKeyspace.for_tenant(tenant_id)
        normalized_agent_id = normalize_identifier(agent_id, field_name="agent_id")
        raw = await self._redis.hgetall(keyspace.balance(normalized_agent_id))
        if not raw:
            return {
                "type": "unknown",
                "balance_microcredits": self.opening_grant_microcredits,
                "total_earned_microcredits": self.opening_grant_microcredits,
                "total_spent_microcredits": 0,
                "last_updated": None,
                "materialized": False,
            }
        decoded = {
            _decode_text(key, field_name="balance field"): _decode_text(
                value, field_name="balance value"
            )
            for key, value in raw.items()
        }
        if frozenset(decoded) != _BALANCE_FIELDS:
            raise MutationOutcomeUnknown("invalid stored balance fields")
        try:
            if decoded["schema_version"] != str(LEDGER_SCHEMA_VERSION):
                raise MutationOutcomeUnknown("stored balance schema is unsupported")
            if decoded["agent_id"] != normalized_agent_id:
                raise MutationOutcomeUnknown("stored balance agent identity is invalid")
            if not _LABEL_PATTERN.fullmatch(decoded["agent_type"]):
                raise MutationOutcomeUnknown("stored balance agent type is invalid")
            balance = _parse_stored_int64(
                decoded["balance_microcredits"],
                field_name="balance_microcredits",
            )
            earned = _parse_stored_int64(
                decoded["total_earned_microcredits"],
                field_name="total_earned_microcredits",
                nonnegative=True,
            )
            spent = _parse_stored_int64(
                decoded["total_spent_microcredits"],
                field_name="total_spent_microcredits",
                nonnegative=True,
            )
            if earned - spent != balance:
                raise MutationOutcomeUnknown("stored balance equation is invalid")
            last_sequence = _parse_stored_int64(
                decoded["last_sequence"],
                field_name="last_sequence",
                nonnegative=True,
            )
            if last_sequence == 0:
                raise MutationOutcomeUnknown("stored balance sequence is invalid")
            if str(UUID(decoded["last_transaction_id"])) != decoded["last_transaction_id"]:
                raise MutationOutcomeUnknown("stored balance transaction identity is invalid")
            updated_at = decoded["updated_at"]
            if not updated_at.endswith("Z"):
                raise MutationOutcomeUnknown("stored balance timestamp is invalid")
            parsed_updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if parsed_updated_at.utcoffset() != timezone.utc.utcoffset(parsed_updated_at):
                raise MutationOutcomeUnknown("stored balance timestamp is invalid")
            return {
                "type": decoded["agent_type"],
                "balance_microcredits": balance,
                "total_earned_microcredits": earned,
                "total_spent_microcredits": spent,
                "last_updated": decoded["updated_at"],
                "materialized": True,
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise MutationOutcomeUnknown("invalid stored balance state") from exc

    async def read_transactions(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LedgerTransaction]:
        """Read deterministic newest-first transactions from the active streams."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or offset > MAX_LEGACY_OFFSET
        ):
            raise ValueError(f"offset must be an integer from 0 to {MAX_LEGACY_OFFSET}")
        keyspace = EconomyKeyspace.for_tenant(tenant_id)
        stream = keyspace.agent_ledger(agent_id) if agent_id is not None else keyspace.tenant_ledger
        entries = await self._redis.xrevrange(stream, max="+", min="-", count=offset + limit)
        transactions: list[LedgerTransaction] = []
        for _, fields in entries[offset:]:
            normalized = {
                _decode_text(key, field_name="stream field"): _decode_text(
                    value, field_name="stream value"
                )
                for key, value in fields.items()
            }
            try:
                record_json = normalized["record_json"]
                payload = json.loads(record_json)
            except (KeyError, json.JSONDecodeError) as exc:
                raise MutationOutcomeUnknown("invalid ledger stream record") from exc
            if canonical_json_bytes(payload).decode("utf-8") != record_json:
                raise MutationOutcomeUnknown("non-canonical ledger stream record")
            transaction = _transaction_from_payload(payload)
            if normalized.get("sequence") != str(transaction.outbox_sequence):
                raise MutationOutcomeUnknown("ledger stream sequence mismatch")
            if normalized.get("transaction_id") != transaction.transaction_id:
                raise MutationOutcomeUnknown("ledger stream transaction mismatch")
            transactions.append(transaction)
        return transactions


def transaction_to_legacy_mapping(transaction: LedgerTransaction) -> dict[str, object]:
    """Convert exact internal values only at the legacy response boundary."""
    return {
        "id": transaction.transaction_id,
        "agent_id": transaction.agent_id,
        "type": transaction.operation.value,
        "amount": float(format_credit_amount(transaction.amount_microcredits)),
        "resource_type": transaction.resource_type,
        "mission_id": transaction.mission_id,
        "timestamp": transaction.created_at,
    }


class RedisLedgerCompatibilityFacade:
    """Opt-in legacy-shaped API backed exclusively by the atomic Redis ledger."""

    def __init__(self, ledger: RedisEconomyLedger) -> None:
        self._ledger = ledger

    async def charge(
        self,
        tenant_id: str,
        agent_id: str,
        amount: CreditInput,
        resource_type: str,
        *,
        idempotency_key: str,
        mission_id: str | None = None,
        agent_type: str = "unknown",
        reason: str = "resource_usage",
    ) -> dict[str, object]:
        result = await self._ledger.mutate(
            LedgerMutation(
                tenant_id=tenant_id,
                agent_id=agent_id,
                operation=LedgerOperation.CHARGE,
                amount_microcredits=parse_credit_amount(amount),
                resource_type=resource_type,
                reason=reason,
                idempotency_key=idempotency_key,
                mission_id=mission_id,
                agent_type=agent_type,
            )
        )
        return transaction_to_legacy_mapping(result.transaction)

    async def reward(
        self,
        tenant_id: str,
        agent_id: str,
        amount: CreditInput,
        resource_type: str,
        *,
        idempotency_key: str,
        mission_id: str | None = None,
        agent_type: str = "unknown",
        reason: str = "earned_reward",
    ) -> dict[str, object]:
        result = await self._ledger.mutate(
            LedgerMutation(
                tenant_id=tenant_id,
                agent_id=agent_id,
                operation=LedgerOperation.REWARD,
                amount_microcredits=parse_credit_amount(amount),
                resource_type=resource_type,
                reason=reason,
                idempotency_key=idempotency_key,
                mission_id=mission_id,
                agent_type=agent_type,
            )
        )
        return transaction_to_legacy_mapping(result.transaction)

    async def get_balance(self, tenant_id: str, agent_id: str) -> dict[str, object]:
        exact = await self._ledger.read_balance(tenant_id, agent_id)
        return {
            "type": exact["type"],
            "balance": float(format_credit_amount(exact["balance_microcredits"])),
            "total_earned": float(format_credit_amount(exact["total_earned_microcredits"])),
            "total_spent": float(format_credit_amount(exact["total_spent_microcredits"])),
            "last_updated": exact["last_updated"],
        }

    async def get_transactions(
        self,
        tenant_id: str,
        limit: int = 100,
        offset: int = 0,
        agent_id: str | None = None,
    ) -> list[dict[str, object]]:
        transactions = await self._ledger.read_transactions(
            tenant_id,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )
        return [transaction_to_legacy_mapping(transaction) for transaction in transactions]

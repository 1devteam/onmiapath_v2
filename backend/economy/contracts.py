"""Typed contracts for the v2 atomic economy ledger."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from backend.economy.amount import MAX_INT64, MIN_AMOUNT_MICROCREDITS
from backend.economy.keyspace import (
    LEDGER_SCHEMA_VERSION,
    idempotency_digest,
    normalize_idempotency_key,
    normalize_identifier,
)


MAX_RESOURCE_TYPE_BYTES = 64
MAX_REASON_BYTES = 128
MAX_AGENT_TYPE_BYTES = 64

_LABEL_PATTERN = re.compile(r"[a-z][a-z0-9_.:-]*\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class LedgerOperation(str, Enum):
    """Operations supported by the initial ledger schema."""

    CHARGE = "charge"
    REWARD = "reward"
    OPENING_GRANT = "opening_grant"


class MutationDisposition(str, Enum):
    """Successful atomic mutation outcomes."""

    COMMITTED = "committed"
    REPLAYED = "replayed"


class LuaResultCode(str, Enum):
    """Stable result codes returned by ``mutate_v1.lua``."""

    COMMITTED = "COMMITTED"
    REPLAYED = "REPLAYED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    ARCHIVE_LAG_LIMIT = "ARCHIVE_LAG_LIMIT"
    MIGRATION_LOCKED = "MIGRATION_LOCKED"
    ACCOUNT_QUARANTINED = "ACCOUNT_QUARANTINED"
    CORRUPT_STATE = "CORRUPT_STATE"
    INTEGER_OVERFLOW = "INTEGER_OVERFLOW"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"


class EconomyLedgerError(RuntimeError):
    """Base class for definite v2 ledger failures."""

    result_code: LuaResultCode | None = None

    def __init__(self, message: str = "economy ledger operation failed") -> None:
        super().__init__(message)


class IdempotencyConflict(EconomyLedgerError):
    result_code = LuaResultCode.IDEMPOTENCY_CONFLICT


class InsufficientFunds(EconomyLedgerError):
    result_code = LuaResultCode.INSUFFICIENT_FUNDS


class ArchiveLagLimit(EconomyLedgerError):
    result_code = LuaResultCode.ARCHIVE_LAG_LIMIT


class MigrationLocked(EconomyLedgerError):
    result_code = LuaResultCode.MIGRATION_LOCKED


class AccountQuarantined(EconomyLedgerError):
    result_code = LuaResultCode.ACCOUNT_QUARANTINED


class CorruptLedgerState(EconomyLedgerError):
    result_code = LuaResultCode.CORRUPT_STATE


class LedgerIntegerOverflow(EconomyLedgerError):
    result_code = LuaResultCode.INTEGER_OVERFLOW


class LedgerSchemaMismatch(EconomyLedgerError):
    result_code = LuaResultCode.SCHEMA_MISMATCH


class InvalidLedgerArgument(EconomyLedgerError):
    result_code = LuaResultCode.INVALID_ARGUMENT


class MutationOutcomeUnknown(EconomyLedgerError):
    """Raised when transport failure prevents determining commit outcome."""


_LUA_ERROR_TYPES: dict[LuaResultCode, type[EconomyLedgerError]] = {
    LuaResultCode.IDEMPOTENCY_CONFLICT: IdempotencyConflict,
    LuaResultCode.INSUFFICIENT_FUNDS: InsufficientFunds,
    LuaResultCode.ARCHIVE_LAG_LIMIT: ArchiveLagLimit,
    LuaResultCode.MIGRATION_LOCKED: MigrationLocked,
    LuaResultCode.ACCOUNT_QUARANTINED: AccountQuarantined,
    LuaResultCode.CORRUPT_STATE: CorruptLedgerState,
    LuaResultCode.INTEGER_OVERFLOW: LedgerIntegerOverflow,
    LuaResultCode.SCHEMA_MISMATCH: LedgerSchemaMismatch,
    LuaResultCode.INVALID_ARGUMENT: InvalidLedgerArgument,
}


def _normalize_label(value: str, *, field_name: str, maximum_bytes: int) -> str:
    """Validate a bounded machine-readable label."""
    if not isinstance(value, str):
        raise InvalidLedgerArgument(f"{field_name} must be a string")
    if not 1 <= len(value.encode("utf-8")) <= maximum_bytes:
        raise InvalidLedgerArgument(f"{field_name} must contain 1 to {maximum_bytes} UTF-8 bytes")
    if not _LABEL_PATTERN.fullmatch(value):
        raise InvalidLedgerArgument(
            f"{field_name} must start with a lowercase letter and contain only "
            "lowercase letters, digits, underscore, dot, colon, or hyphen"
        )
    return value


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    """Serialize the ledger's restricted JSON domain deterministically."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class LedgerMutation:
    """Canonical intent for one atomic ledger mutation."""

    tenant_id: str
    agent_id: str
    operation: LedgerOperation
    amount_microcredits: int
    resource_type: str
    reason: str
    idempotency_key: str
    mission_id: str | None = None
    agent_type: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            normalize_identifier(self.tenant_id, field_name="tenant_id"),
        )
        object.__setattr__(
            self,
            "agent_id",
            normalize_identifier(self.agent_id, field_name="agent_id"),
        )
        if not isinstance(self.operation, LedgerOperation):
            raise InvalidLedgerArgument("operation must be a LedgerOperation")
        if isinstance(self.amount_microcredits, bool) or not isinstance(
            self.amount_microcredits, int
        ):
            raise InvalidLedgerArgument("amount_microcredits must be an integer")
        if not MIN_AMOUNT_MICROCREDITS <= self.amount_microcredits <= MAX_INT64:
            raise InvalidLedgerArgument("amount_microcredits must be a positive int64")

        object.__setattr__(
            self,
            "resource_type",
            _normalize_label(
                self.resource_type,
                field_name="resource_type",
                maximum_bytes=MAX_RESOURCE_TYPE_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "reason",
            _normalize_label(
                self.reason,
                field_name="reason",
                maximum_bytes=MAX_REASON_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "agent_type",
            _normalize_label(
                self.agent_type,
                field_name="agent_type",
                maximum_bytes=MAX_AGENT_TYPE_BYTES,
            ),
        )
        object.__setattr__(self, "idempotency_key", normalize_idempotency_key(self.idempotency_key))
        if self.mission_id is not None:
            object.__setattr__(
                self,
                "mission_id",
                normalize_identifier(self.mission_id, field_name="mission_id"),
            )

    def canonical_request(self) -> dict[str, str | int | None]:
        """Return the exact economic request fields covered by idempotency."""
        return {
            "agent_id": self.agent_id,
            "amount_microcredits": self.amount_microcredits,
            "mission_id": self.mission_id,
            "operation": self.operation.value,
            "reason": self.reason,
            "resource_type": self.resource_type,
            "schema_version": LEDGER_SCHEMA_VERSION,
            "tenant_id": self.tenant_id,
        }

    @property
    def request_hash(self) -> str:
        """Return the SHA-256 hash of the canonical economic request."""
        return hashlib.sha256(canonical_json_bytes(self.canonical_request())).hexdigest()

    @property
    def idempotency_key_hash(self) -> str:
        """Return the storage-safe digest of the retry identity."""
        return idempotency_digest(self.idempotency_key)


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    """Immutable transaction representation shared by Redis and PostgreSQL."""

    transaction_id: str
    tenant_id: str
    agent_id: str
    operation: LedgerOperation
    amount_microcredits: int
    delta_microcredits: int
    balance_before_microcredits: int
    balance_after_microcredits: int
    resource_type: str
    reason: str
    mission_id: str | None
    idempotency_key_hash: str
    request_hash: str
    outbox_sequence: int
    created_at: datetime
    schema_version: int = LEDGER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            parsed_transaction_id = UUID(self.transaction_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise InvalidLedgerArgument("transaction_id must be a UUID string") from exc
        if str(parsed_transaction_id) != self.transaction_id:
            raise InvalidLedgerArgument("transaction_id must use canonical UUID text")

        object.__setattr__(
            self,
            "tenant_id",
            normalize_identifier(self.tenant_id, field_name="tenant_id"),
        )
        object.__setattr__(
            self,
            "agent_id",
            normalize_identifier(self.agent_id, field_name="agent_id"),
        )
        if not isinstance(self.operation, LedgerOperation):
            raise InvalidLedgerArgument("operation must be a LedgerOperation")
        for field_name in (
            "amount_microcredits",
            "delta_microcredits",
            "balance_before_microcredits",
            "balance_after_microcredits",
            "outbox_sequence",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidLedgerArgument(f"{field_name} must be an integer")
            if not -MAX_INT64 - 1 <= value <= MAX_INT64:
                raise InvalidLedgerArgument(f"{field_name} must fit signed int64")
        if self.amount_microcredits <= 0:
            raise InvalidLedgerArgument("amount_microcredits must be positive")
        if self.outbox_sequence <= 0:
            raise InvalidLedgerArgument("outbox_sequence must be positive")
        if abs(self.delta_microcredits) != self.amount_microcredits:
            raise InvalidLedgerArgument("delta magnitude must equal amount_microcredits")
        if self.operation is LedgerOperation.CHARGE and self.delta_microcredits >= 0:
            raise InvalidLedgerArgument("charge delta must be negative")
        if self.operation in (LedgerOperation.REWARD, LedgerOperation.OPENING_GRANT):
            if self.delta_microcredits <= 0:
                raise InvalidLedgerArgument("reward and opening grant deltas must be positive")
        if (
            self.operation is LedgerOperation.OPENING_GRANT
            and self.balance_before_microcredits != 0
        ):
            raise InvalidLedgerArgument("opening grant balance must begin at zero")
        if self.balance_before_microcredits + self.delta_microcredits != (
            self.balance_after_microcredits
        ):
            raise InvalidLedgerArgument("transaction balance equation is invalid")

        _normalize_label(
            self.resource_type,
            field_name="resource_type",
            maximum_bytes=MAX_RESOURCE_TYPE_BYTES,
        )
        _normalize_label(
            self.reason,
            field_name="reason",
            maximum_bytes=MAX_REASON_BYTES,
        )
        if self.mission_id is not None:
            object.__setattr__(
                self,
                "mission_id",
                normalize_identifier(self.mission_id, field_name="mission_id"),
            )
        if not _SHA256_PATTERN.fullmatch(self.idempotency_key_hash):
            raise InvalidLedgerArgument("idempotency_key_hash must be lowercase SHA-256 hex")
        if not _SHA256_PATTERN.fullmatch(self.request_hash):
            raise InvalidLedgerArgument("request_hash must be lowercase SHA-256 hex")
        if not isinstance(self.created_at, datetime):
            raise InvalidLedgerArgument("created_at must be a datetime")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise InvalidLedgerArgument("created_at must be timezone-aware")
        if self.created_at.utcoffset() != timezone.utc.utcoffset(self.created_at):
            raise InvalidLedgerArgument("created_at must use UTC")
        if self.schema_version != LEDGER_SCHEMA_VERSION:
            raise InvalidLedgerArgument("unsupported transaction schema_version")


@dataclass(frozen=True, slots=True)
class LedgerMutationResult:
    """Successful result returned by the atomic ledger."""

    disposition: MutationDisposition
    transaction: LedgerTransaction
    opening_transaction: LedgerTransaction | None
    balance_microcredits: int

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, MutationDisposition):
            raise InvalidLedgerArgument("disposition must be a MutationDisposition")
        if isinstance(self.balance_microcredits, bool) or not isinstance(
            self.balance_microcredits, int
        ):
            raise InvalidLedgerArgument("balance_microcredits must be an integer")
        if not -MAX_INT64 - 1 <= self.balance_microcredits <= MAX_INT64:
            raise InvalidLedgerArgument("balance_microcredits must fit signed int64")
        if self.transaction.balance_after_microcredits != self.balance_microcredits:
            raise InvalidLedgerArgument("result balance must match the transaction")
        if self.opening_transaction is not None:
            if self.opening_transaction.operation is not LedgerOperation.OPENING_GRANT:
                raise InvalidLedgerArgument("opening_transaction must be an opening grant")
            if (
                self.opening_transaction.tenant_id != self.transaction.tenant_id
                or self.opening_transaction.agent_id != self.transaction.agent_id
            ):
                raise InvalidLedgerArgument("opening transaction identity must match mutation")


def exception_for_lua_result(code: LuaResultCode | str, reason: str) -> EconomyLedgerError:
    """Map a definite Lua rejection to its typed Python exception."""
    try:
        result_code = code if isinstance(code, LuaResultCode) else LuaResultCode(code)
    except ValueError as exc:
        raise InvalidLedgerArgument("unknown Lua result code") from exc

    if result_code in (LuaResultCode.COMMITTED, LuaResultCode.REPLAYED):
        raise InvalidLedgerArgument("successful Lua result codes do not map to exceptions")
    error_type = _LUA_ERROR_TYPES[result_code]
    try:
        safe_reason = _normalize_label(
            reason,
            field_name="rejection_reason",
            maximum_bytes=128,
        )
    except InvalidLedgerArgument:
        safe_reason = "ledger_rejected"
    return error_type(safe_reason)

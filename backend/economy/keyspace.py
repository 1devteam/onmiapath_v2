"""Canonical identifiers, hashes, and Redis keys for the v2 economy ledger."""

from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from dataclasses import dataclass


LEDGER_SCHEMA_VERSION = 1
LEDGER_KEYSPACE_VERSION = 2
MAX_IDENTIFIER_BYTES = 128
MIN_IDEMPOTENCY_KEY_BYTES = 16
MAX_IDEMPOTENCY_KEY_BYTES = 128
IDEMPOTENCY_LOG_PREFIX_LENGTH = 12

_FORBIDDEN_IDENTIFIER_CHARACTERS = frozenset(("/", "\\", "{", "}"))
_LOWER_HEX_64_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class InvalidEconomyIdentifier(ValueError):
    """Raised when an economy identifier violates the canonical contract."""


class InvalidIdempotencyKey(ValueError):
    """Raised when an idempotency key is malformed or outside its byte bounds."""


def _contains_control_character(value: str) -> bool:
    """Return whether text contains an ASCII control character or DEL."""
    return any(ord(character) <= 31 or ord(character) == 127 for character in value)


def normalize_identifier(value: str, *, field_name: str = "identifier") -> str:
    """Normalize and validate a tenant, agent, mission, or workflow identifier."""
    if not isinstance(value, str):
        raise InvalidEconomyIdentifier(f"{field_name} must be a string")

    normalized = unicodedata.normalize("NFC", value)
    if normalized != normalized.strip():
        raise InvalidEconomyIdentifier(
            f"{field_name} must not contain leading or trailing whitespace"
        )

    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidEconomyIdentifier(f"{field_name} must contain valid Unicode") from exc
    if not 1 <= len(encoded) <= MAX_IDENTIFIER_BYTES:
        raise InvalidEconomyIdentifier(
            f"{field_name} must contain 1 to {MAX_IDENTIFIER_BYTES} UTF-8 bytes"
        )
    if _contains_control_character(normalized):
        raise InvalidEconomyIdentifier(f"{field_name} must not contain control characters")
    if any(character in _FORBIDDEN_IDENTIFIER_CHARACTERS for character in normalized):
        raise InvalidEconomyIdentifier(f"{field_name} contains a forbidden delimiter")
    return normalized


def normalize_idempotency_key(value: str) -> str:
    """Normalize an opaque idempotency key while preserving case and punctuation."""
    if not isinstance(value, str):
        raise InvalidIdempotencyKey("idempotency key must be a string")

    normalized = unicodedata.normalize("NFC", value)
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidIdempotencyKey("idempotency key must contain valid Unicode") from exc
    if not MIN_IDEMPOTENCY_KEY_BYTES <= len(encoded) <= MAX_IDEMPOTENCY_KEY_BYTES:
        raise InvalidIdempotencyKey(
            "idempotency key must contain "
            f"{MIN_IDEMPOTENCY_KEY_BYTES} to {MAX_IDEMPOTENCY_KEY_BYTES} UTF-8 bytes"
        )
    if _contains_control_character(normalized):
        raise InvalidIdempotencyKey("idempotency key must not contain control characters")
    return normalized


def encode_key_component(value: str, *, field_name: str = "identifier") -> str:
    """Encode a canonical identifier as unpadded URL-safe base64."""
    normalized = normalize_identifier(value, field_name=field_name)
    return base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")


def decode_key_component(value: str, *, field_name: str = "identifier") -> str:
    """Decode and revalidate an unpadded URL-safe base64 key component."""
    if not isinstance(value, str) or not value:
        raise InvalidEconomyIdentifier(f"encoded {field_name} must be a non-empty string")
    if any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in value
    ):
        raise InvalidEconomyIdentifier(f"encoded {field_name} is not unpadded base64url")

    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidEconomyIdentifier(f"encoded {field_name} is invalid") from exc

    normalized = normalize_identifier(decoded, field_name=field_name)
    if encode_key_component(normalized, field_name=field_name) != value:
        raise InvalidEconomyIdentifier(f"encoded {field_name} is not canonical")
    return normalized


def idempotency_digest(value: str) -> str:
    """Return the lowercase SHA-256 digest of a normalized idempotency key."""
    normalized = normalize_idempotency_key(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def redacted_idempotency_reference(value: str) -> str:
    """Return a non-secret correlation prefix for logs and traces."""
    return idempotency_digest(value)[:IDEMPOTENCY_LOG_PREFIX_LENGTH]


def validate_idempotency_digest(value: str) -> str:
    """Validate a previously computed lowercase SHA-256 idempotency digest."""
    if not isinstance(value, str) or not _LOWER_HEX_64_PATTERN.fullmatch(value):
        raise InvalidIdempotencyKey("idempotency digest must be 64 lowercase hex characters")
    return value


@dataclass(frozen=True, slots=True)
class EconomyKeyspace:
    """Construct all tenant-co-located Redis v2 economy keys."""

    tenant_id: str
    tenant_token: str

    def __post_init__(self) -> None:
        normalized = normalize_identifier(self.tenant_id, field_name="tenant_id")
        expected_token = encode_key_component(normalized, field_name="tenant_id")
        if self.tenant_id != normalized or self.tenant_token != expected_token:
            raise InvalidEconomyIdentifier("economy keyspace must use canonical tenant values")

    @classmethod
    def for_tenant(cls, tenant_id: str) -> "EconomyKeyspace":
        """Create a keyspace after canonicalizing a tenant identifier."""
        normalized = normalize_identifier(tenant_id, field_name="tenant_id")
        return cls(
            tenant_id=normalized,
            tenant_token=encode_key_component(normalized, field_name="tenant_id"),
        )

    @property
    def hash_tag(self) -> str:
        """Return the Redis Cluster hash tag shared by every tenant key."""
        return f"{{econ:{self.tenant_token}}}"

    @property
    def prefix(self) -> str:
        """Return the versioned tenant key prefix."""
        return f"op:econ:v{LEDGER_KEYSPACE_VERSION}:{self.hash_tag}"

    @property
    def meta(self) -> str:
        return f"{self.prefix}:meta"

    @property
    def agents(self) -> str:
        return f"{self.prefix}:agents"

    def balance(self, agent_id: str) -> str:
        agent_token = encode_key_component(agent_id, field_name="agent_id")
        return f"{self.prefix}:balance:{agent_token}"

    @property
    def tenant_ledger(self) -> str:
        return f"{self.prefix}:ledger"

    def agent_ledger(self, agent_id: str) -> str:
        agent_token = encode_key_component(agent_id, field_name="agent_id")
        return f"{self.prefix}:agent-ledger:{agent_token}"

    @property
    def outbox(self) -> str:
        return f"{self.prefix}:outbox"

    def idempotency(self, idempotency_key: str) -> str:
        return self.idempotency_from_digest(idempotency_digest(idempotency_key))

    def idempotency_from_digest(self, digest: str) -> str:
        return f"{self.prefix}:idem:{validate_idempotency_digest(digest)}"

    @property
    def quarantine(self) -> str:
        return f"{self.prefix}:quarantine"

    @property
    def migration_lock(self) -> str:
        return f"{self.prefix}:migration-lock"

    def mutation_keys(self, agent_id: str, idempotency_key: str) -> tuple[str, ...]:
        """Return Lua ``KEYS`` in the approved ABI order."""
        return (
            self.meta,
            self.agents,
            self.balance(agent_id),
            self.tenant_ledger,
            self.agent_ledger(agent_id),
            self.outbox,
            self.idempotency(idempotency_key),
            self.quarantine,
            self.migration_lock,
        )

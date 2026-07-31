"""Unit tests for exact economy amounts, contracts, and Redis key construction."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from backend.economy.amount import (
    MAX_INT64,
    MIN_INT64,
    InvalidCreditAmount,
    format_credit_amount,
    parse_credit_amount,
)
from backend.economy.contracts import (
    AccountQuarantined,
    ArchiveLagLimit,
    CorruptLedgerState,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidLedgerArgument,
    LedgerIntegerOverflow,
    LedgerMutation,
    LedgerMutationResult,
    LedgerOperation,
    LedgerSchemaMismatch,
    LedgerTransaction,
    LuaResultCode,
    MigrationLocked,
    MutationDisposition,
    exception_for_lua_result,
)
from backend.economy.keyspace import (
    EconomyKeyspace,
    InvalidEconomyIdentifier,
    InvalidIdempotencyKey,
    decode_key_component,
    encode_key_component,
    idempotency_digest,
    normalize_idempotency_key,
    normalize_identifier,
    redacted_idempotency_reference,
    validate_idempotency_digest,
)


@pytest.mark.unit
@pytest.mark.economy
class TestCreditAmounts:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0.000001", 1),
            ("1", 1_000_000),
            ("1.0", 1_000_000),
            ("1.234567", 1_234_567),
            (1, 1_000_000),
            (Decimal("12.340000"), 12_340_000),
            (Decimal("1E+3"), 1_000_000_000),
            (Decimal(MAX_INT64) / Decimal(1_000_000), MAX_INT64),
        ],
    )
    def test_parse_credit_amount_accepts_exact_values(self, value, expected):
        assert parse_credit_amount(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            True,
            False,
            0,
            -1,
            "0",
            "0.000000",
            "-1",
            "+1",
            " 1",
            "1 ",
            "01",
            ".5",
            "1.",
            "1e3",
            "1_000",
            "NaN",
            "Infinity",
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
            Decimal("0"),
            Decimal("-0.000001"),
            Decimal("0.0000001"),
            Decimal("1.0000000"),
            "0.0000001",
            "9" * 10_000,
            1.5,
            None,
            object(),
            Decimal(MAX_INT64 + 1) / Decimal(1_000_000),
        ],
    )
    def test_parse_credit_amount_rejects_invalid_values(self, value):
        with pytest.raises(InvalidCreditAmount):
            parse_credit_amount(value)

    @pytest.mark.parametrize(
        ("microcredits", "expected"),
        [
            (0, Decimal("0.000000")),
            (1, Decimal("0.000001")),
            (-1, Decimal("-0.000001")),
            (1_234_567, Decimal("1.234567")),
            (MAX_INT64, Decimal("9223372036854.775807")),
            (MIN_INT64, Decimal("-9223372036854.775808")),
        ],
    )
    def test_format_credit_amount_is_exact(self, microcredits, expected):
        assert format_credit_amount(microcredits) == expected

    def test_amount_conversion_does_not_depend_on_caller_decimal_precision(self):
        with localcontext() as context:
            context.prec = 6
            assert parse_credit_amount(Decimal("9223372036854.775807")) == MAX_INT64
            assert format_credit_amount(MAX_INT64) == Decimal("9223372036854.775807")

    @pytest.mark.parametrize("value", [True, 1.0, "1", None, MAX_INT64 + 1, MIN_INT64 - 1])
    def test_format_credit_amount_rejects_invalid_microcredits(self, value):
        with pytest.raises(InvalidCreditAmount):
            format_credit_amount(value)


@pytest.mark.unit
@pytest.mark.economy
class TestCanonicalIdentifiers:
    @pytest.mark.parametrize("value", ["tenant-1", "Agent.Name:1", "é", "内部"])
    def test_identifier_accepts_safe_unicode_and_preserves_case(self, value):
        assert normalize_identifier(value) == value

    def test_identifier_normalizes_unicode_to_nfc(self):
        assert normalize_identifier("e\u0301") == "é"

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " leading",
            "trailing ",
            "\ttenant",
            "tenant\n",
            "tenant/name",
            "tenant\\name",
            "tenant{name}",
            "tenant}name",
            "a" * 129,
            "\ud800",
            None,
            123,
        ],
    )
    def test_identifier_rejects_unsafe_values(self, value):
        with pytest.raises(InvalidEconomyIdentifier):
            normalize_identifier(value)

    def test_identifier_byte_limit_applies_after_utf8_encoding(self):
        assert normalize_identifier("é" * 64) == "é" * 64
        with pytest.raises(InvalidEconomyIdentifier):
            normalize_identifier("é" * 65)

    @pytest.mark.parametrize("value", ["tenant-1", "é", "内部", "CaseSensitive"])
    def test_key_component_round_trip_is_canonical(self, value):
        encoded = encode_key_component(value)
        assert "=" not in encoded
        assert decode_key_component(encoded) == normalize_identifier(value)

    @pytest.mark.parametrize("value", ["", "%%%", "Zg=", "____", None])
    def test_key_component_decode_rejects_invalid_or_noncanonical_values(self, value):
        with pytest.raises(InvalidEconomyIdentifier):
            decode_key_component(value)


@pytest.mark.unit
@pytest.mark.economy
class TestIdempotencyKeys:
    def test_idempotency_key_normalizes_unicode_before_hashing(self):
        composed = "mission:é:attempt:1"
        decomposed = "mission:e\u0301:attempt:1"
        assert normalize_idempotency_key(decomposed) == composed
        assert idempotency_digest(decomposed) == idempotency_digest(composed)

    def test_digest_matches_known_sha256_and_redacts_full_key(self):
        key = "mission:123:step:1"
        digest = idempotency_digest(key)
        assert digest == "8a4580d88e07196aed0d6fc452b2fa6fdcc6509ecf455d58d7cae6158d85e7ef"
        assert redacted_idempotency_reference(key) == digest[:12]
        assert key not in redacted_idempotency_reference(key)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "short",
            "a" * 15,
            "a" * 129,
            "valid-key-length\n",
            "valid-key-value-\ud800",
            None,
            123,
        ],
    )
    def test_idempotency_key_rejects_invalid_values(self, value):
        with pytest.raises(InvalidIdempotencyKey):
            normalize_idempotency_key(value)

    def test_idempotency_key_accepts_opaque_delimiters_because_storage_is_hashed(self):
        key = "tenant/{opaque}\\retry key"
        assert normalize_idempotency_key(key) == key
        assert len(idempotency_digest(key)) == 64

    def test_validate_digest_requires_lowercase_sha256(self):
        digest = "a" * 64
        assert validate_idempotency_digest(digest) == digest
        for invalid in ("a" * 63, "A" * 64, "g" * 64, None):
            with pytest.raises(InvalidIdempotencyKey):
                validate_idempotency_digest(invalid)


@pytest.mark.unit
@pytest.mark.economy
class TestEconomyKeyspace:
    def test_exact_keys_match_the_approved_v2_schema(self):
        keyspace = EconomyKeyspace.for_tenant("tenant-1")
        agent_token = encode_key_component("agent-1")
        digest = idempotency_digest("mission:123:step:1")
        prefix = f"op:econ:v2:{{econ:{encode_key_component('tenant-1')}}}"

        assert keyspace.prefix == prefix
        assert keyspace.meta == f"{prefix}:meta"
        assert keyspace.agents == f"{prefix}:agents"
        assert keyspace.balance("agent-1") == f"{prefix}:balance:{agent_token}"
        assert keyspace.tenant_ledger == f"{prefix}:ledger"
        assert keyspace.agent_ledger("agent-1") == f"{prefix}:agent-ledger:{agent_token}"
        assert keyspace.outbox == f"{prefix}:outbox"
        assert keyspace.idempotency_from_digest(digest) == f"{prefix}:idem:{digest}"
        assert keyspace.quarantine == f"{prefix}:quarantine"
        assert keyspace.migration_lock == f"{prefix}:migration-lock"

    def test_mutation_keys_use_exact_lua_abi_order_and_one_hash_tag(self):
        keyspace = EconomyKeyspace.for_tenant("tenant-1")
        keys = keyspace.mutation_keys("agent-1", "mission:123:step:1")
        assert len(keys) == 9
        assert keys == (
            keyspace.meta,
            keyspace.agents,
            keyspace.balance("agent-1"),
            keyspace.tenant_ledger,
            keyspace.agent_ledger("agent-1"),
            keyspace.outbox,
            keyspace.idempotency("mission:123:step:1"),
            keyspace.quarantine,
            keyspace.migration_lock,
        )
        assert all(key.count(keyspace.hash_tag) == 1 for key in keys)

    def test_tenants_and_agents_cannot_collide_or_inject_hash_tags(self):
        first = EconomyKeyspace.for_tenant("tenant-a")
        second = EconomyKeyspace.for_tenant("tenant-b")
        assert first.hash_tag != second.hash_tag
        assert first.balance("agent:a") != first.balance("agent-a")
        with pytest.raises(InvalidEconomyIdentifier):
            EconomyKeyspace.for_tenant("tenant}{injected")

    def test_keyspace_constructor_cannot_bypass_canonicalization(self):
        with pytest.raises(InvalidEconomyIdentifier):
            EconomyKeyspace(tenant_id="tenant-1", tenant_token="injected")
        with pytest.raises(InvalidEconomyIdentifier):
            EconomyKeyspace(
                tenant_id="e\u0301",
                tenant_token=encode_key_component("é"),
            )

    def test_raw_identifiers_and_idempotency_key_do_not_appear_in_keys(self):
        tenant_id = "tenant-sensitive"
        agent_id = "agent-sensitive"
        idempotency_key = "mission:sensitive:step:1"
        keys = EconomyKeyspace.for_tenant(tenant_id).mutation_keys(
            agent_id,
            idempotency_key,
        )
        assert all(tenant_id not in key for key in keys)
        assert all(agent_id not in key for key in keys)
        assert all(idempotency_key not in key for key in keys)


def _mutation(**overrides) -> LedgerMutation:
    values = {
        "tenant_id": "tenant-1",
        "agent_id": "agent-1",
        "operation": LedgerOperation.CHARGE,
        "amount_microcredits": 1_500_000,
        "resource_type": "llm_call",
        "reason": "resource_usage",
        "idempotency_key": "mission:123:step:1",
        "mission_id": "mission-123",
        "agent_type": "executor",
    }
    values.update(overrides)
    return LedgerMutation(**values)


def _transaction(**overrides) -> LedgerTransaction:
    values = {
        "transaction_id": str(uuid4()),
        "tenant_id": "tenant-1",
        "agent_id": "agent-1",
        "operation": LedgerOperation.CHARGE,
        "amount_microcredits": 1_500_000,
        "delta_microcredits": -1_500_000,
        "balance_before_microcredits": 10_000_000,
        "balance_after_microcredits": 8_500_000,
        "resource_type": "llm_call",
        "reason": "resource_usage",
        "mission_id": "mission-123",
        "idempotency_key_hash": "a" * 64,
        "request_hash": "b" * 64,
        "outbox_sequence": 2,
        "created_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return LedgerTransaction(**values)


@pytest.mark.unit
@pytest.mark.economy
class TestLedgerContracts:
    def test_mutation_is_immutable_and_hashes_only_economic_fields(self):
        mutation = _mutation()
        with pytest.raises(FrozenInstanceError):
            mutation.amount_microcredits = 2_000_000

        assert mutation.idempotency_key not in mutation.canonical_request().values()
        assert len(mutation.request_hash) == 64
        assert len(mutation.idempotency_key_hash) == 64

    def test_request_hash_is_stable_and_excludes_agent_display_type(self):
        first = _mutation(agent_type="executor")
        second = _mutation(agent_type="commander")
        assert first.request_hash == second.request_hash

    @pytest.mark.parametrize(
        "override",
        [
            {"tenant_id": "tenant-2"},
            {"agent_id": "agent-2"},
            {"operation": LedgerOperation.REWARD},
            {"amount_microcredits": 1_500_001},
            {"resource_type": "compute"},
            {"reason": "mission_cost"},
            {"mission_id": None},
        ],
    )
    def test_request_hash_changes_for_every_economic_field(self, override):
        assert _mutation().request_hash != _mutation(**override).request_hash

    @pytest.mark.parametrize(
        "override",
        [
            {"operation": "charge"},
            {"amount_microcredits": True},
            {"amount_microcredits": 0},
            {"amount_microcredits": MAX_INT64 + 1},
            {"resource_type": "LLM CALL"},
            {"reason": ""},
            {"agent_type": "Executor"},
            {"idempotency_key": "short"},
            {"mission_id": "mission/{bad}"},
        ],
    )
    def test_mutation_rejects_invalid_fields(self, override):
        with pytest.raises(
            (InvalidLedgerArgument, InvalidIdempotencyKey, InvalidEconomyIdentifier)
        ):
            _mutation(**override)

    def test_transaction_validates_balance_equation_and_utc(self):
        transaction = _transaction()
        assert transaction.balance_before_microcredits + transaction.delta_microcredits == (
            transaction.balance_after_microcredits
        )

        with pytest.raises(InvalidLedgerArgument, match="balance equation"):
            _transaction(balance_after_microcredits=1)
        with pytest.raises(InvalidLedgerArgument, match="timezone-aware"):
            _transaction(created_at=datetime.now())
        with pytest.raises(InvalidLedgerArgument, match="use UTC"):
            _transaction(created_at=datetime.now(timezone(timedelta(hours=-5))))

    @pytest.mark.parametrize(
        "override",
        [
            {"transaction_id": "not-a-uuid"},
            {"transaction_id": str(uuid4()).upper()},
            {"amount_microcredits": 0},
            {"outbox_sequence": 0},
            {"idempotency_key_hash": "A" * 64},
            {"request_hash": "short"},
            {"schema_version": 2},
            {"delta_microcredits": True},
            {"delta_microcredits": 1_500_000},
            {"delta_microcredits": -1},
            {"operation": LedgerOperation.REWARD},
            {
                "operation": LedgerOperation.OPENING_GRANT,
                "delta_microcredits": 1_500_000,
                "balance_before_microcredits": 1,
                "balance_after_microcredits": 1_500_001,
            },
            {"created_at": None},
        ],
    )
    def test_transaction_rejects_invalid_fields(self, override):
        with pytest.raises(InvalidLedgerArgument):
            _transaction(**override)

    def test_mutation_result_requires_matching_balance_and_opening_identity(self):
        transaction = _transaction()
        result = LedgerMutationResult(
            disposition=MutationDisposition.COMMITTED,
            transaction=transaction,
            opening_transaction=None,
            balance_microcredits=transaction.balance_after_microcredits,
        )
        assert result.transaction is transaction

        with pytest.raises(InvalidLedgerArgument, match="result balance"):
            LedgerMutationResult(
                disposition=MutationDisposition.COMMITTED,
                transaction=transaction,
                opening_transaction=None,
                balance_microcredits=0,
            )

        wrong_opening = _transaction(
            agent_id="agent-2",
            operation=LedgerOperation.OPENING_GRANT,
            amount_microcredits=1_000_000_000,
            delta_microcredits=1_000_000_000,
            balance_before_microcredits=0,
            balance_after_microcredits=1_000_000_000,
            outbox_sequence=1,
        )
        with pytest.raises(InvalidLedgerArgument, match="identity"):
            LedgerMutationResult(
                disposition=MutationDisposition.COMMITTED,
                transaction=transaction,
                opening_transaction=wrong_opening,
                balance_microcredits=transaction.balance_after_microcredits,
            )

    @pytest.mark.parametrize(
        ("code", "exception_type"),
        [
            (LuaResultCode.IDEMPOTENCY_CONFLICT, IdempotencyConflict),
            (LuaResultCode.INSUFFICIENT_FUNDS, InsufficientFunds),
            (LuaResultCode.ARCHIVE_LAG_LIMIT, ArchiveLagLimit),
            (LuaResultCode.MIGRATION_LOCKED, MigrationLocked),
            (LuaResultCode.ACCOUNT_QUARANTINED, AccountQuarantined),
            (LuaResultCode.CORRUPT_STATE, CorruptLedgerState),
            (LuaResultCode.INTEGER_OVERFLOW, LedgerIntegerOverflow),
            (LuaResultCode.SCHEMA_MISMATCH, LedgerSchemaMismatch),
            (LuaResultCode.INVALID_ARGUMENT, InvalidLedgerArgument),
        ],
    )
    def test_lua_rejections_map_to_typed_exceptions(self, code, exception_type):
        error = exception_for_lua_result(code, "bounded_reason")
        assert isinstance(error, exception_type)
        assert error.result_code is code
        assert str(error) == "bounded_reason"

    def test_lua_rejection_reason_is_bounded_and_machine_readable(self):
        assert str(exception_for_lua_result(LuaResultCode.CORRUPT_STATE, "bad\nsecret")) == (
            "ledger_rejected"
        )
        assert str(exception_for_lua_result(LuaResultCode.CORRUPT_STATE, "x" * 129)) == (
            "ledger_rejected"
        )

    @pytest.mark.parametrize("code", [LuaResultCode.COMMITTED, LuaResultCode.REPLAYED, "UNKNOWN"])
    def test_successful_or_unknown_lua_codes_do_not_map_to_rejection(self, code):
        with pytest.raises(InvalidLedgerArgument):
            exception_for_lua_result(code, "reason")

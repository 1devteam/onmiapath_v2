"""Regression tests for selectively forward-ported v7.5 contract requirements."""

import math

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.routes.economy import _normalize_balance_payload
from backend.config.settings import Settings


@pytest.mark.unit
@pytest.mark.economy
class TestEconomyBalanceNormalization:
    """Validate marketplace payloads without trusting embedded identifiers."""

    def test_structured_payload_uses_canonical_map_key(self):
        normalized = _normalize_balance_payload(
            "canonical-agent",
            {"agent_id": "untrusted-agent", "balance": 125.5},
        )

        assert normalized == {
            "agent_id": "canonical-agent",
            "balance": 125.5,
        }

    def test_legacy_numeric_payload_is_supported(self):
        assert _normalize_balance_payload("legacy-agent", 42) == {
            "agent_id": "legacy-agent",
            "balance": 42.0,
        }

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"balance": "100.0"},
            {"balance": True},
            {"balance": math.nan},
            {"balance": math.inf},
            None,
        ],
    )
    def test_invalid_payload_is_rejected_without_echoing_value(self, payload):
        with pytest.raises(HTTPException) as exc_info:
            _normalize_balance_payload("agent-1", payload)

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Invalid marketplace balance payload"


@pytest.mark.unit
class TestProductionSettings:
    """Production configuration must be explicit and non-loopback."""

    @pytest.fixture(autouse=True)
    def clear_process_feature_flag_overrides(self, monkeypatch):
        """Keep process-level CI isolation flags from masking settings cases."""
        monkeypatch.delenv("NATS_ENABLED", raising=False)
        monkeypatch.delenv("OTEL_ENABLED", raising=False)

    @staticmethod
    def production_settings(**overrides):
        values = {
            "_env_file": None,
            "ENVIRONMENT": "production",
            "SECRET_KEY": "s" * 48,
            "JWT_SECRET_KEY": "j" * 48,
            "DATABASE_URL": "postgresql://user:password@postgres:5432/omnipath",
            "REDIS_URL": "redis://:password@redis:6379/0",
            "NATS_URL": "nats://user:password@nats:4222",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4317",
        }
        values.update(overrides)
        return Settings(**values)

    def test_service_dns_configuration_is_accepted(self):
        settings = self.production_settings()

        assert settings.ENVIRONMENT == "production"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("DATABASE_URL", "postgresql://user:password@localhost:5432/omnipath"),
            ("REDIS_URL", "redis://:password@127.0.0.1:6379/0"),
            ("NATS_URL", "nats://user:password@[::1]:4222"),
            ("OTEL_EXPORTER_OTLP_ENDPOINT", "http://0.0.0.0:4317"),
        ],
    )
    def test_loopback_service_urls_are_rejected(self, field, value):
        with pytest.raises(ValidationError, match=field):
            self.production_settings(**{field: value})

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("SECRET_KEY", "CHANGE_ME_run_openssl_rand_hex_32"),
            ("JWT_SECRET_KEY", "your-secret-key-here"),
            ("SECRET_KEY", "short"),
        ],
    )
    def test_missing_placeholder_or_weak_secrets_are_rejected(self, field, value):
        with pytest.raises(ValidationError, match=field):
            self.production_settings(**{field: value})

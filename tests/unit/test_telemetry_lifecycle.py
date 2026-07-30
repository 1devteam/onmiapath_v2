"""Regression tests for OpenTelemetry initialization lifecycle."""

from unittest.mock import Mock

from backend.integrations.observability import telemetry as telemetry_module


def test_get_telemetry_can_defer_initialization(monkeypatch):
    """Application startup can apply settings before creating providers."""
    manager = Mock()
    manager._initialized = False
    monkeypatch.setattr(telemetry_module, "telemetry", manager)

    result = telemetry_module.get_telemetry(initialize=False)

    assert result is manager
    manager.initialize.assert_not_called()


def test_initialize_is_idempotent():
    """Repeated startup hooks must not replace global telemetry providers."""
    manager = telemetry_module.TelemetryManager(enabled=False)

    manager.initialize()
    manager.enabled = True
    manager.initialize()

    assert manager._initialized is True
    assert manager.tracer_provider is None
    assert manager.meter_provider is None


def test_otlp_metrics_are_disabled_by_default():
    """Jaeger trace endpoints must not receive unsupported OTLP metrics."""
    manager = telemetry_module.TelemetryManager(enabled=False)

    assert manager.export_metrics is False


def test_tracer_and_meter_access_do_not_initialize_manager(monkeypatch):
    """Module-level instruments must not lock in default exporter settings."""
    manager = Mock()
    manager._initialized = False
    monkeypatch.setattr(telemetry_module, "telemetry", manager)

    tracer = telemetry_module.get_tracer("tests.telemetry")
    meter = telemetry_module.get_meter("tests.telemetry")

    assert tracer is not None
    assert meter is not None
    manager.initialize.assert_not_called()

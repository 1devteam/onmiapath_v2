import pytest
from datetime import datetime
from fastapi import HTTPException
from backend.api.routes.economy import _normalize_balance_payload


def test_normalize_enforces_canonical_id():
    """Test that the agent_id from the map key is used even if payload has a different one."""
    agent_id = "canonical-id"
    payload = {
        "agent_id": "mismatched-id",
        "type": "test",
        "balance": 100.0,
        "total_earned": 50.0,
        "total_spent": 50.0,
    }

    normalized = _normalize_balance_payload(agent_id, payload)
    assert normalized["agent_id"] == "canonical-id"
    assert normalized["balance"] == 100.0


def test_normalize_rejects_incomplete_dict():
    """Test that incomplete dictionaries raise an HTTPException."""
    agent_id = "test-agent"
    # Missing 'total_spent'
    payload = {"balance": 100.0, "total_earned": 50.0}

    with pytest.raises(HTTPException) as excinfo:
        _normalize_balance_payload(agent_id, payload)
    assert excinfo.value.status_code == 500
    assert "Incomplete balance data" in excinfo.value.detail


def test_normalize_supports_legacy_float():
    """Test that legacy float values are still supported."""
    agent_id = "legacy-agent"
    payload = 42.0

    normalized = _normalize_balance_payload(agent_id, payload)
    assert normalized["agent_id"] == "legacy-agent"
    assert normalized["balance"] == 42.0
    assert normalized["total_earned"] == 0.0
    assert normalized["total_spent"] == 0.0


def test_normalize_valid_dict():
    """Test that a complete dictionary is correctly normalized."""
    agent_id = "valid-agent"
    now = datetime.utcnow()
    payload = {
        "type": "worker",
        "balance": 500.0,
        "total_earned": 1000.0,
        "total_spent": 500.0,
        "last_updated": now,
    }

    normalized = _normalize_balance_payload(agent_id, payload)
    assert normalized["agent_id"] == "valid-agent"
    assert normalized["type"] == "worker"
    assert normalized["balance"] == 500.0
    assert normalized["total_earned"] == 1000.0
    assert normalized["total_spent"] == 500.0
    assert normalized["last_updated"] == now

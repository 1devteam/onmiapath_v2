import pytest
from fastapi.testclient import TestClient

from backend.api.routes import economy


@pytest.mark.integration
class TestPR1Regressions:
    def test_economy_auth_accepts_bearer_header(self, client: TestClient):
        response = client.get(
            "/api/v1/economy/balance",
            headers={"Authorization": "Bearer admin-token"},
        )

        assert response.status_code == 200

    def test_balance_endpoint_handles_legacy_float_payload(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def mock_get_balance(tenant_id: str, agent_id: str):
            return 42.0

        monkeypatch.setattr(economy.marketplace, "get_balance", mock_get_balance)

        response = client.get(
            "/api/v1/economy/balance/agent-legacy",
            headers={"Authorization": "Bearer admin-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == "agent-legacy"
        assert data["balance"] == 42.0
        assert data["agent_type"] == "unknown"
        assert data["total_earned"] == 0.0
        assert data["total_spent"] == 0.0

    def test_balance_endpoint_rejects_incomplete_dict_payload(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def mock_get_balance(tenant_id: str, agent_id: str):
            return {
                "agent_id": agent_id,
                "type": "planner",
                "balance": 100.0,
                # total_earned intentionally missing
                "total_spent": 25.0,
            }

        monkeypatch.setattr(economy.marketplace, "get_balance", mock_get_balance)

        response = client.get(
            "/api/v1/economy/balance/agent-incomplete",
            headers={"Authorization": "Bearer admin-token"},
        )

        assert response.status_code == 500
        assert "missing required field(s): total_earned" in response.json()["detail"]

    def test_metrics_and_performance_routes_are_registered(self, client: TestClient):
        metrics_response = client.get("/metrics")
        performance_response = client.get("/api/v1/performance/agents")

        assert metrics_response.status_code == 200
        assert performance_response.status_code != 404

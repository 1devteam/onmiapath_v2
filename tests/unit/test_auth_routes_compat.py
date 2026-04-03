from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.routes.auth as auth_routes
from backend.api.routes.auth import router
from backend.models.domain.user import User
from backend.security.auth_utils import get_current_user
from backend.models.domain.user import UserRole


@pytest.mark.unit
@pytest.mark.auth
def test_auth_router_uses_v1_auth_prefix():
    assert router.prefix == "/api/v1/auth"


@pytest.mark.unit
@pytest.mark.auth
def test_auth_router_exposes_login_and_token_endpoints():
    route_paths = {route.path for route in router.routes}

    assert "/api/v1/auth/token" in route_paths
    assert "/api/v1/auth/login" in route_paths


@pytest.mark.unit
@pytest.mark.auth
@pytest.mark.asyncio
async def test_admin_token_bypass_returns_valid_user_model():
    user = await get_current_user("admin-token")

    assert user.email == "admin@example.com"
    assert user.username == "admin"
    assert user.role == UserRole.ADMIN


@pytest.mark.unit
@pytest.mark.auth
def test_login_compat_accepts_json_payload(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    async def mock_authenticate_user(email: str, password: str):
        if email == "test@example.com" and password == "test-password":
            return User(
                id="user-1",
                email=email,
                username="test",
                tenant_id="tenant-1",
                role=UserRole.ADMIN,
                is_active=True,
                created_at=datetime.utcnow(),
            )
        return None

    monkeypatch.setattr(auth_routes, "authenticate_user", mock_authenticate_user)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "test-password"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

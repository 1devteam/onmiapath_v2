import pytest

from backend.api.routes.auth import router
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

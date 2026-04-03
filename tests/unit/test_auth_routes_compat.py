import pytest

from backend.api.routes.auth import router, AUTH_ROUTER_PREFIX
from backend.security.auth_utils import (
    get_current_user,
    ADMIN_BYPASS_USER_EMAIL,
    ADMIN_BYPASS_USER_USERNAME,
)
from backend.models.domain.user import UserRole


@pytest.mark.unit
@pytest.mark.auth
def test_auth_router_uses_v1_auth_prefix():
    assert router.prefix == AUTH_ROUTER_PREFIX


@pytest.mark.unit
@pytest.mark.auth
def test_auth_router_exposes_login_and_token_endpoints():
    route_paths = {route.path for route in router.routes}

    assert f"{AUTH_ROUTER_PREFIX}/token" in route_paths
    assert f"{AUTH_ROUTER_PREFIX}/login" in route_paths


@pytest.mark.unit
@pytest.mark.auth
@pytest.mark.asyncio
async def test_admin_token_bypass_returns_valid_user_model():
    user = await get_current_user("admin-token")

    assert user.email == ADMIN_BYPASS_USER_EMAIL
    assert user.username == ADMIN_BYPASS_USER_USERNAME
    assert user.id == "admin-id"
    assert user.tenant_id == "default-tenant"
    assert user.is_active is True
    assert user.role == UserRole.ADMIN

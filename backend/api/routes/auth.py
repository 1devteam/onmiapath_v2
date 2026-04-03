from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ValidationError
from backend.security.auth_utils import (
    create_access_token,
    authenticate_user,
    get_password_hash,
    create_user_in_db,
    get_user_from_db,
    UserCreate,
    User,
)
from datetime import timedelta

AUTH_ROUTER_PREFIX = "/api/v1/auth"

router = APIRouter(prefix=AUTH_ROUTER_PREFIX, tags=["auth"])


class LegacyLoginRequest(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    password: str


@router.post("/register", response_model=User)
async def register_user(user: UserCreate):
    existing_user = await get_user_from_db(user.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    hashed_password = get_password_hash(user.password)
    user_in_db = await create_user_in_db(user.email, hashed_password)
    return User(**user_in_db.dict())


def _build_access_token_response(user: User) -> dict:
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={
            "sub": user.email,
            "user_id": user.id,
            "tenant_id": user.tenant_id,
            "role": user.role.value,
        },
        expires_delta=access_token_expires,
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _build_access_token_response(user)


@router.post("/login")
async def login_compat(request: Request):
    """Backward-compatible alias for clients that call /api/v1/auth/login."""
    content_type = request.headers.get("content-type", "").lower()

    username: Optional[str] = None
    password: Optional[str] = None

    if "application/json" in content_type:
        try:
            payload = LegacyLoginRequest.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid login payload",
            ) from exc
        username = payload.username or payload.email
        password = payload.password
    else:
        form_data = await request.form()
        username = form_data.get("username") or form_data.get("email")
        password = form_data.get("password")

    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing login credentials",
        )

    user = await authenticate_user(username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _build_access_token_response(user)

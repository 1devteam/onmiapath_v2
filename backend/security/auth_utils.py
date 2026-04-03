from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr

from backend.config.settings import Settings
from backend.models.domain.user import User, UserInDB, UserRole

settings = Settings()
logger = logging.getLogger(__name__)

# passlib context for password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-memory store for users for personal build simplification
# In a production system, this would be a database
in_memory_users_db: Dict[str, Dict[str, Any]] = {}
security = HTTPBearer(auto_error=False)


class UserCreate(BaseModel):
    """Request model for user registration."""

    email: EmailStr
    password: str


def get_password_hash(password: str) -> str:
    """Hash a plain text password."""
    logger.info(f"Hashing password of length {len(password)} characters.")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


async def get_user_from_db(email: str) -> Optional[UserInDB]:
    """Retrieve a user from the in-memory database by email."""
    user_data = in_memory_users_db.get(email)
    if user_data:
        return UserInDB(**user_data)
    return None


async def create_user_in_db(email: str, hashed_password: str) -> UserInDB:
    """Create a new user in the in-memory database."""
    user_id = f"user-{len(in_memory_users_db) + 1}"
    tenant_id = (
        f"tenant-{len(in_memory_users_db) + 1}"  # Each user gets their own tenant for simplicity
    )
    username = email.split("@")[0]
    user_data = {
        "id": user_id,
        "email": email,
        "username": username,
        "tenant_id": tenant_id,
        "hashed_password": hashed_password,
        "role": UserRole.ADMIN,  # Default to ADMIN for personal build
        "is_active": True,
        "created_at": datetime.utcnow(),
    }
    in_memory_users_db[email] = user_data
    logger.info(f"User {email} created with tenant {tenant_id}")
    return UserInDB(**user_data)


async def authenticate_user(email: str, password: str) -> Optional[User]:
    """Authenticate a user and return the User model if successful."""
    user_in_db = await get_user_from_db(email)
    if not user_in_db or not verify_password(password, user_in_db.hashed_password):
        return None
    # Use model_dump() for Pydantic v2 compatibility if needed, or .dict() for v1
    return User(**user_in_db.dict())


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    """
    FastAPI dependency to get the current authenticated user from an Authorization header.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Bypass for local testing/personal build
    if token == "admin-token":
        return User(
            id="admin-id",
            email="admin@omnipath.com",
            username="admin",
            tenant_id="default-tenant",
            role=UserRole.ADMIN,
            is_active=True,
            created_at=datetime.utcnow(),
        )

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_email: str = payload.get("sub")
    tenant_id: str = payload.get("tenant_id")
    user_id: str = payload.get("user_id") or f"user-{user_email}"
    role: str = payload.get("role") or UserRole.ADMIN.value

    if user_email is None or tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required fields",
        )

    # For personal build, we reconstruct from token instead of DB fetch
    return User(
        id=user_id,
        email=user_email,
        username=user_email.split("@")[0],
        tenant_id=tenant_id,
        role=UserRole(role),
        is_active=True,
        created_at=datetime.utcnow(),
    )

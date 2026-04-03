from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Query, HTTPException, status

from backend.config.settings import Settings
from backend.models.domain.user import User, UserInDB, UserRole

settings = Settings()
logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-memory store for users for personal build simplification
# In a production system, this would be a database
in_memory_users_db: Dict[str, Dict[str, Any]] = {}


def get_password_hash(password: str) -> str:
    logger.info(f"Hashing password of length {len(password)} characters.")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


async def get_user_from_db(email: str) -> Optional[UserInDB]:
    user_data = in_memory_users_db.get(email)
    if user_data:
        return UserInDB(**user_data)
    return None


async def create_user_in_db(email: str, hashed_password: str) -> UserInDB:
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
    user_in_db = await get_user_from_db(email)
    if not user_in_db or not verify_password(password, user_in_db.hashed_password):
        return None
    return User(**user_in_db.dict())


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(token: str = Query(...)) -> User:
    # Bypass for local testing/personal build
    if token == "admin-token":
        return User(
            id="admin-id",
            email="admin@omnipath.local",
            username="admin",
            tenant_id="default-tenant",
            role=UserRole.ADMIN,
            is_active=True,
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

    if user_email is None or tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required fields",
        )

    # For personal build, we don't re-fetch from DB, just reconstruct from token
    # In a production system, you would fetch the user from the database here
    return User(
        id=f"user-{user_email}",
        email=user_email,
        username=user_email.split("@")[0],
        tenant_id=tenant_id,
        role=UserRole.ADMIN,
    )

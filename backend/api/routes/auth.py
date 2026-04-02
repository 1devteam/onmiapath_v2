from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from backend.security.auth_utils import create_access_token, authenticate_user, get_password_hash, create_user_in_db, get_user_from_db, UserCreate, User
from datetime import timedelta

router = APIRouter()

@router.post("/auth/register", response_model=User, tags=["auth"])
async def register_user(user: UserCreate):
    existing_user = await get_user_from_db(user.email)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    
    hashed_password = get_password_hash(user.password)
    user_in_db = await create_user_in_db(user.email, hashed_password)
    return User(**user_in_db.dict())

@router.post("/auth/token", tags=["auth"])
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={"sub": user.email, "tenant_id": user.tenant_id},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

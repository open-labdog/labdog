from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.auth.schemas import UserCreate, UserRead
from app.auth.users import get_user_manager, UserManager
from app.db import AsyncSessionLocal
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/setup-status")
async def setup_status():
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(func.count()).select_from(User))
            count = result.scalar_one()
        return {"needs_setup": count == 0}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    user_create: UserCreate,
    user_manager: UserManager = Depends(get_user_manager),
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count()).select_from(User))
        count = result.scalar_one()

    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed. Contact an administrator.",
        )

    user = await user_manager.create(user_create)
    return user

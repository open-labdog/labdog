from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_superuser
from app.db import get_db
from app.models.user import User

# ── Schemas ──────────────────────────────────────────────────────────────────


class AdminUserCreate(BaseModel):
    email: str
    password: str
    is_superuser: bool = False


class AdminUserUpdate(BaseModel):
    email: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None


class AdminUserResponse(BaseModel):
    id: int
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PasswordReset(BaseModel):
    password: str


# ── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/admin/users", tags=["admin"])


@router.get("", response_model=list[AdminUserResponse])
async def list_users(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User))
    return result.scalars().all()


@router.post("", response_model=AdminUserResponse, status_code=201)
async def create_user(
    body: AdminUserCreate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    ph = PasswordHelper()
    user = User(
        email=body.email,
        hashed_password=ph.hash(body.password),
        is_active=True,
        is_superuser=body.is_superuser,
        is_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.is_superuser is False:
        count_result = await db.execute(
            select(func.count())
            .select_from(User)
            .where(User.is_superuser.is_(True), User.id != user_id)
        )
        other_superusers = count_result.scalar()
        if other_superusers == 0:
            raise HTTPException(status_code=400, detail="Cannot demote the last superuser")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    current_user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_superuser:
        count_result = await db.execute(
            select(func.count())
            .select_from(User)
            .where(User.is_superuser.is_(True), User.id != user_id)
        )
        other_superusers = count_result.scalar()
        if other_superusers == 0:
            raise HTTPException(status_code=400, detail="Cannot delete the last superuser")

    await db.delete(user)
    await db.commit()


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    body: PasswordReset,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    ph = PasswordHelper()
    user.hashed_password = ph.hash(body.password)
    await db.commit()
    return {"detail": "Password updated"}

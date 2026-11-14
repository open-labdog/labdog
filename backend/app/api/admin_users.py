"""User administration: accounts, roles, and password resets.

Every route needs ``users.manage`` (admins only). Three rules hold on
every path that could break them:

* at least one active admin remains: demoting, disabling or deleting the
  last one is refused;
* nobody changes their own role, disables or deletes themselves, so an
  admin cannot lock the installation out by accident;
* a change that should take effect now (a new role, disabling, a reset
  password) bumps ``token_version``, so existing sessions end on their
  next request instead of at cookie expiry.

``is_superuser`` is still accepted on create and update for clients
written before roles existed: ``true`` means admin, ``false`` on an
admin means operator. ``role`` wins when both are sent.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.password_policy import PasswordPolicyError, check_password
from app.auth.permissions import require
from app.auth.token_version import bump_token_version
from app.db import get_db
from app.models.user import User

RoleName = Literal["admin", "operator", "viewer"]

# ── Schemas ──────────────────────────────────────────────────────────────────


class AdminUserCreate(BaseModel):
    #: Validated like registration: ``UserRead`` serialises ``email`` as
    #: ``EmailStr``, so an address it rejects would 500 on every ``/me``.
    email: EmailStr
    password: str
    role: RoleName | None = None
    display_name: str | None = Field(default=None, max_length=255)
    #: Default on: the administrator chose this password, so the user
    #: should replace it with one only they know.
    must_change_password: bool = True
    #: Deprecated, from before roles: ``true`` means admin.
    is_superuser: bool | None = None


class AdminUserUpdate(BaseModel):
    email: EmailStr | None = None
    is_active: bool | None = None
    role: RoleName | None = None
    display_name: str | None = Field(default=None, max_length=255)
    #: Deprecated, from before roles: ``true`` means admin, ``false`` on
    #: an admin means operator.
    is_superuser: bool | None = None


class AdminUserResponse(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str | None
    is_active: bool
    is_superuser: bool
    is_verified: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PasswordReset(BaseModel):
    password: str
    must_change_password: bool = True


def _enforce_policy(password: str, email: str | None) -> None:
    """Apply the shared password policy, as a 400.

    SEC-30. The endpoints in this module hash and assign directly rather
    than going through ``UserManager``, so they do not inherit
    ``validate_password``. Without this an administrator could set a
    one-character password on any account — including their own, via
    reset — which is the same hole the self-service path had.
    """
    try:
        check_password(password, email=email)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _audit_snapshot(user: User) -> dict:
    """What an audit row records about an account. Never a password hash."""
    return {
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
    }


async def _other_active_admins(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.role == "admin", User.is_active.is_(True), User.id != user_id)
    )
    return result.scalar_one()


async def _get_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/admin/users", tags=["admin"])


@router.get("", response_model=list[AdminUserResponse])
async def list_users(
    _: User = Depends(require("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.id))
    return result.scalars().all()


@router.post("", response_model=AdminUserResponse, status_code=201)
async def create_user(
    body: AdminUserCreate,
    actor: User = Depends(require("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    # SEC-30: these endpoints build the row by hand and never reach
    # UserManager.validate_password, so the policy is applied here.
    _enforce_policy(body.password, body.email)

    role = body.role or ("admin" if body.is_superuser else "viewer")
    ph = PasswordHelper()
    user = User(
        email=body.email,
        hashed_password=ph.hash(body.password),
        display_name=body.display_name,
        is_active=True,
        is_verified=True,
        must_change_password=body.must_change_password,
    )
    user.role = role
    db.add(user)
    await db.flush()
    await log_action(
        db=db,
        action="create",
        entity_type="user",
        entity_id=user.id,
        user_id=actor.id,
        after_state=_audit_snapshot(user),
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    actor: User = Depends(require("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_or_404(db, user_id)
    fields = body.model_dump(exclude_unset=True)

    new_role = fields.pop("role", None)
    legacy_superuser = fields.pop("is_superuser", None)
    if new_role is None and legacy_superuser is not None:
        if legacy_superuser:
            new_role = "admin"
        elif user.role == "admin":
            new_role = "operator"
    role_changes = new_role is not None and new_role != user.role
    disables = fields.get("is_active") is False and user.is_active

    if actor.id == user.id and (role_changes or disables):
        raise HTTPException(
            status_code=400, detail="You cannot change your own role or disable yourself"
        )
    if user.role == "admin" and user.is_active and (role_changes or disables):
        if await _other_active_admins(db, user.id) == 0:
            raise HTTPException(status_code=400, detail="Cannot demote the last admin")

    if "email" in fields and fields["email"] != user.email:
        clash = await db.execute(
            select(User.id).where(User.email == fields["email"], User.id != user.id)
        )
        if clash.first():
            raise HTTPException(status_code=400, detail="Email already registered")

    before = _audit_snapshot(user)
    for field, value in fields.items():
        setattr(user, field, value)
    if role_changes:
        user.role = new_role

    await log_action(
        db=db,
        action="update",
        entity_type="user",
        entity_id=user.id,
        user_id=actor.id,
        before_state=before,
        after_state=_audit_snapshot(user),
    )
    await db.commit()
    if role_changes or disables:
        # A new role or a disabled account takes effect on the next
        # request, not when the cookie happens to expire.
        await bump_token_version(user)
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    actor: User = Depends(require("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    if actor.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    user = await _get_or_404(db, user_id)
    if user.role == "admin" and user.is_active and await _other_active_admins(db, user.id) == 0:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin")

    await log_action(
        db=db,
        action="delete",
        entity_type="user",
        entity_id=user.id,
        user_id=actor.id,
        before_state=_audit_snapshot(user),
    )
    await db.delete(user)
    await db.commit()


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    body: PasswordReset,
    actor: User = Depends(require("users.manage")),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_or_404(db, user_id)
    _enforce_policy(body.password, user.email)

    ph = PasswordHelper()
    user.hashed_password = ph.hash(body.password)
    # Resetting your own password is choosing it, not being handed one.
    user.must_change_password = body.must_change_password and actor.id != user.id
    await log_action(
        db=db,
        action="reset_password",
        entity_type="user",
        entity_id=user.id,
        user_id=actor.id,
    )
    await db.commit()
    # SEC-30: an administrator resetting a password is responding to a
    # suspected compromise more often than not. Leaving the old sessions
    # alive would make the reset cosmetic — the attacker keeps the cookie
    # and the legitimate user is the only one inconvenienced.
    await bump_token_version(user)
    return {"detail": "Password updated"}

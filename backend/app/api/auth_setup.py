import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, text

from app.auth.schemas import UserCreate, UserRead
from app.auth.users import UserManager, current_active_user, get_user_manager
from app.config import settings
from app.db import AsyncSessionLocal
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
    try:
        async with AsyncSessionLocal() as session:
            # Advisory lock prevents TOCTOU race between count check and user creation
            await session.execute(text("SELECT pg_advisory_xact_lock(8675309)"))
            result = await session.execute(select(func.count()).select_from(User))
            count = result.scalar_one()
            if count > 0:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Registration is closed. Contact an administrator.",
                )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")

    user = await user_manager.create(user_create)
    return user


@router.get("/csrf-token", status_code=200)
async def refresh_csrf_token(
    response: Response,
    _: User = Depends(current_active_user),
):
    """Re-issue the CSRF double-submit cookie for an existing session.

    Called by the frontend on mount when the labdog_csrf cookie is absent
    (e.g. sessions that predate the CSRF feature). GET is intentional —
    the CSRF middleware only guards mutating methods, so this works without
    a pre-existing token.
    """
    token = secrets.token_urlsafe(settings.security.csrf_token_bytes)
    response.set_cookie(
        "labdog_csrf",
        token,
        max_age=settings.security.session_lifetime_seconds,
        path="/",
        domain=settings.security.cookie_domain or None,
        secure=settings.security.cookie_secure,
        httponly=False,
        samesite="lax",
    )
    return {"ok": True}

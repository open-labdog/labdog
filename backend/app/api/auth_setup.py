import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, text

from app.auth.external import list_login_providers
from app.auth.permissions import ANYWHERE, GLOBAL, BoundScope, Scope, permissions_for
from app.auth.schemas import MeRead, ScopedPermissions, UserCreate, UserRead
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


@router.get("/providers")
async def login_providers():
    """The sign-in methods besides email and password, for the login page.

    Public: the page needs it before anyone is signed in. Empty unless an
    extension registered a source (``app.auth.external``); Community shows
    the password form alone then.
    """
    providers = await list_login_providers()
    return {
        "providers": [
            {"key": p.key, "name": p.name, "kind": p.kind, "url": p.url} for p in providers
        ]
    }


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

    # Deliberately ``safe=False``: ``UserManager.create`` promotes the very
    # first user to superuser by setting the flag on ``user_create``, and
    # ``safe=True`` would make fastapi-users strip it again (its
    # ``create_update_dict()`` drops is_superuser/is_active/is_verified). The
    # manager neutralises client-supplied privilege flags itself — see
    # ``UserManager.create`` in app/auth/users.py.
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


#: ``GET /api/users/me`` with the caller's permissions. Registered in
#: place of fastapi-users' own GET (``app.main`` keeps only its PATCH), so
#: the frontend learns who it is and what it may do in one request.
me_router = APIRouter(prefix="/api/users", tags=["users"])


@me_router.get("/me", response_model=MeRead)
async def read_me(user: User = Depends(current_active_user)):
    data = UserRead.model_validate(user).model_dump()
    return MeRead(
        **data,
        permissions=sorted(await permissions_for(user)),
        permissions_anywhere=sorted(await permissions_for(user, BoundScope.of(ANYWHERE))),
    )


@me_router.get("/me/permissions", response_model=ScopedPermissions)
async def read_my_permissions(
    host_id: int | None = None,
    group_id: int | None = None,
    user: User = Depends(current_active_user),
):
    """What the caller may do on one host or group (global with neither).

    The frontend wraps a host or group page in this answer, so a user
    whose rights are scoped sees that page's buttons enabled. Like
    ``GET /me`` it answers any session: it only describes the caller.
    """
    if host_id is not None and group_id is not None:
        raise HTTPException(status_code=422, detail="Pass host_id or group_id, not both")
    if host_id is not None:
        scope = Scope("host", host_id)
    elif group_id is not None:
        scope = Scope("group", group_id)
    else:
        scope = GLOBAL
    perms = await permissions_for(user, BoundScope.of(scope))
    return ScopedPermissions(scope=scope.kind, id=scope.id, permissions=sorted(perms))

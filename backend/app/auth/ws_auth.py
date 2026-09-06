"""Authenticating a WebSocket before it is accepted.

A WebSocket is not covered by the HTTP dependency chain, so everything
the API gets for free — the session-generation check, CORS — has to be
done here explicitly. Two things were missing (SEC-34):

* The ``Origin`` header was never looked at. ``SameSite=lax`` stops a
  cross-site page sending the auth cookie in practice, so this was not a
  live hole, but relying on one cookie attribute for the only
  cross-origin control is thin.
* The session generation added for SEC-30 was not checked, so a token
  invalidated by logging out or changing a password still opened a
  terminal. That one *was* live: the mechanism that revokes a session
  everywhere had a door it did not cover.
"""

import jwt
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.token_version import SESSION_GENERATION_CLAIM
from app.config import settings
from app.models.user import User

_ALGORITHM = "HS256"
_AUDIENCE = ["fastapi-users:auth"]
_COOKIE_NAME = "labdog_auth"


async def check_ws_origin(websocket: WebSocket) -> bool:
    """Whether this handshake's ``Origin`` is one we serve.

    A handshake with no ``Origin`` is allowed: browsers always send one,
    so its absence means a non-browser client (``websocat``, a script),
    which the cookie already gates. Refusing it would break those
    without closing anything a browser could exploit.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    return origin in settings.security.allowed_origins


async def get_ws_user(websocket: WebSocket, db: AsyncSession) -> User:
    """Closes WebSocket with 4401 and raises RuntimeError on failure."""
    token = websocket.cookies.get(_COOKIE_NAME)
    if not token:
        await websocket.close(code=4401, reason="Not authenticated")
        raise RuntimeError("WebSocket auth failed: no cookie")

    try:
        payload = jwt.decode(
            token,
            settings.security.secret_key,
            algorithms=[_ALGORITHM],
            audience=_AUDIENCE,
        )
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise ValueError("No sub claim")
        user_id = int(user_id_str)
        claimed_generation = payload.get(SESSION_GENERATION_CLAIM)
        if not isinstance(claimed_generation, int):
            raise ValueError("No session generation claim")
    except Exception:
        await websocket.close(code=4401, reason="Invalid token")
        raise RuntimeError("WebSocket auth failed: invalid token")

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if user is None:
        await websocket.close(code=4401, reason="User not found or inactive")
        raise RuntimeError("WebSocket auth failed: user not found")

    # SEC-30 applies here too: a token from before a logout or a password
    # change must not open a terminal.
    if claimed_generation != (user.token_version or 0):
        await websocket.close(code=4401, reason="Session no longer valid")
        raise RuntimeError("WebSocket auth failed: stale session generation")

    return user

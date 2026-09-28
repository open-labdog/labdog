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

The origin check first compared only against ``security.allowed_origins``.
That setting exists for CORS, which never applies when the UI and the API
share an origin, so a deployment behind a reverse proxy had no reason to
set it and lost the terminal on upgrade (BUG-88). A same-origin handshake
is now allowed as well.
"""

import logging
from urllib.parse import urlsplit

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
_DEFAULT_PORTS = {"http": 80, "https": 443}

logger = logging.getLogger(__name__)


def _is_same_origin(origin: str, host: str | None) -> bool:
    """Whether ``origin`` names the host this request was sent to.

    Only host and port are compared. Behind a TLS-terminating proxy the
    request reaches us over plain HTTP while the browser's ``Origin`` says
    https, so the scheme cannot be matched; it only supplies the default
    port when either side leaves the port out.

    A cross-site page cannot pass this: the browser sets ``Origin`` to that
    page's origin, not ours. A DNS-rebound name does pass, since then the
    ``Host`` is the attacker's name too, but the browser holds no LabDog
    cookie for that name, so the handshake still fails authentication.
    """
    if not host:
        return False
    try:
        parsed_origin = urlsplit(origin)
        parsed_host = urlsplit(f"//{host}")
        default_port = _DEFAULT_PORTS.get(parsed_origin.scheme)
        origin_port = parsed_origin.port or default_port
        host_port = parsed_host.port or default_port
    except ValueError:
        return False
    if not parsed_origin.hostname or not parsed_host.hostname:
        return False
    return parsed_origin.hostname == parsed_host.hostname and origin_port == host_port


async def check_ws_origin(websocket: WebSocket) -> bool:
    """Whether this handshake's ``Origin`` is one we serve.

    Allowed: the request's own origin, compared against its ``Host``
    header, and anything in ``security.allowed_origins``, for a frontend
    served from elsewhere such as the dev server.

    A handshake with no ``Origin`` is allowed: browsers always send one,
    so its absence means a non-browser client (``websocat``, a script),
    which the cookie already gates. Refusing it would break those
    without closing anything a browser could exploit.

    A refusal is logged. It is answered before ``accept()``, which the
    ASGI server turns into an HTTP 403, so the browser sees only a close
    with code 1006 and no reason; the log is the one place it shows up.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    host = websocket.headers.get("host")
    if _is_same_origin(origin, host) or origin in settings.security.allowed_origins:
        return True
    logger.warning(
        "Refused WebSocket handshake on %s: Origin %r is neither this "
        "request's host (%r) nor listed in security.allowed_origins",
        websocket.url.path,
        origin,
        host,
    )
    return False


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

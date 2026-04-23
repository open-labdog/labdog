import jwt
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

_ALGORITHM = "HS256"
_AUDIENCE = ["fastapi-users:auth"]
_COOKIE_NAME = "labdog_auth"


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

    return user

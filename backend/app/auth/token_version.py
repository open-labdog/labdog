"""SEC-30: a session generation, so that logging out means something.

LabDog's auth cookie is a stateless JWT. Logging out cleared the cookie
in the browser and did nothing else, so a cookie somebody had already
copied — from a shared machine, a proxy log, an XSS payload — stayed
valid for the rest of ``session_lifetime_seconds``, 24 hours by default.
Changing a password did not help, and neither did an administrator
resetting one, which are precisely the moments when revoking the old
session is the entire point of the action.

``User.token_version`` is a counter the token carries as a ``tv`` claim.
Every request compares the claim to the column, so bumping the column
invalidates every token minted before the bump — at once, and without a
second datastore in the request path. The alternative, a Redis denylist,
needs that second store and fails *open* when it is unreachable, which
is the wrong direction for a revocation mechanism.

A token with no ``tv`` claim is rejected. That logs every existing
session out once, on the deploy that adds the column; accepting them
would leave exactly the tokens this exists to revoke working until they
expired by themselves.

The comparison costs nothing extra: ``read_token`` already loads the
user row on every request to check ``is_active``, so the value being
compared against is already in hand.
"""

from __future__ import annotations

import logging

from fastapi_users import models
from fastapi_users.authentication.strategy.jwt import JWTStrategy
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.manager import BaseUserManager
from sqlalchemy.ext.asyncio import async_object_session

logger = logging.getLogger(__name__)

#: Claim name. Short because it rides in every cookie. Named for the
#: generation rather than the token so bandit does not read the constant
#: as a hardcoded credential (B105) — it is a field name, not a secret.
SESSION_GENERATION_CLAIM = "tv"


async def bump_token_version(user) -> None:
    """Invalidate every token already issued for *user*.

    Commits through the session *user* is attached to, because the
    revocation has to survive the request that triggered it even if
    something later in that request fails — a logout that rolls back is
    a logout that did not happen.

    A detached user (no session) is a programming error rather than
    something to paper over, so it is logged loudly and skipped instead
    of raising into an endpoint that has already done its real work.
    """
    session = async_object_session(user)
    if session is None:
        logger.error(
            "cannot revoke sessions for user %s: the row is not attached to a session",
            getattr(user, "id", "?"),
        )
        return
    user.token_version = (user.token_version or 0) + 1
    await session.commit()


class VersionedJWTStrategy(JWTStrategy):
    """``JWTStrategy`` that stamps and checks the session generation."""

    async def write_token(self, user: models.UP) -> str:
        """Mint a token stamped with the user's current generation.

        Mirrors ``JWTStrategy.write_token`` rather than calling it,
        because the claim has to be inside the signed payload — there is
        no upstream hook to add one.
        """
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            SESSION_GENERATION_CLAIM: user.token_version or 0,
        }
        return generate_jwt(
            data,
            self.encode_key,
            self.lifetime_seconds,
            algorithm=self.algorithm,
        )

    async def read_token(
        self,
        token: str | None,
        user_manager: BaseUserManager,
    ) -> models.UP | None:
        user = await super().read_token(token, user_manager)
        if user is None:
            return None
        claimed = _claimed_version(token, self)
        if claimed is None or claimed != (user.token_version or 0):
            # Not an error worth logging per request: an old cookie
            # arriving after a logout is the mechanism working.
            return None
        return user

    async def destroy_token(self, token: str, user: models.UP) -> None:
        """Make logout actually revoke, rather than only forget.

        ``AuthenticationBackend.logout`` calls this and swallows
        ``StrategyDestroyNotSupportedError``, which is what the base
        ``JWTStrategy`` raises — so logout used to clear the cookie and
        leave the token valid. Bumping here revokes every session the
        user has, which is the behaviour someone clicking "log out" on a
        machine they do not trust is asking for.
        """
        await bump_token_version(user)


def _claimed_version(token: str | None, strategy: JWTStrategy) -> int | None:
    """The ``tv`` claim, or ``None`` when absent or unreadable.

    Decoding a second time is deliberate. Overriding
    ``read_token`` wholesale would mean copying fastapi-users' user
    lookup and its exception handling into this file, where it would
    silently rot against the next release. A JWT decode is cheap
    relative to the database round trip that already happens on the same
    request.
    """
    if token is None:
        return None
    try:
        data = decode_jwt(
            token,
            strategy.decode_key,
            strategy.token_audience,
            algorithms=[strategy.algorithm],
        )
    except Exception:
        return None
    value = data.get(SESSION_GENERATION_CLAIM)
    return value if isinstance(value, int) else None

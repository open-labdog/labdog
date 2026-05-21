"""
CSRF double-submit-cookie middleware.

On every state-changing request (POST/PUT/PATCH/DELETE) the middleware
requires that the request carries:

  - Cookie ``labdog_csrf`` (set by the server at login, readable by JS)
  - Header ``X-CSRF-Token`` with the same value

Both values are compared with ``hmac.compare_digest`` to prevent timing
attacks.  A mismatch returns 403.

Exempt paths (the CSRF token does not yet exist at these points, or
clearing the session must succeed regardless of token state):
  - POST /api/auth/jwt/login
  - POST /api/auth/register
  - POST /api/auth/forgot-password
  - POST /api/auth/reset-password
  - POST /api/auth/jwt/logout   — must not block sign-out; the middleware
                                   also clears the csrf cookie on this path.

WebSocket connections are transparently forwarded without inspection.
GET/HEAD/OPTIONS requests are always allowed.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request, Response

from app.config import settings

logger = logging.getLogger(__name__)

_CSRF_COOKIE = "labdog_csrf"
_CSRF_HEADER = "X-CSRF-Token"

# Mutating HTTP methods that require the double-submit token.
_GUARDED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths exempted from CSRF validation.
_EXEMPT_PATHS = frozenset(
    {
        "/api/auth/jwt/login",
        "/api/auth/register",
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
        "/api/auth/jwt/logout",
    }
)

_LOGOUT_PATH = "/api/auth/jwt/logout"


class CSRFMiddleware:
    """Pure ASGI double-submit-cookie CSRF middleware.

    Implemented as a raw ASGI callable (not BaseHTTPMiddleware) so it
    does not interfere with WebSocket upgrade handshakes.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        # Pass WebSocket and lifespan scopes through untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        method = request.method.upper()

        if method in _GUARDED_METHODS:
            path = request.url.path
            if path not in _EXEMPT_PATHS:
                cookie_val = request.cookies.get(_CSRF_COOKIE, "")
                header_val = request.headers.get(_CSRF_HEADER, "")

                if not cookie_val or not header_val:
                    logger.debug(
                        "CSRF check failed for %s %s: missing token", method, path
                    )
                    response = Response(
                        content='{"detail":"CSRF token missing or invalid"}',
                        status_code=403,
                        media_type="application/json",
                    )
                    await response(scope, receive, send)
                    return

                if not hmac.compare_digest(cookie_val, header_val):
                    logger.debug(
                        "CSRF check failed for %s %s: token mismatch", method, path
                    )
                    response = Response(
                        content='{"detail":"CSRF token missing or invalid"}',
                        status_code=403,
                        media_type="application/json",
                    )
                    await response(scope, receive, send)
                    return

        # For the logout path, clear the CSRF cookie in the response.
        if method == "POST" and request.url.path == _LOGOUT_PATH:

            async def send_with_csrf_clear(message):
                if message["type"] == "http.response.start":
                    clear_cookie = (
                        f"{_CSRF_COOKIE}=; Max-Age=0; Path=/; SameSite=lax"
                    )
                    if settings.security.cookie_secure:
                        clear_cookie += "; Secure"
                    if settings.security.cookie_domain:
                        clear_cookie += f"; Domain={settings.security.cookie_domain}"
                    extra_header = (b"set-cookie", clear_cookie.encode())
                    message = {
                        **message,
                        "headers": list(message.get("headers", [])) + [extra_header],
                    }
                await send(message)

            await self.app(scope, receive, send_with_csrf_clear)
            return

        await self.app(scope, receive, send)

"""Paths that take a password from a browser that is not signed in yet.

Two middlewares treat these specially, and both must agree:

* ``CSRFMiddleware`` exempts them: before sign-in the browser has no CSRF
  cookie to echo, and a login form cannot be forged into doing harm.
* ``_LoginRateLimitMiddleware`` (``app.main``) throttles them per account,
  because each request can test a password.

An extension that adds a password login of its own (LDAP) registers its
endpoint here, from its ``register()``, so it gets both. Exact paths only:
put the provider in the query string (``/api/auth/ldap/login?provider=ad``),
not in the path, so a prefix never exempts more than one route.

Part of the plugin contract versioned by ``app.auth.permissions.PLUGIN_API``.
"""

from __future__ import annotations

_CORE = ("/api/auth/jwt/login", "/api/auth/register")

_paths: set[str] = set(_CORE)


def register_password_login_path(path: str) -> None:
    """Treat *path* as a password login: CSRF-exempt and rate-limited."""
    if not path.startswith("/api/") or "?" in path or "{" in path:
        raise ValueError(f"login path must be an exact /api/ path, got {path!r}")
    _paths.add(path)


def is_password_login_path(path: str) -> bool:
    return path in _paths


def reset_password_login_paths() -> None:
    """Back to the core paths only. For tests."""
    _paths.clear()
    _paths.update(_CORE)

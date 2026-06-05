import logging
import logging.config
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import FileResponse, RedirectResponse

from app.api._repo_scan import router as repo_scan_router
from app.api.action_packs import router as action_packs_router
from app.api.action_resolutions import router as action_resolutions_router
from app.api.actions import router as actions_router
from app.api.admin_users import router as admin_users_router
from app.api.audit import router as audit_router
from app.api.auth_setup import router as auth_setup_router
from app.api.ca_cert_actions import router as ca_cert_actions_router
from app.api.ca_certs import router as ca_certs_router
from app.api.cron_jobs import router as cron_jobs_router
from app.api.cron_sync import router as cron_sync_router
from app.api.discovery import router as discovery_router
from app.api.drift import router as drift_router
from app.api.git_repos import router as git_repos_router
from app.api.groups import router as groups_router
from app.api.host_state import router as host_state_router
from app.api.hosts import router as hosts_router
from app.api.hosts_drift import router as hosts_drift_router
from app.api.hosts_entries import router as hosts_entries_router
from app.api.hosts_sync import router as hosts_sync_router
from app.api.linux_groups import router as linux_groups_router
from app.api.linux_users import router as linux_users_router
from app.api.package_sync import router as package_sync_router
from app.api.packages import router as packages_router
from app.api.proxmox_discovery import router as proxmox_discovery_router
from app.api.grafana import router as grafana_router
from app.api.proxmox_nodes import router as proxmox_nodes_router
from app.api.resolver import router as resolver_router
from app.api.resolver_sync import router as resolver_sync_router
from app.api.rules import router as rules_router
from app.api.scans import router as scans_router
from app.api.scheduled_actions import router as scheduled_actions_router
from app.api.service_drift import router as service_drift_router
from app.api.service_live import router as service_live_router
from app.api.service_sync import router as service_sync_router
from app.api.services import router as services_router
from app.api.settings import router as settings_router
from app.api.ssh_keys import router as ssh_keys_router
from app.api.ssh_terminal import router as ssh_terminal_router
from app.api.sync import router as sync_router
from app.api.user_sync import router as user_sync_router
from app.api.version import router as version_router
from app.api.webhooks import router as webhooks_router
from app.auth.schemas import UserRead, UserUpdate
from app.auth.users import auth_backend, fastapi_users
from app.config import settings
from app.middleware.csrf import CSRFMiddleware

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Set up application-wide logging from config."""
    level = settings.logging.level.upper()

    if settings.logging.format == "json":
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": fmt,
                    "datefmt": "%Y-%m-%dT%H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stderr",
                },
            },
            "root": {
                "level": level,
                "handlers": ["console"],
            },
            "loggers": {
                "uvicorn": {"level": level},
                "uvicorn.access": {"level": level},
                "celery": {"level": level},
                "sqlalchemy.engine": {
                    "level": "WARNING" if level != "DEBUG" else "INFO",
                },
            },
        }
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting trusted proxies."""
    trusted = settings.server.trusted_proxies
    if trusted and request.client:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # Walk the chain from right to left, skipping trusted proxies
            chain = [ip.strip() for ip in forwarded.split(",")]
            for ip in reversed(chain):
                if ip not in trusted:
                    return ip
    if request.client:
        return request.client.host
    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Login rate limiter (uses limits library directly)
# ---------------------------------------------------------------------------


def _build_login_limiter():
    """Build a standalone rate limiter for login endpoints.

    Uses the ``limits`` library (a slowapi dependency) with Redis storage
    so the limit survives across workers/restarts.
    """
    from limits import parse as parse_limit
    from limits.storage import storage_from_string
    from limits.strategies import MovingWindowRateLimiter

    storage = storage_from_string(settings.redis.url)
    limiter = MovingWindowRateLimiter(storage)
    rate = parse_limit(settings.rate_limit.login)

    class _LoginLimiter:
        def test(self, key: str) -> bool:
            return limiter.test(rate, "login", key)

        def hit(self, key: str) -> bool:
            return limiter.hit(rate, "login", key)

    return _LoginLimiter()


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware:
    """Pure ASGI middleware that adds security headers to HTTP responses.

    Uses the raw ASGI interface instead of BaseHTTPMiddleware to avoid
    breaking WebSocket connections (BaseHTTPMiddleware wraps responses
    in a way that prevents WebSocket upgrade handshakes).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                dict(message.get("headers", []))
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"x-xss-protection", b"1; mode=block"),
                    (
                        b"permissions-policy",
                        b"camera=(), microphone=(), geolocation=(), payment=()",
                    ),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self' 'unsafe-inline';"
                        b" style-src 'self' 'unsafe-inline'",
                    ),
                ]
                if settings.tls.force_https or settings.security.cookie_secure:
                    extra.append(
                        (
                            b"strict-transport-security",
                            b"max-age=63072000; includeSubDomains",
                        )
                    )
                message = {
                    **message,
                    "headers": list(message.get("headers", [])) + extra,
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# HTTPS redirect middleware
# ---------------------------------------------------------------------------


class HTTPSRedirectMiddleware:
    """Pure ASGI middleware for HTTPS redirect that skips WebSocket connections."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.datastructures import URL

            url = URL(scope=scope)
            if url.scheme == "http":
                redirect_url = url.replace(scheme="https")
                response = RedirectResponse(url=str(redirect_url), status_code=301)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: sync every enabled action pack from its git remote, then
    fold them into the in-memory action registry. Failures are logged
    per-pack but don't prevent the app from booting — bundled actions
    are always available."""
    logger = logging.getLogger(__name__)
    try:
        from app.actions.registry import reload_registry_async  # noqa: PLC0415
        from app.db import AsyncSessionLocal  # noqa: PLC0415
        from app.packs.service import sync_enabled_packs  # noqa: PLC0415

        async with AsyncSessionLocal() as session:
            await sync_enabled_packs(session)
            await reload_registry_async(session)
    except Exception:
        logger.exception("action-pack startup sync failed; bundled pack only")
    yield


def create_app() -> FastAPI:
    _configure_logging()
    logger = logging.getLogger(__name__)

    app = FastAPI(title="LabDog", version="0.1.0", lifespan=_lifespan)

    # -- HTTPS redirect (must be outermost) --
    if settings.tls.force_https:
        app.add_middleware(HTTPSRedirectMiddleware)
        logger.info("HTTPS redirect enabled")

    # -- Security headers --
    app.add_middleware(SecurityHeadersMiddleware)

    # -- CORS --
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    )

    # -- CSRF double-submit cookie (runs after CORS, before route handlers) --
    app.add_middleware(CSRFMiddleware)

    # -- Rate limiting --
    if settings.rate_limit.enabled:
        limiter = Limiter(
            key_func=_get_client_ip,
            default_limits=[settings.rate_limit.api],
            storage_uri=settings.redis.url,
        )
        app.state.limiter = limiter

        # Wrap SlowAPIMiddleware so it skips WebSocket connections
        # (SlowAPIMiddleware extends BaseHTTPMiddleware which breaks WS)
        class _WSafeSlowAPI:
            def __init__(self, app):
                self._ws_app = app
                self._http_app = SlowAPIMiddleware(app)

            async def __call__(self, scope, receive, send):
                if scope["type"] != "http":
                    await self._ws_app(scope, receive, send)
                else:
                    await self._http_app(scope, receive, send)

        app.add_middleware(_WSafeSlowAPI)

        # Stricter limit on auth endpoints (login, register)
        # Uses pure ASGI middleware to avoid BaseHTTPMiddleware breaking WS
        _login_limiter = _build_login_limiter()

        class _LoginRateLimitMiddleware:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] != "http":
                    await self.app(scope, receive, send)
                    return
                request = Request(scope, receive)
                if request.method == "POST" and request.url.path in (
                    "/api/auth/jwt/login",
                    "/api/auth/register",
                ):
                    client_ip = _get_client_ip(request)
                    if not _login_limiter.hit(client_ip):
                        response = Response(
                            content='{"detail":"Too many login attempts. Try again later."}',
                            status_code=429,
                            media_type="application/json",
                        )
                        await response(scope, receive, send)
                        return
                await self.app(scope, receive, send)

        app.add_middleware(_LoginRateLimitMiddleware)

        @app.exception_handler(RateLimitExceeded)
        async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(exc.detail)},
            )

        logger.info(
            "Rate limiting enabled — login: %s, api: %s",
            settings.rate_limit.login,
            settings.rate_limit.api,
        )

    # -- Auth routes --
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/api/auth/jwt",
        tags=["auth"],
    )

    app.include_router(auth_setup_router)

    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/api/users",
        tags=["users"],
    )

    app.include_router(actions_router, prefix="/api")
    app.include_router(action_packs_router, prefix="/api")
    app.include_router(action_resolutions_router, prefix="/api")
    app.include_router(groups_router, prefix="/api")
    app.include_router(hosts_router, prefix="/api")
    app.include_router(host_state_router, prefix="/api")
    app.include_router(ssh_keys_router, prefix="/api")
    app.include_router(rules_router, prefix="/api")
    app.include_router(sync_router, prefix="/api")
    app.include_router(drift_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(discovery_router, prefix="/api")
    app.include_router(git_repos_router, prefix="/api")
    app.include_router(repo_scan_router, prefix="/api")
    app.include_router(scheduled_actions_router, prefix="/api")
    app.include_router(scans_router, prefix="/api")
    app.include_router(admin_users_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(services_router, prefix="/api")
    app.include_router(service_drift_router, prefix="/api")
    app.include_router(service_sync_router, prefix="/api")
    app.include_router(service_live_router, prefix="/api")
    app.include_router(hosts_entries_router, prefix="/api")
    app.include_router(hosts_drift_router, prefix="/api")
    app.include_router(hosts_sync_router, prefix="/api")
    app.include_router(linux_users_router, prefix="/api")
    app.include_router(linux_groups_router, prefix="/api")
    app.include_router(cron_jobs_router, prefix="/api")
    app.include_router(user_sync_router, prefix="/api")
    app.include_router(cron_sync_router, prefix="/api")
    app.include_router(packages_router, prefix="/api")
    app.include_router(package_sync_router, prefix="/api")
    app.include_router(ca_certs_router, prefix="/api")
    app.include_router(ca_cert_actions_router, prefix="/api")
    app.include_router(resolver_router, prefix="/api")
    app.include_router(resolver_sync_router, prefix="/api")
    app.include_router(proxmox_nodes_router, prefix="/api")
    app.include_router(proxmox_discovery_router, prefix="/api")
    app.include_router(grafana_router, prefix="/api")
    app.include_router(ssh_terminal_router)

    app.include_router(version_router)
    app.include_router(webhooks_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # -- Static file serving (SPA) --
    static_dir = _resolve_static_dir()
    if static_dir:
        logger.info("Serving frontend static files from %s", static_dir)
        index_html = static_dir / "index.html"

        @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def spa_fallback(full_path: str):
            """Serve static files; fall back to index.html for SPA routes."""
            file_path = static_dir / full_path
            if full_path and not file_path.resolve().is_relative_to(static_dir.resolve()):
                return FileResponse(index_html)
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            # Support trailingSlash: true exports (e.g. /login/ → login/index.html)
            if full_path and file_path.is_dir():
                dir_index = file_path / "index.html"
                if dir_index.is_file():
                    return FileResponse(dir_index)
            # Support dynamic routes: /hosts/123/ → hosts/placeholder/index.html
            result = _resolve_dynamic_route(static_dir, full_path)
            if result:
                resolved_file, dynamic_value = result
                if dynamic_value:
                    # Rewrite the RSC flight data so the baked-in route
                    # params match the actual URL instead of "placeholder".
                    content = resolved_file.read_text(encoding="utf-8")
                    # HTML has escaped quotes (\"), .txt has plain quotes
                    content = content.replace('\\"placeholder\\"', f'\\"{dynamic_value}\\"')
                    content = content.replace('"placeholder"', f'"{dynamic_value}"')
                    media_type = "text/html" if resolved_file.suffix == ".html" else "text/plain"
                    return Response(content=content, media_type=media_type)
                return FileResponse(resolved_file)
            return FileResponse(index_html)
    else:
        logger.warning("No frontend static directory found — running in API-only mode")

    return app


def _resolve_dynamic_route(static_dir: Path, full_path: str) -> tuple[Path, str | None] | None:
    """Resolve a Next.js dynamic route by substituting missing path segments
    with the generateStaticParams placeholder directory.

    Returns ``(resolved_file, dynamic_value)`` where *dynamic_value* is the
    original URL segment that was substituted (e.g. ``"1"`` for
    ``/groups/1/``), or ``None`` when no substitution was needed.
    """
    parts = Path(full_path).parts
    if not parts:
        return None
    current = static_dir
    dynamic_value: str | None = None
    for part in parts:
        candidate = current / part
        if candidate.is_dir():
            current = candidate
        elif candidate.is_file():
            return (candidate, dynamic_value)
        else:
            placeholder = current / "placeholder"
            if placeholder.is_dir():
                dynamic_value = part
                current = placeholder
            else:
                return None
    index = current / "index.html"
    if index.is_file():
        return (index, dynamic_value)
    return None


def _resolve_static_dir() -> Path | None:
    """Return the frontend static directory, or None if not available.

    Skipped when LABDOG_DEV_MODE=1 (set by dev.sh) so the Next.js dev
    server on :3000 is used instead of a stale static export.
    """
    if os.environ.get("LABDOG_DEV_MODE"):
        return None
    configured = settings.server.static_dir
    if configured:
        p = Path(configured)
        if p.is_dir() and (p / "index.html").is_file():
            return p
        return None

    # Auto-detect: development layout
    dev_path = Path(__file__).resolve().parents[2] / "frontend" / "out"
    if dev_path.is_dir() and (dev_path / "index.html").is_file():
        return dev_path

    # Auto-detect: production packaged install
    prod_path = Path("/usr/lib/labdog/frontend/out")
    if prod_path.is_dir() and (prod_path / "index.html").is_file():
        return prod_path

    return None


app = create_app()

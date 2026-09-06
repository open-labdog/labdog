import ipaddress
import json
import logging
import logging.config
import os
import re
import urllib.parse
from contextlib import asynccontextmanager
from functools import lru_cache
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
from app.api.ai import router as ai_router
from app.api.audit import router as audit_router
from app.api.auth_setup import router as auth_setup_router
from app.api.ca_cert_actions import router as ca_cert_actions_router
from app.api.ca_certs import router as ca_certs_router
from app.api.cron_jobs import router as cron_jobs_router
from app.api.cron_sync import router as cron_sync_router
from app.api.dashboard import router as dashboard_router
from app.api.discovery import router as discovery_router
from app.api.drift import router as drift_router
from app.api.git_repos import router as git_repos_router
from app.api.grafana import router as grafana_router
from app.api.groups import router as groups_router
from app.api.host_state import router as host_state_router
from app.api.hosts import router as hosts_router
from app.api.hosts_drift import router as hosts_drift_router
from app.api.hosts_entries import router as hosts_entries_router
from app.api.hosts_sync import router as hosts_sync_router
from app.api.linux_groups import router as linux_groups_router
from app.api.linux_users import router as linux_users_router
from app.api.metrics import router as metrics_router
from app.api.metrics import status_router as metrics_status_router
from app.api.package_sync import router as package_sync_router
from app.api.packages import router as packages_router
from app.api.proxmox_discovery import router as proxmox_discovery_router
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


def _is_trusted_proxy(ip: str, trusted: list[str]) -> bool:
    """Whether *ip* is one of the configured proxies.

    Accepts CIDR entries as well as literal addresses (SEC-32). The
    check used to be a plain ``in`` against the list, so ``10.0.0.0/8``
    never matched anything — which made the setting unusable in Docker,
    where the proxy's address is assigned at container start and is not
    known when the config is written.
    """
    for entry in trusted:
        if ip == entry:
            return True
        try:
            if ipaddress.ip_address(ip) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            # A malformed entry should not break IP resolution for
            # every request; skip it and try the next.
            continue
    return False


@lru_cache(maxsize=1)
def _warn_untrusted_forwarding() -> None:
    """Say once that forwarded addresses are being discarded.

    Cached rather than guarded by a module flag so the "only once" part
    is the mechanism rather than a convention someone has to maintain.
    """
    logging.getLogger(__name__).warning(
        "X-Forwarded-For is present but server.trusted_proxies is empty, so the "
        "real client address is being ignored. Set trusted_proxies to your "
        "reverse proxy's address or network."
    )


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting trusted proxies."""
    trusted = settings.server.trusted_proxies
    forwarded = request.headers.get("x-forwarded-for", "")
    if trusted and request.client:
        if forwarded:
            # Walk the chain from right to left, skipping trusted proxies
            chain = [ip.strip() for ip in forwarded.split(",")]
            for ip in reversed(chain):
                if not _is_trusted_proxy(ip, trusted):
                    return ip
    elif forwarded:
        # A reverse proxy is forwarding the real client address and
        # LabDog is discarding it, so every request looks like it came
        # from the proxy — which is what collapsed the login rate limit
        # into a single shared bucket (SEC-32).
        _warn_untrusted_forwarding()
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
# Login rate-limit keying (SEC-32)
# ---------------------------------------------------------------------------
#
# The limiter used to key on the client IP alone. Behind a reverse proxy
# with ``server.trusted_proxies`` unset — the default — every request
# resolves to the proxy's address, so the whole install shared one
# 5/minute bucket: five bad passwords from anywhere locked everybody out,
# and an attacker was never throttled relative to anyone else.
#
# Keying on the account being attacked fixes both halves. Two buckets are
# consumed per attempt:
#
#   * ``(ip, identity)`` — the targeted case. One source guessing one
#     account's password.
#   * ``identity`` — the distributed case. Many sources guessing one
#     account's password, which the first bucket alone would not catch.
#
# There is deliberately no bucket on the IP alone. That is the one this
# change exists to remove, and behind a proxy it is the same global
# bucket by another name.

#: Paths the login limiter guards.
_LOGIN_PATHS = frozenset({"/api/auth/jwt/login", "/api/auth/register"})

#: Most a login body may be before we stop buffering it to find the
#: account name. A real one is a couple of hundred bytes; the cap keeps
#: an attacker from making the middleware hold arbitrary memory.
_MAX_LOGIN_BODY = 8192

#: Identities longer than this are truncated before they become part of
#: a Redis key, so the key size stays bounded by us rather than by the
#: caller.
_MAX_IDENTITY = 128


def login_rate_limit_keys(client_ip: str, identity: str | None) -> list[str]:
    """Buckets one login attempt should consume.

    Empty when the request carries no recognisable account name. Such a
    request cannot test a password, so throttling it buys nothing — and
    keying it on the IP would reintroduce exactly the shared bucket this
    replaces, reachable by anyone willing to POST nonsense.
    """
    if not identity:
        return []
    return [f"ip-user:{client_ip}|{identity}", f"user:{identity}"]


def login_identity(content_type: str, body: bytes) -> str | None:
    """The account name a login or register body is about, normalised.

    ``/api/auth/jwt/login`` is an OAuth2 password form (``username``);
    ``/api/auth/register`` is JSON (``email``). Case and surrounding
    whitespace are folded so that ``Alice@x.com`` and ``alice@x.com ``
    cannot buy an attacker two separate budgets against one account.
    """
    if not body:
        return None
    raw: str | None = None
    ctype = content_type.split(";", 1)[0].strip().lower()
    try:
        if ctype == "application/json":
            payload = json.loads(body)
            if isinstance(payload, dict):
                value = payload.get("email") or payload.get("username")
                raw = value if isinstance(value, str) else None
        else:
            # Form-encoded, which is what OAuth2PasswordRequestForm reads.
            parsed = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
            values = parsed.get("username") or parsed.get("email") or []
            raw = values[0] if values else None
    except (ValueError, UnicodeDecodeError):
        return None
    if not raw:
        return None
    return raw.strip().casefold()[:_MAX_IDENTITY] or None


async def _buffered_receive(receive):
    """Read the request body, returning it plus a replayable ``receive``.

    ASGI bodies are consumed once. To key on the account name we have to
    look at the body before the route does, so the messages are recorded
    and handed back verbatim — including an ``http.disconnect``, which
    must reach the app rather than being swallowed here.

    Stops at :data:`_MAX_LOGIN_BODY`; anything beyond that is left in the
    stream for the route to read, and the identity simply comes out as
    ``None``.
    """
    messages: list[dict] = []
    total = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        total += len(message.get("body", b""))
        if not message.get("more_body", False) or total > _MAX_LOGIN_BODY:
            break
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.request")

    async def replay():
        if messages:
            return messages.pop(0)
        return await receive()

    return (b"" if total > _MAX_LOGIN_BODY else body), replay


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

    # Warm the settings cache before serving. The synchronous readers used
    # by the SSH paths and the terminal's idle checker read that cache and
    # never the database, so an unwarmed process would answer from hardcoded
    # defaults. Kept separate from the pack sync below so a git failure
    # cannot take the settings down with it.
    try:
        from app.db import AsyncSessionLocal  # noqa: PLC0415
        from app.settings_service import refresh_settings_cache  # noqa: PLC0415

        async with AsyncSessionLocal() as session:
            await refresh_settings_cache(session)
    except Exception:
        logger.warning(
            "could not warm the settings cache at startup; synchronous "
            "readers will use defaults until a session refreshes it",
            exc_info=True,
        )

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

    # SEC-34: the interactive docs enumerate every route and schema and
    # are served unauthenticated. Off by default; `server.expose_docs`
    # turns them back on where that is wanted (dev/labdog.toml does).
    _docs = settings.server.expose_docs
    app = FastAPI(
        title="LabDog",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url="/docs" if _docs else None,
        redoc_url="/redoc" if _docs else None,
        openapi_url="/openapi.json" if _docs else None,
    )

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
                elif scope["path"] == "/metrics":
                    # Exempt the scrape endpoint from the general API rate
                    # limit. `@limiter.exempt` isn't usable here — the
                    # `Limiter` is constructed inside `create_app()`, so
                    # there's no module-level object to decorate. Without
                    # this, `_get_client_ip` (which only trusts
                    # `X-Forwarded-For` from `settings.server.trusted_proxies`,
                    # empty by default) sees every request as the reverse
                    # proxy's IP, so a Prometheus scraper would share the
                    # 100/min bucket with every logged-in operator — a
                    # monitoring gap that opens exactly when the UI is
                    # busiest. The TTL cache in app.metrics.collector is
                    # what actually bounds the DoS surface here, not this
                    # rate limit.
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
                if not (request.method == "POST" and request.url.path in _LOGIN_PATHS):
                    await self.app(scope, receive, send)
                    return

                # SEC-32: the bucket is per account, not per source
                # address, so the body has to be read here — and then
                # handed back intact, because ASGI bodies are read once.
                body, receive = await _buffered_receive(receive)
                identity = login_identity(request.headers.get("content-type", ""), body)
                client_ip = _get_client_ip(request)
                for key in login_rate_limit_keys(client_ip, identity):
                    if not _login_limiter.hit(key):
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
    app.include_router(dashboard_router, prefix="/api")
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
    app.include_router(ai_router, prefix="/api")
    app.include_router(metrics_status_router, prefix="/api")
    app.include_router(ssh_terminal_router)

    app.include_router(version_router)
    # Under /api like every other router. These endpoints authenticate with
    # a shared secret rather than a session cookie, so they are exempted
    # from CSRF by prefix in app.middleware.csrf — an exemption that only
    # holds while they stay under this prefix.
    app.include_router(webhooks_router, prefix="/api")
    # metrics_router owns the literal root path "/metrics" and MUST be
    # registered before the SPA catch-all route below
    # (`@app.api_route("/{full_path:path}", ...)`) is added — FastAPI/
    # Starlette match routes in registration order, and the catch-all
    # matches every unmatched GET/HEAD with `index.html` + HTTP 200. If
    # metrics_router were registered after (or conditionally, only when
    # enabled), a disabled or misregistered exporter would silently serve
    # the SPA shell for `/metrics` instead of a 404 — exactly the failure
    # mode `app.api.metrics.get_metrics` is designed to avoid by always
    # registering and branching on `settings.metrics.enabled` internally.
    app.include_router(metrics_router)

    @app.get("/health")
    async def health():
        """Liveness. Kept at the original path so nothing pointed here breaks.

        Deliberately still a constant: this answers "is the process
        responding", which is what a restart policy should act on.
        Readiness is a different question and lives at /health/ready.
        """
        return {"status": "ok"}

    @app.get("/health/live")
    async def health_live():
        """Liveness, at the conventional path. Same answer as /health."""
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready(response: Response):
        """Readiness: can this instance actually do its job right now?

        /health returned ``{"status": "ok"}`` unconditionally — it never
        touched the database, Redis, or the Celery children.
        ``CeleryManager.is_alive()`` existed and was never called from
        anywhere. So if the Celery subprocess died, the container stayed
        healthy forever while nothing executed a single task: no sync, no
        drift check, no scheduled action, and no signal that anything was
        wrong (BUG-72).

        Returns 503 with a per-component dict when any component is down,
        so an operator reading the failing probe learns *which* one.
        """
        from sqlalchemy import text as _sql_text  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

        from app.celery_manager import active_manager  # noqa: PLC0415

        components: dict[str, str] = {}

        # A dedicated connection rather than one from the request pool.
        # The question is "can I reach Postgres", and borrowing from the
        # pool answers a different one: under load the probe would queue
        # behind real traffic and report the database down when it is
        # merely busy — turning a slow moment into a restart.
        #
        # Engine construction is inside the try: a health check that raises
        # is a health check that returns 500 with no component detail,
        # which is the one outcome this endpoint exists to prevent.
        probe_engine = None
        try:
            probe_engine = create_async_engine(settings.database.url, pool_size=1, max_overflow=0)
            async with probe_engine.connect() as conn:
                await conn.execute(_sql_text("SELECT 1"))
            components["database"] = "ok"
        except Exception as exc:
            logger.warning("readiness: database check failed: %s", exc)
            components["database"] = "error"
        finally:
            if probe_engine is not None:
                try:
                    await probe_engine.dispose()
                except Exception:  # noqa: BLE001
                    logger.warning("readiness: probe engine dispose failed", exc_info=True)

        try:
            import redis.asyncio as _redis  # noqa: PLC0415

            client = _redis.from_url(settings.redis.url)
            try:
                await client.ping()
                components["redis"] = "ok"
            finally:
                await client.aclose()
        except Exception as exc:
            logger.warning("readiness: redis check failed: %s", exc)
            components["redis"] = "error"

        manager = active_manager()
        if manager is None:
            # Either --no-celery (a development choice, not a fault) or a
            # forked uvicorn worker that does not own the subprocesses.
            # Saying "down" here would fail the probe on a healthy dev run;
            # saying "ok" would claim knowledge this process does not have.
            components["celery"] = "not_supervised_here"
        elif manager.is_alive():
            components["celery"] = "ok"
        else:
            dead = ", ".join(manager.dead_workers()) or "unknown"
            logger.error("readiness: celery worker(s) not running: %s", dead)
            components["celery"] = f"error: {dead} not running"

        ready = all(not v.startswith("error") for v in components.values())
        if not ready:
            response.status_code = 503
        return {"status": "ready" if ready else "not_ready", "components": components}

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
                resolved_file, dynamic_values = result
                if dynamic_values:
                    content = _rewrite_placeholder(
                        str(resolved_file),
                        resolved_file.stat().st_mtime_ns,
                        dynamic_values,
                    )
                    if content is None:
                        return FileResponse(index_html)
                    media_type = "text/html" if resolved_file.suffix == ".html" else "text/plain"
                    return Response(content=content, media_type=media_type)
                return FileResponse(resolved_file)
            return FileResponse(index_html)
    else:
        logger.warning("No frontend static directory found — running in API-only mode")

    return app


_SAFE_DYNAMIC_SEGMENT = re.compile(r"[0-9]{1,19}")
"""URL segments allowed to be substituted into pre-rendered flight data.

Digits only, because every dynamic route in the app router is an integer
database primary key — ``actions/runs/[runId]``, ``git-repos/[id]``,
``groups/[id]``, ``groups/[id]/actions/runs/[runId]``, ``hosts/[id]``,
``hosts/[id]/actions/runs/[runId]``, ``hosts/discovery/[id]/pending`` and
``hosts/scans/[id]``. 19 digits is int64. (The discovery entry read
``hosts/discovery/[id]`` until BUG-76; the export has no page at that
path, only under ``/pending``.)

This is the security boundary for the rewrite below, and it is an
allow-list on purpose. The segment is interpolated into a ``<script>``
block, so a deny-list would have to anticipate every way of breaking out
of a JavaScript string literal; permitting only digits leaves nothing to
break out with.

**If a slug-shaped route is ever added, widening this is not sufficient
on its own** — see the escaping note in ``_rewrite_placeholder``.
"""


def _substitute_placeholders(content: str, values: tuple[str, ...], quote: str) -> str:
    """Replace the baked-in placeholders in *content*, in path order.

    BUG-76. The export bakes the literal ``placeholder`` into the flight
    data once per dynamic segment, in two distinct shapes, and a route
    with two segments contains both twice:

    * the path array — ``["","hosts","placeholder","actions","runs","placeholder",""]``
    * one route-param tuple each — ``["id","placeholder","d",null]`` and
      ``["runId","placeholder","d",null]``

    Replacing every occurrence with one value made ``/hosts/7/actions/runs/12/``
    render as though the host id were also ``12``, so the back-link and
    breadcrumb on a run page opened under a host pointed at the run.

    Each shape carries its segments in path order, so each is walked
    separately and consumed in order. The param tuples go first: their
    placeholders also match the plain pattern, and taking them out of the
    way first is what keeps the two counts independent.

    An occurrence past the end of *values* is left alone rather than
    guessed at. A stale ``placeholder`` in the flight data is what the
    unfixed code produced for every unhandled shape, and the client
    router resolves the route regardless — a wrong id would be worse
    than none.

    *quote* is the quoting the file uses: ``"`` in the ``.txt`` payloads,
    ``\\"`` in HTML, where the flight data is inside a JS string.
    """
    q = re.escape(quote)
    # One pass, two counters. A separate pass per shape would let the
    # second pattern pick up a param tuple the first had deliberately
    # left alone, and replace it with the wrong value — which is how the
    # "more placeholders than values" case failed in review.
    pattern = re.compile(
        rf"({q}\w+{q},{q})placeholder({q},{q}d{q})"  # ["id","placeholder","d"
        rf"|{q}placeholder{q}(?!:)"  # a path-array element
    )
    # The lookahead above skips a JSON prop *key* named "placeholder"
    # (e.g. a form input's), which is not a route value.
    counts = {"param": 0, "path": 0}

    def repl(match: re.Match[str]) -> str:
        kind = "param" if match.group(1) is not None else "path"
        index = counts[kind]
        if index >= len(values):
            return match.group(0)
        counts[kind] = index + 1
        value = values[index]
        if kind == "param":
            return f"{match.group(1)}{value}{match.group(2)}"
        return f"{quote}{value}{quote}"

    return pattern.sub(repl, content)


@lru_cache(maxsize=512)
def _rewrite_placeholder(
    file_path: str, mtime_ns: int, dynamic_values: tuple[str, ...]
) -> str | None:
    """Return *file_path* with the baked-in route placeholders replaced.

    Returns ``None`` when any value is not a permitted segment, which the
    caller turns into a plain ``index.html`` response.

    Keyed on ``mtime_ns`` as well as the path so a redeployed export is not
    served from a stale entry; bounded by ``maxsize`` so the cache cannot be
    grown without limit by requesting many distinct ids.

    Security: the values are re-validated here rather than trusted from the
    caller. They reach this function straight from the URL, and land inside
    the RSC flight-data ``<script>`` block of the response — so before this
    check, ``GET /hosts/x"</script><script>alert(1)</script>/`` closed the
    string and the script tag and executed. The CSP in
    ``SecurityHeadersMiddleware`` allows ``script-src 'unsafe-inline'``, and
    the CSRF cookie is deliberately readable by JavaScript, so injected code
    could drive any authenticated mutation same-origin.

    No output escaping is layered on top, deliberately. The placeholder
    appears in two different quoting contexts — plain ``"placeholder"`` in
    ``.txt`` payloads and backslash-escaped ``\\"placeholder\\"`` in HTML —
    which need *different* escaping, and a single escape helper applied to
    both would be wrong in one of them. With the values constrained to digits
    there is nothing to escape, so the allow-list carries the safety on its
    own. Widening ``_SAFE_DYNAMIC_SEGMENT`` therefore requires adding
    context-correct escaping at the same time.
    """
    if not dynamic_values:
        return None
    if not all(_SAFE_DYNAMIC_SEGMENT.fullmatch(v) for v in dynamic_values):
        return None
    content = Path(file_path).read_text(encoding="utf-8")
    content = _substitute_placeholders(content, dynamic_values, '\\"')
    content = _substitute_placeholders(content, dynamic_values, '"')
    return content


def _resolve_dynamic_route(static_dir: Path, full_path: str) -> tuple[Path, tuple[str, ...]] | None:
    """Resolve a Next.js dynamic route by substituting missing path segments
    with the generateStaticParams placeholder directory.

    Returns ``(resolved_file, dynamic_values)`` where *dynamic_values* holds
    the original URL segments that were substituted, **in path order** —
    ``("1",)`` for ``/groups/1/`` and ``("7", "12")`` for
    ``/hosts/7/actions/runs/12/``. Empty when no substitution was needed.

    BUG-76: this used to keep a single value, so the second segment of a
    nested route overwrote the first and both were rewritten to it.

    A segment that is not a permitted id (see ``_SAFE_DYNAMIC_SEGMENT``) is
    refused outright rather than substituted, so the caller falls through to
    the SPA shell and the client router resolves the route. Legitimate URLs
    are unaffected: every dynamic segment the app produces is an integer id.
    """
    parts = Path(full_path).parts
    if not parts:
        return None
    current = static_dir
    dynamic_values: list[str] = []
    for part in parts:
        candidate = current / part
        if candidate.is_dir():
            current = candidate
        elif candidate.is_file():
            return (candidate, tuple(dynamic_values))
        else:
            placeholder = current / "placeholder"
            if placeholder.is_dir():
                if not _SAFE_DYNAMIC_SEGMENT.fullmatch(part):
                    return None
                dynamic_values.append(part)
                current = placeholder
            else:
                return None
    index = current / "index.html"
    if index.is_file():
        return (index, tuple(dynamic_values))
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

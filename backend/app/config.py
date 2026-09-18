"""
LabDog configuration — loaded from TOML config file.

Resolution order (first found wins):
  1. LABDOG_CONFIG env var (explicit path)
  2. labdog.toml in project root  (development)
  3. /etc/labdog/labdog.toml    (production / packaged install)

Environment variables can override any setting using double-underscore
separators for nested keys.  E.g. LABDOG_SERVER__PORT=9000 overrides
[server] port.
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
import types
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin
from urllib.parse import urlparse

from pydantic import BaseModel, model_validator

from app.key_format import decode_master_key

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    static_dir: str = ""
    trusted_proxies: list[str] = []
    forwarded_allow_ips: str = "127.0.0.1"
    #: SEC-34. ``/docs``, ``/redoc`` and ``/openapi.json`` are served
    #: without authentication and enumerate every endpoint, its
    #: parameters and its schemas. That is a convenience in development
    #: and a map for anyone who reaches the port in production, so it is
    #: off unless asked for. ``dev/labdog.toml`` turns it on.
    expose_docs: bool = False


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://labdog:labdog@localhost:5432/labdog"
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379/0"


#: Placeholder values that must never reach a running instance. The
#: first two are this file's own defaults; ``CHANGE_ME`` is what
#: ``packaging/etc/labdog.toml`` ships, and it was **not** on this list —
#: so a packaged install that skipped the "generate your secrets" step
#: booted happily with a nine-character signing key that is public in
#: this repository (SEC-31).
_INSECURE_DEFAULTS = {
    "change-me-in-production",
    "change-me-32-bytes-base64-encoded",
    "CHANGE_ME",
}

#: HS256 signing keys shorter than this are brute-forceable offline, and
#: forging the auth cookie is forging any account. ``openssl rand
#: -base64 32`` — what the docs already tell operators to run — produces
#: 44 characters.
_MIN_SECRET_KEY_LENGTH = 32


class SecurityConfig(BaseModel):
    secret_key: str = "change-me-in-production"
    encryption_key: str = "change-me-32-bytes-base64-encoded"
    labdog_server_ip: str = "127.0.0.1"
    allowed_origins: list[str] = ["http://localhost:3000"]
    cookie_secure: bool = False
    cookie_domain: str = ""
    session_lifetime_seconds: int = 86400
    csrf_token_bytes: int = 32


class TLSConfig(BaseModel):
    cert_file: str = ""
    key_file: str = ""
    force_https: bool = False


class RateLimitConfig(BaseModel):
    enabled: bool = True
    login: str = "5/minute"
    api: str = "100/minute"


class MetricsConfig(BaseModel):
    """Prometheus ``/metrics`` exporter.

    Disabled by default and deliberately **file-level, not a DB-backed
    ``AppSetting``**: the endpoint is unauthenticated (like ``GET
    /api/version``) once enabled, so flipping it on must require server
    access (editing ``labdog.toml`` / setting ``LABDOG_METRICS__ENABLED``
    and restarting), not just a LabDog login. ``/api/settings`` gates on
    ``current_active_user`` (not superuser), so a DB toggle would let any
    authenticated — or XSS'd — session expose fleet state with one PATCH.
    Protection for the enabled endpoint itself is expected to be a reverse
    proxy allow-list, not application-level auth.
    """

    enabled: bool = False
    cache_ttl_seconds: float = 15.0
    action_key_label: bool = True
    #: Hard timeout for the Redis broker probe that produces
    #: ``labdog_broker_queue_depth``. Short on purpose: the broker is a
    #: second failure domain in an unauthenticated request path, so a
    #: broker that is not answering promptly is reported as unreachable
    #: rather than waited for. See ``app.metrics.broker``.
    broker_timeout_seconds: float = 0.2


class AlertsConfig(BaseModel):
    """Inbound alert webhook.

    ``webhook_token`` is the shared bearer token a Grafana contact point
    must send. **File-level rather than a DB-backed ``AppSetting``**, for
    the same reason as ``MetricsConfig`` above: ``/api/settings`` gates on
    ``current_active_user``, so a token stored there could be read — and
    rewritten — by any authenticated or XSS'd session, which is not where
    a shared secret belongs.

    Empty by default, and an empty token means the endpoint refuses
    everything rather than accepting anything. An alert receiver that
    silently accepted unauthenticated POSTs would let anyone on the
    network write rows LabDog might then spend money investigating.
    """

    webhook_token: str = ""


class LoggingConfig(BaseModel):
    level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    format: Literal["text", "json"] = "text"
    audit_retention_days: int = 90
    #: Separate from the audit window on purpose: an audit trail is usually
    #: wanted for longer than an ansible transcript (BUG-73).
    run_retention_days: int = 90
    #: Individual drift-check samples. Separate again: these are small, and
    #: they feed a trend chart that offers up to 90 days, so the window
    #: should not fall below what the chart can ask for. Deleted samples are
    #: folded into ``drift_sample_rollup`` first — see
    #: ``app.tasks.drift_retention``.
    drift_retention_days: int = 90


class SSHConfig(BaseModel):
    max_sessions_per_user: int = 5
    max_total_sessions: int = 50
    idle_timeout_seconds: int = 1800
    connect_timeout: int = 10
    #: Fallback for the ``ssh.command_timeout`` app setting, used before the
    #: settings cache is warm. Bounds one command on an already-open session;
    #: the connect half is ``connect_timeout`` above.
    command_timeout: int = 60
    default_port: int = 22


class DiscoveryConfig(BaseModel):
    min_prefix: int = 20
    scan_timeout: float = 1.0
    max_concurrent: int = 100
    max_bulk_add: int = 50
    rediscovery_enabled: bool = False
    rediscovery_interval_minutes: int = 60


class CeleryConfig(BaseModel):
    concurrency: int = 4
    #: Slots on the dedicated orchestrator worker. Each slot holds one
    #: ``run_action`` sitting in ``result.join()``; the work it is waiting
    #: for runs on the ``work`` pool, so this caps concurrent action *runs*,
    #: not concurrent host operations.
    #:
    #: Exceeding it only queues orchestrators — it cannot deadlock, which
    #: is the whole point of the separate worker — so this is a throughput
    #: knob, not a safety one. Kept at 4 because prefork forks a full
    #: interpreter per slot and a mostly-idle one still costs its RSS;
    #: raise it on an install that runs many schedules on the same minute.
    #: A thread pool would be cheaper but Celery cannot enforce
    #: ``soft_time_limit`` on one, and the orchestrator's 12h soft limit is
    #: what finalises a wedged run.
    orchestrator_concurrency: int = 4
    max_tasks_per_child: int = 100


class DriftConfig(BaseModel):
    check_interval_minutes: int = 30


class HostsConfig(BaseModel):
    ip_recheck_on_drift: bool = True


class AnsibleConfig(BaseModel):
    """Ansible / action-pack runtime configuration.

    Pack sources are not configured here — DB-backed packs are managed
    via the admin UI at ``/settings/packs``. This section only carries
    filesystem paths LabDog uses to materialise those packs.
    """

    packs_root_dir: str = "/var/lib/labdog/packs"
    """Directory under which DB-backed packs are checked out.

    Each pack gets a subdirectory named after its integer id. Must be
    writable by the labdog process; for container deploys, mount a
    persistent volume here so checkouts survive restarts.
    """


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    redis: RedisConfig = RedisConfig()
    security: SecurityConfig = SecurityConfig()
    tls: TLSConfig = TLSConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    metrics: MetricsConfig = MetricsConfig()
    alerts: AlertsConfig = AlertsConfig()
    logging: LoggingConfig = LoggingConfig()
    ssh: SSHConfig = SSHConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    celery: CeleryConfig = CeleryConfig()
    drift: DriftConfig = DriftConfig()
    hosts: HostsConfig = HostsConfig()
    ansible: AnsibleConfig = AnsibleConfig()

    @model_validator(mode="before")
    @classmethod
    def _apply_env_overrides(cls, values: dict) -> dict:
        """Allow env-var overrides with LABDOG_ prefix and __ separators.

        ``__`` separates path segments, so nested models are reachable too:
          * ``LABDOG_SERVER__PORT=9000`` → ``server.port = 9000``
          * ``LABDOG_ANSIBLE__DEFAULT_PACK__REPO=…`` → ``ansible.default_pack.repo = …``

        List and dict-typed fields accept either a JSON literal
        (``'["a","b"]'``) or a comma-separated string (``'a,b'``). Scalar
        fields are passed through as raw strings — pydantic handles coercion.
        """
        prefix = "LABDOG_"
        for key, raw in os.environ.items():
            if not key.startswith(prefix):
                continue
            parts = key[len(prefix) :].lower().split("__")
            if len(parts) < 2:
                continue
            coerced = _coerce_env_value(cls, parts, raw)
            _set_nested(values, parts, coerced)
        return values


def _unwrap_optional(annotation: Any) -> Any:
    """Reduce ``X | None`` / ``Optional[X]`` to ``X`` so callers see the
    substantive type. Other unions are left intact."""
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _resolve_path_annotation(settings_cls: type[BaseModel], path: list[str]) -> Any | None:
    """Walk ``path`` through nested BaseModel fields and return the leaf
    annotation. Returns ``None`` if any segment doesn't exist on the model
    so the caller can fall back to passing the raw string through."""
    annotation: Any = settings_cls
    for segment in path:
        fields = getattr(annotation, "model_fields", None)
        if fields is None or segment not in fields:
            return None
        annotation = _unwrap_optional(fields[segment].annotation)
    return annotation


def _set_nested(container: dict, path: list[str], value: Any) -> None:
    """Assign *value* at ``container[path[0]][path[1]]…[path[-1]]``, creating
    intermediate dicts as needed. Existing non-dict values at interior
    segments are overwritten with a dict — env overrides beat whatever was
    in the TOML file, consistent with the prior two-level behaviour."""
    target = container
    for segment in path[:-1]:
        existing = target.get(segment)
        if not isinstance(existing, dict):
            existing = {}
            target[segment] = existing
        target = existing
    target[path[-1]] = value


def _coerce_env_value(settings_cls: type[BaseModel], path: list[str], raw: str) -> Any:
    """Parse an env-var string into the shape of the target field.

    List and dict fields accept JSON (``'["a","b"]'``) or, as a shorthand
    for lists of scalars, comma-separated strings (``'a,b,c'``). Scalars
    pass through untouched for pydantic to coerce.
    """
    annotation = _resolve_path_annotation(settings_cls, path)
    if annotation is None:
        return raw
    origin = get_origin(annotation)
    stripped = raw.strip()

    if origin is list:
        if stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        if stripped == "":
            return []
        return [part.strip() for part in stripped.split(",") if part.strip()]

    if origin is dict:
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        return raw

    return raw


# ---------------------------------------------------------------------------
# Config-file discovery
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _find_config_path() -> Path | None:
    """Return the first config file that exists, or None."""
    explicit = os.environ.get("LABDOG_CONFIG")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        return None

    candidates = [
        _PROJECT_ROOT / "labdog.toml",
        Path("/etc/labdog/labdog.toml"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_toml(path: Path | None) -> dict:
    """Read and parse a TOML file, returning an empty dict if absent."""
    if path is None:
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


_LOCALHOST_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}


def _is_localhost_origin(origin: str) -> bool:
    """Return True if the parsed hostname of *origin* is a loopback/unspecified address."""
    try:
        hostname = urlparse(origin).hostname or ""
    except Exception:
        return False
    if hostname in _LOCALHOST_HOSTNAMES:
        return True
    # Catch the entire 127.0.0.0/8 range (127.x.x.x).
    if hostname.startswith("127."):
        parts = hostname.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return True
    return False


def _validate_required(s: Settings) -> None:
    """Raise on insecure or missing required settings at startup."""
    errors: list[str] = []
    if s.security.secret_key in _INSECURE_DEFAULTS:
        errors.append(
            "security.secret_key is not set. "
            "Set LABDOG_SECURITY__SECRET_KEY or [security] secret_key in labdog.toml."
        )
    elif len(s.security.secret_key) < _MIN_SECRET_KEY_LENGTH:
        # Chained to the placeholder branch: a placeholder is also too
        # short, and two errors about one value help nobody.
        errors.append(
            f"security.secret_key is only {len(s.security.secret_key)} characters. "
            f"It signs the auth cookie, so a key short enough to brute-force "
            f"offline is a key that forges any account, including a superuser's. "
            f"Use at least {_MIN_SECRET_KEY_LENGTH}: openssl rand -base64 32"
        )

    if s.security.encryption_key in _INSECURE_DEFAULTS:
        errors.append(
            "security.encryption_key is not set. "
            "Generate one with: python -m app.crypto.key_management "
            "and set LABDOG_SECURITY__ENCRYPTION_KEY"
            " or [security] encryption_key in labdog.toml."
        )
    else:
        # Checked here so a malformed key fails at boot rather than at
        # the first host sync, hours later, as a task traceback.
        try:
            decode_master_key(s.security.encryption_key)
        except ValueError as exc:
            errors.append(f"security.encryption_key is unusable: {exc}")

    # CORS: allow_credentials is hardcoded True in main.py, and Starlette
    # answers a wildcard-with-credentials by reflecting whatever Origin
    # the request carried. That is not "open to everyone" in the harmless
    # sense — it is credentialed cross-origin access from any site the
    # victim visits, with the session cookie attached.
    if "*" in s.security.allowed_origins:
        errors.append(
            "security.allowed_origins contains '*' and LabDog always sends "
            "credentials, so every origin would be reflected back as allowed — "
            "any website a logged-in user visits could drive the API as them. "
            "List the frontend URL(s) explicitly."
        )

    if s.security.cookie_secure:
        for origin in s.security.allowed_origins:
            if _is_localhost_origin(origin):
                errors.append(
                    f"security.allowed_origins contains {origin!r} but "
                    "security.cookie_secure=True. "
                    "Localhost origins should only be used in dev (when cookie_secure=False). "
                    "Set allowed_origins to your production frontend URL(s)."
                )
    else:
        # A warning, not an error. Plenty of homelabs run this over plain
        # HTTP on a trusted LAN, and refusing to start would be wrong for
        # them — but on a routable origin the auth cookie travels without
        # the Secure flag, so any plaintext request leaks the session.
        remote = [o for o in s.security.allowed_origins if not _is_localhost_origin(o)]
        if remote:
            logger.warning(
                "security.cookie_secure is False but allowed_origins includes %s. "
                "The session cookie will be sent over plain HTTP. Set cookie_secure "
                "to true once LabDog is behind TLS.",
                ", ".join(repr(o) for o in remote),
            )

    if errors:
        raise SystemExit("FATAL: LabDog cannot start:\n  - " + "\n  - ".join(errors))


def load_settings() -> Settings:
    """Build a Settings instance from the TOML file + env overrides."""
    path = _find_config_path()
    data = _load_toml(path)
    s = Settings(**data)
    _validate_required(s)
    return s


settings = load_settings()

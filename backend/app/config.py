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
import os
import tomllib
import types
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, model_validator

# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    static_dir: str = ""
    trusted_proxies: list[str] = []
    forwarded_allow_ips: str = "127.0.0.1"


class DatabaseConfig(BaseModel):
    url: str = "postgresql+asyncpg://labdog:labdog@localhost:5432/labdog"
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379/0"


_INSECURE_DEFAULTS = {"change-me-in-production", "change-me-32-bytes-base64-encoded"}


class SecurityConfig(BaseModel):
    secret_key: str = "change-me-in-production"
    encryption_key: str = "change-me-32-bytes-base64-encoded"
    labdog_server_ip: str = "127.0.0.1"
    allowed_origins: list[str] = ["http://localhost:3000"]
    cookie_secure: bool = False
    cookie_domain: str = ""
    session_lifetime_seconds: int = 86400


class TLSConfig(BaseModel):
    cert_file: str = ""
    key_file: str = ""
    force_https: bool = False


class RateLimitConfig(BaseModel):
    enabled: bool = True
    login: str = "5/minute"
    api: str = "100/minute"


class LoggingConfig(BaseModel):
    level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    format: Literal["text", "json"] = "text"
    audit_retention_days: int = 90


class SSHConfig(BaseModel):
    max_sessions_per_user: int = 5
    max_total_sessions: int = 50
    idle_timeout_seconds: int = 1800
    connect_timeout: int = 10
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


def _validate_required(s: Settings) -> None:
    """Raise on insecure or missing required settings at startup."""
    errors: list[str] = []
    if s.security.secret_key in _INSECURE_DEFAULTS:
        errors.append(
            "security.secret_key is not set. "
            "Set LABDOG_SECURITY__SECRET_KEY or [security] secret_key in labdog.toml."
        )
    if s.security.encryption_key in _INSECURE_DEFAULTS:
        errors.append(
            "security.encryption_key is not set. "
            "Generate one with: python -m app.crypto.key_management "
            "and set LABDOG_SECURITY__ENCRYPTION_KEY"
            " or [security] encryption_key in labdog.toml."
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

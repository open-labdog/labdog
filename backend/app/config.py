"""
Barricade configuration — loaded from TOML config file.

Resolution order (first found wins):
  1. BARRICADE_CONFIG env var (explicit path)
  2. barricade.toml in project root  (development)
  3. /etc/barricade/barricade.toml    (production / packaged install)

Environment variables can override any setting using double-underscore
separators for nested keys.  E.g. BARRICADE_SERVER__PORT=9000 overrides
[server] port.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

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
    url: str = "postgresql+asyncpg://barricade:barricade@localhost:5432/barricade"
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379/0"


_INSECURE_DEFAULTS = {"change-me-in-production", "change-me-32-bytes-base64-encoded"}


class SecurityConfig(BaseModel):
    secret_key: str = "change-me-in-production"
    encryption_key: str = "change-me-32-bytes-base64-encoded"
    barricade_server_ip: str = "127.0.0.1"
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

    @model_validator(mode="before")
    @classmethod
    def _apply_env_overrides(cls, values: dict) -> dict:
        """Allow env-var overrides with BARRICADE_ prefix and __ separators.

        Example: BARRICADE_SERVER__PORT=9000  →  values["server"]["port"] = 9000
        """
        prefix = "BARRICADE_"
        for key, raw in os.environ.items():
            if not key.startswith(prefix):
                continue
            parts = key[len(prefix) :].lower().split("__")
            if len(parts) < 2:
                continue
            section = parts[0]
            field = "__".join(parts[1:])
            if section not in values:
                values[section] = {}
            values[section][field] = raw
        return values


# ---------------------------------------------------------------------------
# Config-file discovery
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _find_config_path() -> Path | None:
    """Return the first config file that exists, or None."""
    explicit = os.environ.get("BARRICADE_CONFIG")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        return None

    candidates = [
        _PROJECT_ROOT / "barricade.toml",
        Path("/etc/barricade/barricade.toml"),
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
            "Set BARRICADE_SECURITY__SECRET_KEY or [security] secret_key in barricade.toml."
        )
    if s.security.encryption_key in _INSECURE_DEFAULTS:
        errors.append(
            "security.encryption_key is not set. "
            "Generate one with: python -m app.crypto.key_management "
            "and set BARRICADE_SECURITY__ENCRYPTION_KEY"
            " or [security] encryption_key in barricade.toml."
        )
    if errors:
        raise SystemExit("FATAL: Barricade cannot start:\n  - " + "\n  - ".join(errors))


def load_settings() -> Settings:
    """Build a Settings instance from the TOML file + env overrides."""
    path = _find_config_path()
    data = _load_toml(path)
    s = Settings(**data)
    _validate_required(s)
    return s


settings = load_settings()

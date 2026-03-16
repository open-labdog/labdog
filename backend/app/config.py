from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    DATABASE_URL: str = "postgresql+asyncpg://barricade:barricade@localhost:5432/barricade"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production"
    ENCRYPTION_KEY: str = "change-me-32-bytes-base64-encoded"
    BARRICADE_SERVER_IP: str = "127.0.0.1"
    DRIFT_CHECK_INTERVAL_MINUTES: int = 30
    DISCOVERY_MIN_PREFIX: int = 20  # smallest allowed CIDR prefix (/20 = 4094 hosts)
    DISCOVERY_SCAN_TIMEOUT: float = 1.0  # per-host TCP timeout in seconds
    DISCOVERY_MAX_CONCURRENT: int = 100  # max simultaneous TCP connections
    DISCOVERY_MAX_BULK_ADD: int = 50  # max hosts per bulk-add request


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    DATABASE_URL: str = "postgresql+asyncpg://barricade:barricade@localhost:5432/barricade"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production"
    ENCRYPTION_KEY: str = "change-me-32-bytes-base64-encoded"
    BARRICADE_SERVER_IP: str = "127.0.0.1"
    DRIFT_CHECK_INTERVAL_MINUTES: int = 30


settings = Settings()

"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEV_JWT_SECRET = "oxh-local-dev-secret-change-in-production-32chars-min"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    seed_on_startup: bool = False

    database_url: str = "postgresql+psycopg://oxh_user:oxh_password@localhost:5432/oxh_prior_auth"
    test_database_url: str = (
        "postgresql+psycopg://oxh_user:oxh_password@localhost:5432/oxh_prior_auth_test"
    )

    jwt_secret_key: str = DEFAULT_DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "oxh-identity"
    jwt_audience: str = "oxh-pa"
    jwt_access_token_expire_hours: int = 720

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        env = self.app_env.strip().lower()
        if env not in {"development", "dev", "test", "testing"}:
            if self.jwt_secret_key == DEFAULT_DEV_JWT_SECRET or len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be overridden with a 32+ character secret outside development/test."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

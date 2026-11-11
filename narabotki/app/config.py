"""Конфигурация приложения (pydantic-settings). Секреты — только из env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SberID
    SBERID_CLIENT_ID: str = ""
    SBERID_CLIENT_SECRET: str = ""
    SBERID_REDIRECT_URI: str = "http://localhost:8000/auth/sberid/callback"

    # GigaChat
    GIGACHAT_CLIENT_ID: str = ""
    GIGACHAT_CLIENT_SECRET: str = ""
    GIGACHAT_SCOPE: str = "GIGACHAT_API_PERS"  # PERS | B2B | CORP
    GIGACHAT_CA_BUNDLE: str | None = None      # НУЦ Минцифры для прода
    GIGACHAT_VERIFY_SSL: bool = True

    # Инфраструктура
    DATABASE_URL: str = "postgresql+asyncpg://sberone:sberone@localhost:5432/sberone"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Секреты
    PSEUDONYM_KEY: str = "change_me_hmac_key"
    ENCRYPTION_KEY: str = "change_me_fernet_key"
    JWT_SECRET: str = "change_me_jwt_secret"

    # Compliance
    DATA_RESIDENCY: str = "RU"
    SESSION_TTL_SECONDS: int = 12 * 60 * 60
    AUDIT_RETENTION_DAYS: int = 1825  # 5 лет


settings = Settings()

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    admin_telegram_ids: str = Field(default="", alias="ADMIN_TELEGRAM_IDS")
    credentials_encryption_key: str = Field(alias="CREDENTIALS_ENCRYPTION_KEY")
    public_base_url: str = Field(default="http://localhost:8080", alias="PUBLIC_BASE_URL")
    poll_interval_sec: int = Field(default=300, alias="POLL_INTERVAL_SEC")
    seen_items_ttl_days: int = Field(default=90, alias="SEEN_ITEMS_TTL_DAYS")
    database_path: str = Field(default="data/bot.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    web_host: str = Field(default="0.0.0.0", alias="WEB_HOST")
    web_port: int = Field(default=8080, alias="WEB_PORT")
    http_proxy: str = Field(default="", alias="HTTP_PROXY")

    @field_validator("public_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def admin_ids(self) -> set[int]:
        if not self.admin_telegram_ids.strip():
            return set()
        return {
            int(part.strip())
            for part in self.admin_telegram_ids.split(",")
            if part.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

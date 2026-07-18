from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    allowed_user_ids: str = Field(default="", alias="ALLOWED_USER_IDS")
    watch_poll_interval_seconds: int = Field(
        default=120,
        alias="WATCH_POLL_INTERVAL_SECONDS",
    )
    ebay_app_id: str = Field(default="", alias="EBAY_APP_ID")
    ebay_cert_id: str = Field(default="", alias="EBAY_CERT_ID")
    ebay_marketplace_id: str = Field(default="EBAY_US", alias="EBAY_MARKETPLACE_ID")
    database_path: str = Field(default="data/bot.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def allowed_ids(self) -> set[int]:
        if not self.allowed_user_ids.strip():
            return set()
        return {
            int(part.strip())
            for part in self.allowed_user_ids.split(",")
            if part.strip()
        }

    @property
    def ebay_api_enabled(self) -> bool:
        return bool(self.ebay_app_id and self.ebay_cert_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()

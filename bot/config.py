from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Validated configuration loaded from environment variables and .env."""
    bot_token: str
    database_url: str
    drivers_chat_id: int
    telegram_admin_ids: str = ""
    support_username: str = "makeshixdevAI"
    timezone: str = "Asia/Bishkek"
    log_level: str = "INFO"
    payment_base_url: str = "https://payments.example.invalid/checkout"
    payment_webhook_secret: str = ""
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    subscription_price_7_days: int = 300
    subscription_price_30_days: int = 900
    payment_currency: str = "KGS"
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1] / ".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def admin_ids(self) -> set[int]:
        """Return configured administrator Telegram IDs as a set of integers."""
        return {int(item.strip()) for item in self.telegram_admin_ids.split(",") if item.strip()}

settings = Settings()

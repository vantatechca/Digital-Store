from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Digital Store"
    secret_key: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./store.db"
    base_url: str = "http://localhost:8000"

    admin_email: str = "admin@store.local"
    admin_password: str = "admin123"

    resend_api_key: str = ""
    mail_from: str = "support@order-confirmed.com"
    mail_from_name: str = "Digital Store"

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""
    enable_test_provider: bool = True

    download_token_ttl_hours: int = 72
    max_downloads_per_item: int = 5

    access_token_ttl_minutes: int = 60 * 12


@lru_cache
def get_settings() -> Settings:
    return Settings()

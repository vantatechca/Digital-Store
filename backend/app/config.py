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

    # Whop embedded checkout
    whop_api_key: str = ""
    whop_product_id: str = ""       # prod_… (the inline price is attached to this product)
    whop_plan_id: str = ""          # plan_… (optional; kept for reference)
    whop_webhook_secret: str = ""   # Standard Webhooks signing secret from the Whop dashboard
    # https://api.whop.com (production) or https://sandbox-api.whop.com (sandbox/test)
    whop_api_base: str = "https://api.whop.com"

    download_token_ttl_hours: int = 720   # 30 days — buyer-friendly; security comes from streaming + unguessable tokens
    max_downloads_per_item: int = 10

    # Cloudflare R2 (S3-compatible) for hosting downloadable files.
    # When set, a delivery rule's download_url that is an object KEY (not http…)
    # is served via a short-lived signed URL so files can't be reshared.
    # Works with any S3-compatible host (Cloudflare R2, Supabase Storage, Backblaze…).
    r2_account_id: str = ""             # R2 only (used to build the default endpoint)
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_endpoint: str = ""               # full S3 endpoint (required for non-R2, e.g. Supabase)
    r2_region: str = "auto"             # "auto" for R2; the project region for Supabase (e.g. us-east-1)
    download_presign_ttl_seconds: int = 60

    access_token_ttl_minutes: int = 60 * 12


@lru_cache
def get_settings() -> Settings:
    return Settings()

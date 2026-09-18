"""Application configuration.

Every external dependency has a safe, clearly-labelled development adapter so
local development and CI never require real credentials. Production values are
supplied via environment variables only — see .env.example.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core ---
    # canonical origin, used in invitation links and emails
    app_base_url: str = "http://localhost:8000"
    environment: str = "development"  # development | staging | production
    secret_key: str = "dev-only-insecure-secret-change-me"
    database_url: str = f"sqlite:///{BASE_DIR / 'dev.db'}"
    media_dir: str = str(BASE_DIR / "media")  # used when storage_backend=local

    # cookie security — set false only for http localhost development
    session_cookie_secure: bool = False

    # --- Auth ---
    # 'clerk' in production; 'dev' is an INSECURE local adapter that emails a
    # one-time code (printed to console when email_provider=console).
    auth_provider: str = "dev"
    clerk_publishable_key: str = ""
    clerk_secret_key: str = ""
    clerk_jwks_url: str = ""  # e.g. https://<slug>.clerk.accounts.dev/.well-known/jwks.json
    session_ttl_days: int = 14

    # --- Admin bootstrap ---
    # Comma-separated emails that become administrators on first sign-in.
    # This is the ONLY way the first admin is created; members can never
    # grant themselves admin. Later admins can be granted from the dashboard.
    admin_emails: str = ""

    # --- Email ---
    # 'resend' in production; 'console' prints emails to the server log and,
    # combined with auth_provider=dev, surfaces OTP codes in the dev UI.
    email_provider: str = "console"
    resend_api_key: str = ""
    email_from: str = "Exponential <hello@exponential.nyc>"
    # when true, outbox sends are skipped entirely (tests/CI safety switch)
    email_disable_sends: bool = False

    # --- Object storage ---
    # 'local' (dev) stores under media_dir and streams through an
    # authenticated route. 's3' stores in a private bucket and streams
    # through the same authenticated route (no public URLs ever exist).
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_endpoint_url: str = ""  # for S3-compatible stores (R2, Tigris, MinIO)

    # --- Membership ---
    member_cap: int = 80  # initial cap; changes require an admin action
    invite_ttl_days: int = 14
    login_code_ttl_minutes: int = 10

    # --- Misc ---
    seed_demo_data: bool = False  # staging/dev only — never in production

    @property
    def is_dev_auth(self) -> bool:
        return self.auth_provider == "dev"

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

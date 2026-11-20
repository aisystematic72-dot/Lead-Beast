"""All settings. No other module reads environment variables directly."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LB_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://lb:lb@localhost:5432/lb"
    secret_key: SecretStr = SecretStr("change-me")
    environment: str = "development"

    operator_name: str = ""
    operator_phone: str = ""
    operator_address: str = ""
    operator_email: str = ""
    operator_password_hash: str = ""  # sha256 hex; set with `leadbeast user set-password`
    api_token_hash: str = ""

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # Gemini (hosted). Accepts LB_GEMINI_API_KEY or the bare GEMINI_API_KEY already in .env.
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("LB_GEMINI_API_KEY", "GEMINI_API_KEY"))
    gemini_model: str = "gemini-flash-latest"
    gemini_fallback_model: str = "gemini-flash-lite-latest"  # used when the main one is overloaded
    gemini_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # Facebook activity rules (see leadbeast.social.fb_activity)
    fb_sample_posts: int = 5          # judge the page on its most recent N posts
    fb_active_max_gap_days: float = 14.0   # average gap at or below this = actively posting
    fb_stale_after_days: int = 60     # newest post older than this = not actively marketing

    monthly_cap_cents: int = 3000
    # Overpass rejects (HTTP 406) the old bot-style agent with a placeholder contact address.
    user_agent: str = "LeadBeast/1.0 (self-hosted lead research)"
    daily_request_cap_per_host: int = 500
    per_host_min_interval_s: float = 2.0
    http_timeout_s: float = 15.0

    packs_dir: Path = Path("packs")
    reports_dir: Path = Path("var/reports")
    secrets_file: Path | None = None

    golden_precision_threshold: float = 0.90
    identity_merge_threshold: float = 0.88
    identity_review_threshold: float = 0.70

    ses_region: str = ""
    ses_sender: str = ""
    smtp_host: str = ""  # e.g. email-smtp.us-east-1.amazonaws.com for Amazon SES
    smtp_port: int = 587
    smtp_user_ref: str = "LB_SMTP_USER"
    smtp_password_ref: str = "LB_SMTP_PASSWORD"
    sender_email: str = ""
    unsubscribe_base_url: str = "https://example.com/u"
    public_base_url: str = "http://localhost:8000"
    default_recipient_tz: str = "America/New_York"
    espocrm_url: str = ""
    espocrm_api_key_ref: str = "LB_ESPOCRM_API_KEY"
    ntfy_url: str = "http://ntfy"
    distress_words: list[str] = Field(
        default_factory=lambda: [
            "desperate", "failing", "struggling", "going under", "bankrupt",
            "dying", "losing customers", "in trouble", "can't keep up",
        ]
    )

    @property
    def operator_complete(self) -> bool:
        return bool(self.operator_name and self.operator_phone and self.operator_address)


@lru_cache
def get_settings() -> Settings:
    return Settings()

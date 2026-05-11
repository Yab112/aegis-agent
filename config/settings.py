from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    gemini_api_key: str

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def strip_gemini_key(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    # Gemini 1.5 is retired on the v1beta API; use 2.5+ (override via GEMINI_MODEL).
    gemini_model: str = "gemini-2.5-flash"

    @field_validator("gemini_model", mode="before")
    @classmethod
    def strip_gemini_model(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    # Embeddings
    hf_api_token: str
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Supabase
    supabase_url: str
    supabase_service_key: str
    # Only for local scripts (e.g. apply_supabase_schema.py); the FastAPI app uses REST + service key.
    supabase_db_password: str | None = None

    # Google
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    google_calendar_id: str
    calendar_timezone: str = "Africa/Addis_Ababa"

    # Telegram (handoff alerts to owner — see docs/telegram_setup.md)
    telegram_bot_token: str
    telegram_chat_id: str
    # Optional but recommended: pass the same value to setWebhook(secret_token=…).
    # Telegram sends it as header X-Telegram-Bot-Api-Secret-Token on each update.
    telegram_webhook_secret: str | None = None

    @field_validator("telegram_webhook_secret", mode="before")
    @classmethod
    def strip_telegram_webhook_secret(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @field_validator("telegram_bot_token", "telegram_chat_id", mode="before")
    @classmethod
    def strip_telegram_secrets(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    # RAG
    rag_top_k: int = 5
    rag_confidence_threshold: float = 0.72
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64

    # API
    api_secret_key: str
    allowed_origins: str = (
        "https://yabibal.site,https://www.yabibal.site,http://localhost:3000"
    )
    port: int = 8000
    # Terminal: INFO=pipeline + HTTP; DEBUG=also httpx/lower-level Google noise
    log_level: str = "INFO"

    # Abuse protection and public API smoothing
    rate_limit_enabled: bool = True
    rate_limit_chat_per_minute: int = 15
    rate_limit_blog_per_minute: int = 120
    rate_limit_default_per_minute: int = 90
    blog_api_cache_ttl_seconds: int = 45

    # When blog_posts.image_url / og_image_url are null (old rows or failed cover upload), the
    # public API fills from this URL so Next.js meta tags always have an absolute OG image.
    blog_default_og_image: str | None = None

    @field_validator("blog_default_og_image", mode="before")
    @classmethod
    def strip_blog_default_og(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def strip_log_level(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    # Owner
    owner_name: str = "Yabibal"
    owner_role: str = "Full-Stack & AI Engineer"
    portfolio_url: str = "https://yabibal.site"

    # HTML email to visitors (Telegram reply relay): HTTPS logo URL (e.g. Cloudinary or site static).
    email_brand_logo_url: str | None = None
    # One line under the logo (value prop / positioning).
    email_brand_tagline: str | None = None
    # Optional second button (e.g. Calendly, /contact). Label defaults to "Book a call" when URL is set.
    email_brand_secondary_cta_url: str | None = None
    email_brand_secondary_cta_label: str | None = None
    email_brand_linkedin_url: str | None = None
    email_brand_github_url: str | None = None
    # When true, Gemini rewrites your Telegram draft into a professional email body before send.
    visitor_reply_email_polish: bool = True

    @field_validator("email_brand_logo_url", mode="before")
    @classmethod
    def strip_email_logo(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @field_validator(
        "email_brand_tagline",
        "email_brand_secondary_cta_url",
        "email_brand_secondary_cta_label",
        "email_brand_linkedin_url",
        "email_brand_github_url",
        mode="before",
    )
    @classmethod
    def strip_email_brand_optional(cls, v: object) -> object:
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @field_validator("visitor_reply_email_polish", mode="before")
    @classmethod
    def parse_visitor_reply_polish(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off")
        return v

    # Assistant persona (single voice for all chat — override with ASSISTANT_NAME in .env)
    assistant_name: str = "Aegis"

    @field_validator("assistant_name", mode="before")
    @classmethod
    def strip_assistant_name(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()

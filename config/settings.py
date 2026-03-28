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

    # WhatsApp
    whatsapp_phone_number_id: str
    whatsapp_access_token: str
    whatsapp_recipient_number: str

    # RAG
    rag_top_k: int = 5
    rag_confidence_threshold: float = 0.72
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64

    # API
    api_secret_key: str
    allowed_origins: str = "https://yabibal.site,http://localhost:3000"
    port: int = 8000
    # Terminal: INFO=pipeline + HTTP; DEBUG=also httpx/lower-level Google noise
    log_level: str = "INFO"

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

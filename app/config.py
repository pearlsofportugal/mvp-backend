"""Application settings loaded from environment variables."""
from typing import List
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Caminho absoluto para o ficheiro .env
ENV_FILE = Path(__file__).parent.parent / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "MVP-Scraper"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: List[str] = ["http://localhost:4200", "http://localhost:8000"]
    # Database
    database_url: str
    database_url_sync: str
    api_key: str = ""

    # Scraping defaults
    default_min_delay: float = 2.0
    default_max_delay: float = 5.0
    default_user_agent: str = "RealEstateResearchBot/1.0 (+contact: you@example.com)"
    default_max_pages: int = 10
    request_timeout: int = 120

    # AI / GenAI
    google_genai_api_key: str = ""
    google_genai_model: str = "gemini-2.5-flash"
    google_genai_temperature: float = 0.2
    ai_rate_limit_requests: int = 20   # pedidos permitidos por janela
    ai_rate_limit_window: int = 60     # janela em segundos
    @field_validator('database_url')
    @classmethod
    def validate_database_url(cls, v):
        if not v.startswith(('postgresql+asyncpg://', 'sqlite+aiosqlite://')):
            raise ValueError('database_url must use async driver')
        return v
    
    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v:
            import warnings
            warnings.warn(
                "API_KEY não configurada — endpoints desprotegidos.",
                stacklevel=2,
            )
        return v


settings = Settings()
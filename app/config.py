"""Application settings loaded from environment variables."""

from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Caminho absoluto para o ficheiro .env
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application

    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "MVP-Scraper"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    docs_enabled: bool | None = None
    cors_origins: list[str] = ["http://localhost:4200", 
                               "http://localhost:8080",
                               "https://frontend-app-41588214705.europe-west1.run.app"
                               ]
    
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
    ai_rate_limit_requests: int = 20
    ai_rate_limit_window: int = 60
    
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def effective_docs_enabled(self) -> bool:
        if self.docs_enabled is not None:
            return self.docs_enabled
        return not self.is_production

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError("DATABASE_URL must use an async driver")
        return value
    
    @field_validator("database_url_sync")
    @classmethod
    def validate_database_url_sync(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "sqlite:///")):
            raise ValueError("DATABASE_URL_SYNC must use a sync driver")
        return value
    
    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            import json

            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # @field_validator("api_key")
    # @classmethod
    # def validate_api_key(cls, v: str) -> str:
    #     if not v:
    #         import warnings
    #         warnings.warn(
    #             "API_KEY não configurada — endpoints desprotegidos.",
    #             stacklevel=2,
    #         )
    #     return v

    @model_validator(mode="after")
    def validate_production_settings(self):
        if self.is_production and self.debug:
            raise ValueError("DEBUG must be false in production")

        if self.is_production and not self.api_key.strip():
            raise ValueError("API_KEY must be set in production")

        if self.is_production and not self.cors_origins:
            raise ValueError("CORS_ORIGINS must contain at least one allowed origin in production")

        return self
settings = Settings()
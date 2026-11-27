from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "waypoint-dev-secret-change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WAYPOINT_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./waypoint.db"
    secret_key: str = _DEV_SECRET
    token_ttl_minutes: int = 60 * 24 * 7
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]
    static_dir: str | None = None

    gemini_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("WAYPOINT_GEMINI_API_KEY", "GEMINI_API_KEY")
    )
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = 12.0
    google_maps_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("WAYPOINT_GOOGLE_MAPS_API_KEY", "GOOGLE_MAPS_API_KEY")
    )

    quote_ttl_seconds: int = 900
    approval_ttl_seconds: int = 600
    supplier_ack_delay_seconds: float = 1.5
    auto_reconcile: bool = True
    simulator_seed: int = 4242
    simulator_lab_enabled: bool = True
    fixed_now: datetime | None = None
    chat_rate_per_minute: int = 30

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.environment == "production":
            if self.secret_key == _DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError("WAYPOINT_SECRET_KEY must be set to a 32+ char secret in production")
            if self.simulator_lab_enabled:
                raise ValueError("WAYPOINT_SIMULATOR_LAB_ENABLED must be false in production")
        return self

    @property
    def ai_provider(self) -> str:
        return "gemini" if self.gemini_api_key else "heuristic"


@lru_cache
def get_settings() -> Settings:
    return Settings()

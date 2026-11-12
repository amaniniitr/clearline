"""Runtime settings. Secrets come from the environment only (never from code or logs)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]  # clearline/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ROOT / "services/core/.env"), extra="ignore")

    database_url: str = "postgresql://localhost:5432/clearline"
    dbos_database_url: str | None = None
    app_version: str = "clearline-0.1.0"

    anthropic_api_key: str | None = None
    # Provider for reading/writing calls: gemini (Vertex AI, default) | claude
    model_provider: str = "gemini"
    vertex_api_key: str | None = None          # Vertex AI express-mode key (never logged)
    google_cloud_project: str | None = None
    google_cloud_location: str = "global"
    gemini_read_a: str = "gemini-3.8-flash"
    gemini_read_b: str = "gemini-3.5-flash-lite"
    gemini_reread: str = "gemini-4-argon"
    gemini_light: str = "gemini-3.5-flash-lite"
    gemini_agent: str = "gemini-3.5-flash-lite"
    demo_pace_ms: int = 0
    model_read_a: str = "claude-sonnet-5-5"
    model_read_b: str = "claude-haiku-5-5"
    model_agent: str = "claude-sonnet-5-5"
    model_narrator: str = "claude-haiku-5-5"

    # Jev: OpenRouter is the primary route; TypeSafe direct is the alternative.
    openrouter_api_key: str | None = None
    typesafe_api_key: str | None = None
    jev_model: str = "jev-1.14.0"
    jev_openrouter_model: str = "typesafe/jev-1.14"

    # live: call models · fixture: replay recorded calls only · auto: fixture if recorded, else live, else offline
    ai_mode: str = "auto"
    daily_spend_cap_usd: float = 10.0

    storage_dir: Path = ROOT / "var/files"
    fixtures_dir: Path = ROOT / "services/core/app/ai/fixtures"
    scenarios_dir: Path = ROOT / "scenarios/out"
    max_upload_mb: int = 15
    max_pages: int = 10
    cors_origins: str = "http://localhost:3000"
    cors_origin_regex: str = r"https://.*\.vercel\.app"

    def model_post_init(self, __context) -> None:
        if not self.dbos_database_url:
            if "localhost" in self.database_url or "127.0.0.1" in self.database_url:
                self.dbos_database_url = "postgresql://localhost:5432/clearline_dbos"
            else:
                # Managed Postgres providers grant one database; DBOS isolates its tables in the `dbos` schema.
                self.dbos_database_url = self.database_url

    @property
    def claude_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.vertex_api_key)

    @property
    def llm_enabled(self) -> bool:
        return self.gemini_enabled if self.model_provider == "gemini" else self.claude_enabled

    @property
    def jev_enabled(self) -> bool:
        return bool(self.openrouter_api_key or self.typesafe_api_key)


@lru_cache
def settings() -> Settings:
    s = Settings()
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    s.fixtures_dir.mkdir(parents=True, exist_ok=True)
    return s

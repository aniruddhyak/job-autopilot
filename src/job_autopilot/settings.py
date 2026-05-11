"""Application settings loaded from environment variables.

Uses ``pydantic-settings`` so settings are type-validated and can come from
a ``.env`` file or shell environment variables.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root = three levels up from this file
# (.../src/job_autopilot/settings.py → project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration for Job Autopilot."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Paths ---
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    config_dir: Path = Field(default=PROJECT_ROOT / "config")

    # --- Logging ---
    log_level: str = Field(default="INFO")

    # --- Dashboard ---
    dashboard_port: int = Field(default=8000)

    # --- LLM (used in Phase 2; included now for completeness) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"

    # ------------------------------------------------------------------
    # Convenience properties for paths used across the app
    # ------------------------------------------------------------------

    @property
    def sources_file(self) -> Path:
        return self.config_dir / "sources.json"

    @property
    def raw_jobs_file(self) -> Path:
        return self.data_dir / "raw_jobs.json"

    @property
    def runs_file(self) -> Path:
        return self.data_dir / "runs.json"
    
    @property
    def resume_file(self) -> Path:
        """Path to the resume markdown used for LLM scoring.

        Defaults to data/resume_scoring.md (a trimmed scoring-only version),
        falling back to data/resume.md if the scoring-specific file doesn't exist.
        """
        scoring = self.data_dir / "resume_scoring.md"
        if scoring.exists():
            return scoring
        return self.data_dir / "resume.md"


# Module-level singleton — import this anywhere
settings = Settings()
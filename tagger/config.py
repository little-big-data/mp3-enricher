from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    discogs_token: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    library_root: Path | None = None
    working_dir: Path = Path("./tagger_workdir")

    workers: int = 4
    discogs_fuzzy_threshold: int = 85
    id3_version: Literal["2.3", "2.4"] = "2.3"
    llm_provider: Literal["claude", "gemini"] = "claude"

    retry_not_found: bool = False

    @field_validator("workers", mode="after")
    @classmethod
    def workers_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"workers must be >= 1, got {v}")
        return v

    @field_validator("discogs_fuzzy_threshold", mode="after")
    @classmethod
    def threshold_must_be_in_range(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError(f"discogs_fuzzy_threshold must be 0-100, got {v}")
        return v

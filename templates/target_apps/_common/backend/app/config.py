"""Application settings from environment variables (never hard-coded secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "sqlite:///./data/app.db"


@dataclass(frozen=True)
class Settings:
    app_name: str
    database_url: str
    log_level: str


def load_settings() -> Settings:
    return Settings(
        app_name=os.environ.get("APP_NAME", "{{APP_TITLE}}"),
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )

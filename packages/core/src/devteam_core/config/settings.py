"""Typed platform settings (validated at startup; unknown keys are rejected)."""

import ipaddress
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProfileName = Literal["low_memory", "standard", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiSettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class PathSettings(_Section):
    data_dir: Path = Path("data")
    workspaces_dir: Path = Path("workspaces")


class DatabaseSettings(_Section):
    state_db: str = "state.db"
    checkpoints_db: str = "checkpoints.db"
    busy_timeout_ms: int = Field(default=5000, ge=0, le=600_000)
    auto_migrate: bool = True


class LoggingSettings(_Section):
    level: LogLevel = "INFO"
    console_format: Literal["json", "console"] = "json"
    file_enabled: bool = True
    file_name: str = "devteam.log"
    file_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    file_backup_count: int = Field(default=5, ge=0, le=100)

    @field_validator("level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class OllamaSettings(_Section):
    base_url: str = "http://127.0.0.1:11434"
    request_timeout_s: float = Field(default=600, gt=0, le=7200)

    @field_validator("base_url")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        # Spec §9.1 / roadmap P7: the default workflow never sends content to a non-local model endpoint.
        # The gateway (Part F) re-enforces this; rejecting it at config load fails fast.
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("ollama.base_url must be an http(s) URL")
        if not is_loopback_host(parts.hostname):
            raise ValueError("ollama.base_url must point to a loopback address (local-only default)")
        if parts.username or parts.password:
            raise ValueError("ollama.base_url must not embed credentials")
        return value.rstrip("/")


class ResourceSettings(_Section):
    max_parallel_deterministic: int = Field(default=1, ge=1, le=8)
    llm_context_tokens: int = Field(default=4096, ge=512, le=131_072)
    min_free_ram_mb: int = Field(default=3072, ge=0)


class Settings(_Section):
    profile: ProfileName = "low_memory"
    root: Path
    api: ApiSettings = ApiSettings()
    paths: PathSettings = PathSettings()
    database: DatabaseSettings = DatabaseSettings()
    logging: LoggingSettings = LoggingSettings()
    ollama: OllamaSettings = OllamaSettings()
    resources: ResourceSettings = ResourceSettings()

    # -- derived locations (absolute) ---------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return self._resolve(self.paths.data_dir)

    @property
    def workspaces_dir(self) -> Path:
        return self._resolve(self.paths.workspaces_dir)

    @property
    def state_db_path(self) -> Path:
        return self.data_dir / self.database.state_db

    @property
    def checkpoints_db_path(self) -> Path:
        return self.data_dir / self.database.checkpoints_db

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def _resolve(self, value: Path) -> Path:
        return value if value.is_absolute() else (self.root / value).resolve()

"""Typed platform settings (validated at startup; unknown keys are rejected)."""

import ipaddress
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProfileName = Literal["low_memory", "standard", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiSettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def _loopback_host(cls, value: str) -> str:
        # Spec §15 / D-29: the platform API is reachable from this machine only.
        if not is_loopback_host(value):
            raise ValueError("api.host must be a loopback address (127.0.0.1, ::1 or localhost)")
        return value


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


class FileToolSettings(_Section):
    """HC-06 Controlled File Service limits (approved F-9i / F-9j, AP-008)."""

    max_write_bytes: int = Field(default=1_048_576, ge=65_536, le=10_485_760)  # F-9i
    max_task_write_bytes: int = Field(default=10_485_760, ge=1_048_576, le=104_857_600)  # F-9j
    max_read_bytes: int = Field(default=1_048_576, ge=65_536, le=10_485_760)
    max_list_entries: int = Field(default=2_000, ge=10, le=20_000)


class UploadSettings(_Section):
    """HC-20 upload limits (Part B plan §2, approved AP-005)."""

    allowed_extensions: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md")  # D-22 (b)
    max_file_mb: int = Field(default=20, ge=1, le=500)
    max_files_per_upload: int = Field(default=10, ge=1, le=100)
    max_pdf_pages: int = Field(default=300, ge=1, le=10_000)
    max_docx_uncompressed_mb: int = Field(default=100, ge=1, le=2000)
    max_docx_entries: int = Field(default=2000, ge=10, le=100_000)
    max_docx_compression_ratio: int = Field(default=100, ge=2, le=10_000)
    max_extracted_chars: int = Field(default=2_000_000, ge=1000)
    chunk_target_chars: int = Field(default=1200, ge=200, le=20_000)
    chunk_max_chars: int = Field(default=2000, ge=200, le=40_000)

    @field_validator("allowed_extensions")
    @classmethod
    def _normalise_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        supported = {".pdf", ".docx", ".txt", ".md"}
        normalised = tuple(ext.lower() for ext in value)
        unknown = set(normalised) - supported
        if unknown:
            raise ValueError(f"no extractor exists for {sorted(unknown)}")
        return normalised

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


class AuthSettings(_Section):
    """HC-15 local API authentication (D-29 (a))."""

    token_file: str = "api_token"  # noqa: S105 - a file name, not a secret; under data_dir (gitignored)
    cookie_name: str = "devteam_session"
    allowed_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")

    @field_validator("allowed_origins")
    @classmethod
    def _loopback_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for origin in value:
            parts = urlsplit(origin)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError(f"invalid origin {origin!r}")
            if not is_loopback_host(parts.hostname) or parts.path not in {"", "/"}:
                raise ValueError(f"allowed origin must be a loopback origin: {origin!r}")
        return tuple(origin.rstrip("/") for origin in value)


class SecretsSettings(_Section):
    """HC-17 secret storage (D-18, AP-007): the OS keyring only, never a plaintext file.

    ``memory`` exists solely for the automated test suite and is rejected in every other profile.
    """

    backend: Literal["keyring", "memory"] = "keyring"
    service_name: str = Field(default="devteam-ai", pattern=r"^[a-z][a-z0-9-]{2,40}$")


class RateLimitSettings(_Section):
    """HC-18 API token-bucket limits (requests per minute)."""

    enabled: bool = True
    upload_per_minute: int = Field(default=10, ge=1)
    mutation_per_minute: int = Field(default=60, ge=1)
    session_per_minute: int = Field(default=10, ge=1)


class Settings(_Section):
    profile: ProfileName = "low_memory"
    root: Path
    api: ApiSettings = ApiSettings()
    paths: PathSettings = PathSettings()
    database: DatabaseSettings = DatabaseSettings()
    logging: LoggingSettings = LoggingSettings()
    ollama: OllamaSettings = OllamaSettings()
    resources: ResourceSettings = ResourceSettings()
    uploads: UploadSettings = UploadSettings()
    auth: AuthSettings = AuthSettings()
    secrets: SecretsSettings = SecretsSettings()
    files: FileToolSettings = FileToolSettings()
    ratelimit: RateLimitSettings = RateLimitSettings()

    @model_validator(mode="after")
    def _memory_secrets_only_in_tests(self) -> Self:
        if self.secrets.backend == "memory" and self.profile != "test":
            raise ValueError("secrets.backend 'memory' is allowed only in the test profile (D-18)")
        return self

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

    @property
    def api_token_path(self) -> Path:
        return self.data_dir / self.auth.token_file

    def _resolve(self, value: Path) -> Path:
        return value if value.is_absolute() else (self.root / value).resolve()

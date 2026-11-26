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
    """Where the local Ollama runs. Which model serves which slot lives in ``config/models.yaml`` (Part F);
    an ``ollama.models`` key is no longer accepted (unknown keys are rejected), so there is one source."""

    base_url: str = "http://127.0.0.1:11434"
    request_timeout_s: float = Field(default=600, gt=0, le=7200)
    keep_alive: str = Field(default="1m", pattern=r"^[0-9]{1,4}[smh]$")  # D-59: 0s on low_memory

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
    max_model_ram_mb: int = Field(default=4096, ge=256, le=131_072)  # MODEL_ROUTING rule 8


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


class OrchestrationSettings(_Section):
    """HC-07/HC-19 run orchestration and the run worker (Part D plan §3.6; D-40, D-41)."""

    recursion_limit: int = Field(default=200, ge=25, le=10_000)  # LangGraph backstop (LG-05)
    poll_interval_s: float = Field(default=1.0, gt=0, le=60)  # worker job polling
    heartbeat_interval_s: float = Field(default=5.0, gt=0, le=300)
    lease_ttl_s: float = Field(default=30.0, gt=0, le=3600)  # a lease older than this is dead
    max_crash_resumes: int = Field(default=3, ge=1, le=10)  # D-40: then the task is Blocked
    resource_wait_s: float = Field(default=60.0, ge=1, le=3600)  # WAITING_FOR_RESOURCES retry delay

    @model_validator(mode="after")
    def _heartbeat_inside_lease(self) -> Self:
        # At least two heartbeats must fit in one lease, or a live worker would look dead.
        if self.heartbeat_interval_s * 2 > self.lease_ttl_s:
            raise ValueError("orchestration.lease_ttl_s must be at least twice heartbeat_interval_s")
        return self


class SandboxLimits(_Section):
    """HC-14 container ceilings for one sandbox run (Part E plan §3.2). Recipes may only ask for less."""

    memory_mb: int = Field(default=1536, ge=256, le=8192)  # hard limit; swap is never added on top
    cpus: float = Field(default=1.5, gt=0, le=8)
    pids: int = Field(default=256, ge=32, le=4096)
    wall_clock_s: int = Field(default=600, ge=10, le=3600)  # watchdog kills, then the container is removed
    tmpfs_mb: int = Field(default=256, ge=16, le=2048)  # /tmp, noexec
    work_copy_mb: int = Field(default=512, ge=16, le=4096)  # most bytes copied into /work
    output_kb: int = Field(default=1024, ge=16, le=16_384)  # stdout / stderr kept per stream


_DISTRO = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class SandboxSettings(_Section):
    """HC-14 Docker sandbox (Part E plan §3.2, §3.10, §3.11; D-07 (b), D-45, D-46, D-50).

    The engine runs inside WSL2 and is reached only on loopback with mutual TLS. There is no setting
    for the container network, user, root filesystem or mounts: those are fixed in code.
    """

    enabled: bool = True
    docker_host: str = "tcp://127.0.0.1:2376"
    wsl_distribution: str = Field(default="Ubuntu-24.04", pattern=_DISTRO)  # doctor and scripts only
    min_free_ram_mb: int = Field(default=2048, ge=256, le=16_384)
    unload_models_before_run: bool = True
    lock_ttl_s: float = Field(default=120.0, ge=10, le=3600)  # D-46: a resource lease older than this is dead
    lock_wait_s: float = Field(
        default=900.0, ge=1, le=7200
    )  # wait this long for the lease, then wait for resources
    image_lock_path: Path = Path("sandbox/images/images.lock.json")
    limits: SandboxLimits = Field(default_factory=SandboxLimits)

    @field_validator("docker_host")
    @classmethod
    def _loopback_tls_endpoint(cls, value: str) -> str:
        # D-50: only the WSL engine's mutual-TLS listener on IPv4 loopback. Plain TCP to a remote host,
        # Unix sockets, named pipes (Docker Desktop) and SSH are refused at load time.
        parts = urlsplit(value)
        if parts.scheme != "tcp" or parts.hostname != "127.0.0.1" or parts.port is None:
            raise ValueError("sandbox.docker_host must be tcp://127.0.0.1:<port> (mutual TLS, D-50)")
        if parts.username or parts.password or parts.path not in {"", "/"} or parts.query:
            raise ValueError("sandbox.docker_host must be exactly tcp://127.0.0.1:<port>")
        return f"tcp://127.0.0.1:{parts.port}"


class LlmSettings(_Section):
    """LLM Gateway behaviour (Part F; HC-02/04/18). Models and routing: ``config/models.yaml``."""

    response_cache: bool = False  # D-28: available, off by default
    breaker_threshold: int = Field(default=3, ge=1, le=20)
    breaker_cooldown_s: float = Field(default=120, gt=0, le=3600)
    cancel_wait_s: float = Field(default=15, gt=0, le=120)  # wait for a cancelled call to end
    unload_verify_s: float = Field(default=10, gt=0, le=120)  # /api/ps check after an unload
    digest_refresh_s: float = Field(default=300, gt=0, le=86_400)


class RagSettings(_Section):
    """Retrieval over project knowledge (Part F, HC-08; plan §6.4-§6.5). ChromaDB in-process (D-54)."""

    top_k: int = Field(default=6, ge=1, le=12)
    max_distance: float = Field(default=0.8, gt=0, le=2)  # cosine distance cut-off
    batch_size: int = Field(default=16, ge=1, le=64)
    search_timeout_s: float = Field(default=30, gt=0, le=300)
    index_job_max_calls: int = Field(default=2000, ge=1, le=100_000)
    index_job_minutes: float = Field(default=30, gt=0, le=600)


class FeasibilitySettings(_Section):
    """Feasibility Gate (Part G; D-02, D-63): coverage threshold and the report folder in the workspace."""

    min_coverage_pct: int = Field(default=50, ge=1, le=100)
    report_dir: str = Field(default="reports", pattern=r"^[a-z][a-z0-9_]{0,30}$")


class ContextSettings(_Section):
    """Context Manager token budgets per role group (Part F plan §6.5): retrieval and memory caps."""

    discovery_retrieval_tokens: int = Field(default=1536, ge=0, le=8192)
    discovery_memory_tokens: int = Field(default=256, ge=0, le=8192)
    build_retrieval_tokens: int = Field(default=1024, ge=0, le=8192)
    build_memory_tokens: int = Field(default=512, ge=0, le=8192)
    documenter_retrieval_tokens: int = Field(default=768, ge=0, le=8192)
    documenter_memory_tokens: int = Field(default=512, ge=0, le=8192)


class Settings(_Section):
    profile: ProfileName = "low_memory"
    root: Path
    api: ApiSettings = ApiSettings()
    paths: PathSettings = PathSettings()
    database: DatabaseSettings = DatabaseSettings()
    logging: LoggingSettings = LoggingSettings()
    ollama: OllamaSettings = OllamaSettings()
    llm: LlmSettings = LlmSettings()
    rag: RagSettings = RagSettings()
    feasibility: FeasibilitySettings = FeasibilitySettings()
    context: ContextSettings = ContextSettings()
    resources: ResourceSettings = ResourceSettings()
    uploads: UploadSettings = UploadSettings()
    auth: AuthSettings = AuthSettings()
    secrets: SecretsSettings = SecretsSettings()
    files: FileToolSettings = FileToolSettings()
    ratelimit: RateLimitSettings = RateLimitSettings()
    orchestration: OrchestrationSettings = OrchestrationSettings()
    sandbox: SandboxSettings = SandboxSettings()

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

    @property
    def sandbox_tls_dir(self) -> Path:
        """Client certificate, encrypted client key and CA certificate for the engine (D-50)."""
        return self.data_dir / "sandbox_tls"

    @property
    def sandbox_image_lock_path(self) -> Path:
        return self._resolve(self.sandbox.image_lock_path)

    def _resolve(self, value: Path) -> Path:
        return value if value.is_absolute() else (self.root / value).resolve()

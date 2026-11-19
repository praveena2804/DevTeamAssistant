import shutil
from pathlib import Path

import pytest

from devteam_core.config import deep_merge, env_to_tree, is_loopback_host, load_settings
from devteam_core.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throw-away repository root containing a copy of the real configuration files."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return tmp_path


def test_real_repository_configuration_is_valid() -> None:
    for profile in ("low_memory", "standard", "test"):
        settings = load_settings(root=REPO_ROOT, profile=profile, environ={})
        assert settings.profile == profile


def test_defaults_profile_is_low_memory(repo: Path) -> None:
    settings = load_settings(root=repo, environ={})
    assert settings.profile == "low_memory"
    assert settings.resources.llm_context_tokens == 4096
    assert settings.resources.max_parallel_deterministic == 1
    assert settings.api.host == "127.0.0.1"


def test_profile_overrides_defaults(repo: Path) -> None:
    settings = load_settings(root=repo, profile="standard", environ={})
    assert settings.resources.llm_context_tokens == 8192
    assert settings.logging.level == "INFO"  # untouched default survives the merge


def test_precedence_defaults_profile_dotenv_environment(repo: Path) -> None:
    (repo / ".env").write_text(
        "DEVTEAM_LOGGING__LEVEL=warning\nDEVTEAM_API__PORT=8100\nDEVTEAM_RESOURCES__MIN_FREE_RAM_MB=1\n",
        encoding="utf-8",
    )
    settings = load_settings(
        root=repo,
        profile="test",  # profile sets level DEBUG
        environ={"DEVTEAM_API__PORT": "8200"},
    )
    assert settings.logging.level == "WARNING"  # .env beats profile
    assert settings.api.port == 8200  # environment beats .env
    assert settings.resources.min_free_ram_mb == 1  # .env beats defaults
    assert settings.database.busy_timeout_ms == 1000  # profile beats defaults


def test_profile_selected_from_environment_then_dotenv(repo: Path) -> None:
    (repo / ".env").write_text("DEVTEAM_PROFILE=standard\n", encoding="utf-8")
    assert load_settings(root=repo, environ={}).profile == "standard"
    assert load_settings(root=repo, environ={"DEVTEAM_PROFILE": "test"}).profile == "test"
    assert load_settings(root=repo, profile="low_memory", environ={"DEVTEAM_PROFILE": "test"}).profile == (
        "low_memory"
    )


def test_unknown_profile_is_rejected(repo: Path) -> None:
    with pytest.raises(ConfigError, match="unknown configuration profile"):
        load_settings(root=repo, profile="turbo", environ={})


def test_unknown_keys_are_rejected(repo: Path) -> None:
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_settings(root=repo, environ={"DEVTEAM_API__HOSTNAME": "x"})


def test_invalid_values_are_rejected(repo: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(root=repo, environ={"DEVTEAM_API__PORT": "70000"})


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:11434",
        "http://ollama.example.com:11434",
        "https://api.some-provider.example/v1",
        "http://0.0.0.0:11434",
        "ftp://127.0.0.1:11434",
        "http://user:pw@127.0.0.1:11434",
    ],
)
def test_non_local_or_unsafe_ollama_url_is_rejected(repo: Path, url: str) -> None:
    with pytest.raises(ConfigError):
        load_settings(root=repo, environ={"DEVTEAM_OLLAMA__BASE_URL": url})


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/", "http://localhost:11434", "http://[::1]:11434"])
def test_loopback_ollama_url_is_accepted(repo: Path, url: str) -> None:
    settings = load_settings(root=repo, environ={"DEVTEAM_OLLAMA__BASE_URL": url})
    assert not settings.ollama.base_url.endswith("/")


def test_relative_paths_resolve_under_root(repo: Path) -> None:
    settings = load_settings(root=repo, environ={})
    assert settings.data_dir == (repo / "data").resolve()
    assert settings.state_db_path == (repo / "data" / "state.db").resolve()
    assert settings.logs_dir == (repo / "data" / "logs").resolve()


def test_settings_are_immutable(repo: Path) -> None:
    settings = load_settings(root=repo, environ={})
    with pytest.raises(ValueError, match="frozen"):
        settings.api.port = 1  # type: ignore[misc]


def test_malformed_yaml_and_missing_files(repo: Path) -> None:
    (repo / "config" / "defaults.yaml").write_text("api: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_settings(root=repo, environ={})
    (repo / "config" / "defaults.yaml").unlink()
    with pytest.raises(ConfigError, match="not found"):
        load_settings(root=repo, environ={})


def test_env_to_tree_and_deep_merge() -> None:
    tree = env_to_tree({"DEVTEAM_A__B": "1", "DEVTEAM_A__C": "2", "OTHER": "x", "DEVTEAM_ROOT": "/r"})
    assert tree == {"a": {"b": "1", "c": "2"}}
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}}) == {"a": {"b": 9, "c": 2}}
    with pytest.raises(ConfigError):
        env_to_tree({"DEVTEAM_A____B": "1"})
    with pytest.raises(ConfigError):
        env_to_tree({"DEVTEAM_A": "1", "DEVTEAM_A__B": "2"})


def test_is_loopback_host() -> None:
    assert is_loopback_host("localhost")
    assert is_loopback_host("127.0.0.5")
    assert is_loopback_host("::1")
    assert not is_loopback_host("10.0.0.1")
    assert not is_loopback_host("example.com")

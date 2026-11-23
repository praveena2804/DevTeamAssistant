"""D.1 ``orchestration`` settings section (Part D plan §3.6; D-40, D-41)."""

import shutil
from pathlib import Path

import pytest

from devteam_core.config import load_settings
from devteam_core.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return tmp_path


def test_defaults_match_the_approved_plan(repo: Path) -> None:
    o = load_settings(root=repo, environ={}).orchestration
    assert (o.recursion_limit, o.poll_interval_s, o.heartbeat_interval_s, o.lease_ttl_s) == (200, 1, 5, 30)
    assert o.max_crash_resumes == 3  # D-40
    assert o.resource_wait_s == 60


def test_every_profile_loads_the_section() -> None:
    for profile in ("low_memory", "standard", "test"):
        assert load_settings(root=REPO_ROOT, profile=profile, environ={}).orchestration.recursion_limit > 0


def test_environment_override(repo: Path) -> None:
    settings = load_settings(root=repo, environ={"DEVTEAM_ORCHESTRATION__MAX_CRASH_RESUMES": "2"})
    assert settings.orchestration.max_crash_resumes == 2


@pytest.mark.parametrize(
    "environ",
    [
        {"DEVTEAM_ORCHESTRATION__UNKNOWN_KEY": "1"},
        {"DEVTEAM_ORCHESTRATION__RECURSION_LIMIT": "5"},
        {"DEVTEAM_ORCHESTRATION__MAX_CRASH_RESUMES": "0"},
        {"DEVTEAM_ORCHESTRATION__POLL_INTERVAL_S": "0"},
        # a lease shorter than two heartbeats would make a live worker look dead
        {"DEVTEAM_ORCHESTRATION__HEARTBEAT_INTERVAL_S": "20", "DEVTEAM_ORCHESTRATION__LEASE_TTL_S": "30"},
    ],
)
def test_invalid_values_are_rejected(repo: Path, environ: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        load_settings(root=repo, environ=environ)

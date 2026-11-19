"""Shared fixtures for cross-package suites."""

import shutil
from pathlib import Path

import pytest

from devteam_core.config import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Settings:
    """Settings for a throw-away repository root: the real config files, data under ``tmp_path``."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    return load_settings(root=tmp_path, profile="test", environ={})

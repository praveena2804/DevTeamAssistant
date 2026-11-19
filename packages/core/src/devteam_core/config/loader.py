"""Layered configuration loader.

Precedence (lowest → highest), roadmap §13 Part A:

1. ``config/defaults.yaml``
2. ``config/profiles/<profile>.yaml``
3. ``.env`` in the repository root (``DEVTEAM_*`` keys only)
4. process environment (``DEVTEAM_*`` keys only)

Nested keys use a double underscore: ``DEVTEAM_LOGGING__LEVEL=DEBUG`` → ``logging.level``.
The active profile is chosen by the explicit argument, else ``DEVTEAM_PROFILE`` (environment, then ``.env``),
else ``profile`` in ``defaults.yaml``.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import ValidationError

from devteam_core.config.settings import Settings
from devteam_core.errors import ConfigError
from devteam_core.paths import find_repo_root

ENV_PREFIX = "DEVTEAM_"
_NESTING = "__"
_IGNORED_ENV_KEYS = {"DEVTEAM_ROOT"}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"top level of {path} must be a mapping")
    return loaded


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def env_to_tree(env: Mapping[str, str | None]) -> dict[str, Any]:
    """Convert ``DEVTEAM_A__B=x`` entries into ``{"a": {"b": "x"}}``."""
    tree: dict[str, Any] = {}
    for raw_key, value in env.items():
        if value is None or not raw_key.startswith(ENV_PREFIX) or raw_key in _IGNORED_ENV_KEYS:
            continue
        parts = [part.lower() for part in raw_key[len(ENV_PREFIX) :].split(_NESTING)]
        if any(not part for part in parts):
            raise ConfigError(f"malformed configuration variable name: {raw_key}")
        node = tree
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ConfigError(f"configuration variable {raw_key} conflicts with a scalar value")
            node = child
        node[parts[-1]] = value
    return tree


def load_settings(
    *,
    root: Path | None = None,
    profile: str | None = None,
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> Settings:
    repo_root = (root or find_repo_root()).resolve()
    env = dict(os.environ if environ is None else environ)
    env_file = dotenv_path if dotenv_path is not None else repo_root / ".env"
    dotenv: dict[str, str | None] = dict(dotenv_values(env_file)) if env_file.is_file() else {}

    defaults = _read_yaml(repo_root / "config" / "defaults.yaml")
    chosen = profile or env.get("DEVTEAM_PROFILE") or dotenv.get("DEVTEAM_PROFILE") or defaults.get("profile")
    if not isinstance(chosen, str) or not chosen:
        raise ConfigError("no configuration profile selected")
    profile_file = repo_root / "config" / "profiles" / f"{chosen}.yaml"
    if not profile_file.is_file():
        raise ConfigError(f"unknown configuration profile {chosen!r} (no {profile_file})")

    tree = deep_merge(defaults, _read_yaml(profile_file))
    tree = deep_merge(tree, env_to_tree(dotenv))
    tree = deep_merge(tree, env_to_tree(env))
    tree["profile"] = chosen
    tree["root"] = repo_root

    try:
        return Settings.model_validate(tree)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc

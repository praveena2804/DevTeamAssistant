"""Repository-root discovery and platform directory helpers.

Path *safety* for agent file access (traversal, junctions, ADS, reserved names) is HC-06 and arrives in
Part C; this module only locates platform directories.
"""

import os
from pathlib import Path

from devteam_core.errors import ConfigError

ROOT_ENV_VAR = "DEVTEAM_ROOT"
_MARKERS = ("pyproject.toml", "config/defaults.yaml")


def _is_repo_root(candidate: Path) -> bool:
    return all((candidate / marker).is_file() for marker in _MARKERS)


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the repository root.

    ``DEVTEAM_ROOT`` wins when set; otherwise walk upwards from ``start`` (default: this file) looking for
    ``pyproject.toml`` together with ``config/defaults.yaml``.
    """
    override = os.environ.get(ROOT_ENV_VAR)
    if override:
        root = Path(override).resolve()
        if not _is_repo_root(root):
            raise ConfigError(f"{ROOT_ENV_VAR} does not point at a DevTeam AI repository root: {root}")
        return root
    origin = (start or Path(__file__)).resolve()
    for candidate in (origin, *origin.parents):
        if _is_repo_root(candidate):
            return candidate
    raise ConfigError(f"could not locate the repository root above {origin}")


def resolve_under(root: Path, value: Path) -> Path:
    """Resolve a configured path: absolute paths are kept, relative paths are anchored at ``root``."""
    return value if value.is_absolute() else (root / value).resolve()

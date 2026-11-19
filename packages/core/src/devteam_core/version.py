"""Platform version, read from the installed distribution metadata."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devteam-core")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
    __version__ = "0.0.0+unknown"

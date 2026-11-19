"""Centralised, validated platform configuration."""

from devteam_core.config.loader import deep_merge, env_to_tree, load_settings
from devteam_core.config.settings import (
    ApiSettings,
    DatabaseSettings,
    LoggingSettings,
    OllamaSettings,
    PathSettings,
    ResourceSettings,
    Settings,
    is_loopback_host,
)

__all__ = [
    "ApiSettings",
    "DatabaseSettings",
    "LoggingSettings",
    "OllamaSettings",
    "PathSettings",
    "ResourceSettings",
    "Settings",
    "deep_merge",
    "env_to_tree",
    "is_loopback_host",
    "load_settings",
]

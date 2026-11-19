"""Centralised, validated platform configuration."""

from devteam_core.config.loader import deep_merge, env_to_tree, load_settings
from devteam_core.config.settings import (
    ApiSettings,
    AuthSettings,
    DatabaseSettings,
    LoggingSettings,
    OllamaSettings,
    PathSettings,
    RateLimitSettings,
    ResourceSettings,
    Settings,
    UploadSettings,
    is_loopback_host,
)

__all__ = [
    "ApiSettings",
    "AuthSettings",
    "DatabaseSettings",
    "LoggingSettings",
    "OllamaSettings",
    "PathSettings",
    "RateLimitSettings",
    "ResourceSettings",
    "Settings",
    "UploadSettings",
    "deep_merge",
    "env_to_tree",
    "is_loopback_host",
    "load_settings",
]

"""HC-06 controlled tool execution layer (HS-10): registry, Controlled File Service, tool layer, tools."""

from devteam_harness.tools.builtin import build_default_registry
from devteam_harness.tools.files import ControlledFileService, FileLimitError, FileLimits, FileSafetyError
from devteam_harness.tools.layer import (
    ToolContext,
    ToolInvocation,
    ToolLayer,
    ToolNotFoundError,
    ToolUnavailableError,
)
from devteam_harness.tools.registry import ToolRegistry, ToolSpec, describe

__all__ = [
    "ControlledFileService",
    "FileLimitError",
    "FileLimits",
    "FileSafetyError",
    "ToolContext",
    "ToolInvocation",
    "ToolLayer",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolSpec",
    "ToolUnavailableError",
    "build_default_registry",
    "describe",
]

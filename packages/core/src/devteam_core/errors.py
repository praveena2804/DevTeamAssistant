"""Base error types.

The full error taxonomy (retryable / fatal / budget / policy) belongs to HC-12 and is defined in Part C.
Part A only provides the root type and configuration errors.
"""


class DevTeamError(Exception):
    """Root of every DevTeam AI platform error."""

    code: str = "devteam_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigError(DevTeamError):
    """Configuration is missing, malformed or violates a platform rule."""

    code = "config_error"

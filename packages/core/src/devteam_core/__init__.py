"""DevTeam AI shared foundation.

``devteam_core`` has no dependencies on other DevTeam AI packages (roadmap §7.1, enforced by import-linter).
"""

from devteam_core.version import __version__

__all__ = ["__version__"]

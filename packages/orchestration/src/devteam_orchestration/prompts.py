"""Trusted system prompts (HC-16): loaded from ``prompts/`` and verified against ``config/prompts.yaml``.

Part D wrote the ten version-1 prompts inline here; Part F moved them **verbatim** into files (D-58), so
their hashes are unchanged. They are platform text, not agent text: the executor puts them first as the
only ``system`` message, and untrusted material follows as labelled, delimited data.

Prompts are immutable per version: changing a text means a new version file, a new ``released`` hash in
``config/prompts.yaml``, and the agent's ``AgentSpec.prompt_version`` updated to the new active version.
"""

from __future__ import annotations

from pathlib import Path

from devteam_harness.prompts import InMemorySkillRegistry
from devteam_harness.prompts.files import FilePromptRegistry, build_file_registries


def build_prompt_registry(config_dir: Path) -> tuple[FilePromptRegistry, InMemorySkillRegistry]:
    """The verified prompt registry and the file-backed skills (start-up refuses any mismatch)."""
    return build_file_registries(config_dir)


__all__ = ["build_prompt_registry"]

"""File-backed prompt and skill registries (HC-16, HS-22; D-36, D-58; Part F plan §5 F.3).

**Prompts** live in ``prompts/<agent>/<prompt_id>.v<N>.md``: YAML front matter between ``---`` lines, then
the body. **Hash rule:** SHA-256 of the body's UTF-8 bytes exactly as stored after the closing ``---``
line (LF line endings, ``.gitattributes`` ``eol=lf``; no trailing newline is added or removed).

``config/prompts.yaml`` names the active version of each prompt and records the hash of **every released
version**. Start-up is refused when a released file is missing, its hash differs (a released version is
never edited: a change means a new version), a file is not listed, the front matter disagrees with the
file name, or an agent's ``AgentSpec.prompt_version`` is not the active version.

**Skills** (D-36) live in ``prompts/skills/<skill_id>.v<N>.md`` with front matter ``applies_to`` and
optional ``operations``. A skill is guidance text only and grants no permission.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from devteam_core.contracts.statuses import AgentRole
from devteam_core.errors import ConfigError
from devteam_harness.prompts import (
    InMemoryPromptRegistry,
    InMemorySkillRegistry,
    PromptTemplate,
    SkillSpec,
    sha256_text,
)

_FILE = re.compile(r"^(?P<id>[a-z][a-z0-9_.]{1,80})\.v(?P<version>[1-9][0-9]{0,3})\.md$")
_DELIMITER = "---\n"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromptEntry(_Strict):
    active: int = Field(ge=1)
    eval_required: bool = False
    released: dict[int, str]


class PromptConfig(_Strict):
    schema_version: Literal[1] = 1
    prompts: dict[str, PromptEntry]


class _PromptFront(_Strict):
    prompt_id: str
    version: int = Field(ge=1)
    agent: AgentRole
    origin: str | None = None


class _SkillFront(_Strict):
    skill_id: str
    version: int = Field(ge=1)
    applies_to: tuple[AgentRole, ...]
    operations: tuple[str, ...] = ()


def split_front_matter(raw: str, where: str) -> tuple[dict[str, object], str]:
    """``(front matter, body)``; the body is returned byte-for-byte (the hash rule depends on it)."""
    if "\r" in raw:
        raise ConfigError(f"{where}: prompt files use LF line endings only (hash rule)")
    if not raw.startswith(_DELIMITER):
        raise ConfigError(f"{where}: missing front matter")
    end = raw.find("\n" + _DELIMITER, len(_DELIMITER) - 1)
    if end < 0:
        raise ConfigError(f"{where}: unterminated front matter")
    front = yaml.safe_load(raw[len(_DELIMITER) : end + 1]) or {}
    if not isinstance(front, dict):
        raise ConfigError(f"{where}: front matter must be a mapping")
    return front, raw[end + 1 + len(_DELIMITER) :]


def load_prompt_config(config_dir: Path) -> PromptConfig:
    path = config_dir / "prompts.yaml"
    if not path.is_file():
        raise ConfigError(f"prompt configuration not found: {path}")
    try:
        return PromptConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise ConfigError(f"prompts.yaml is invalid: {exc.error_count()} problem(s)") from None


def code_root() -> Path:
    """The repository holding this code. Prompts are platform code assets, so they are found next to the
    code, not under ``DEVTEAM_ROOT`` (a data root may be a throw-away folder; see KI-040)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "prompts").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    raise ConfigError("the prompts/ folder was not found next to the platform code")


class FilePromptRegistry(InMemoryPromptRegistry):
    """Loads every prompt file once, verifies it against ``config/prompts.yaml``, then serves from memory
    (with the in-memory tamper check on every read)."""

    def __init__(self, prompts_dir: Path, config: PromptConfig) -> None:
        super().__init__()
        self.config = config
        self.prompts_dir = prompts_dir
        problems: list[str] = []
        seen: set[tuple[str, int]] = set()
        for path in sorted(prompts_dir.glob("*/*.md")):
            if path.parent.name == "skills":
                continue
            match = _FILE.fullmatch(path.name)
            where = path.relative_to(prompts_dir).as_posix()
            if match is None:
                problems.append(f"{where}: file name must be <prompt_id>.v<N>.md")
                continue
            prompt_id, version = match["id"], int(match["version"])
            front_raw, body = split_front_matter(path.read_bytes().decode("utf-8"), where)
            try:
                front = _PromptFront.model_validate(front_raw)
            except ValidationError:
                problems.append(f"{where}: front matter is invalid")
                continue
            if (front.prompt_id, front.version) != (
                prompt_id,
                version,
            ) or path.parent.name != front.agent.value:
                problems.append(f"{where}: front matter does not match the file name or folder")
                continue
            entry = config.prompts.get(prompt_id)
            recorded = entry.released.get(version) if entry else None
            if recorded is None:
                problems.append(f"{where}: not a released version in config/prompts.yaml")
                continue
            if sha256_text(body) != recorded:
                problems.append(f"{where}: text changed after release (hash mismatch); add a new version")
                continue
            self.register(PromptTemplate.create(prompt_id, version, body))
            seen.add((prompt_id, version))
        for prompt_id, entry in config.prompts.items():
            if entry.active not in entry.released:
                problems.append(f"{prompt_id}: active version {entry.active} is not released")
            problems.extend(
                f"{prompt_id} v{v}: released file is missing"
                for v in entry.released
                if (prompt_id, v) not in seen
            )
        if problems:
            raise ConfigError("prompt registry: " + "; ".join(problems[:10]))

    def active(self) -> dict[str, PromptTemplate]:
        return {pid: self.get(pid, e.active) for pid, e in self.config.prompts.items()}

    def check_agents(self, pinned: Mapping[str, int]) -> None:
        """Every agent's pinned prompt version must be the active one (no silent drift)."""
        problems = [
            f"{pid}: agent pins v{version}, active is v{self.config.prompts[pid].active}"
            if pid in self.config.prompts
            else f"{pid}: not in config/prompts.yaml"
            for pid, version in pinned.items()
            if pid not in self.config.prompts or self.config.prompts[pid].active != version
        ]
        if problems:
            raise ConfigError("prompt versions: " + "; ".join(problems))


def load_skills(skills_dir: Path) -> InMemorySkillRegistry:
    """File-backed skills (D-36). A missing folder means no skills."""
    if not skills_dir.is_dir():
        return InMemorySkillRegistry()
    skills: list[SkillSpec] = []
    for path in sorted(skills_dir.glob("*.md")):
        match = _FILE.fullmatch(path.name)
        where = f"skills/{path.name}"
        if match is None:
            raise ConfigError(f"{where}: file name must be <skill_id>.v<N>.md")
        front_raw, body = split_front_matter(path.read_bytes().decode("utf-8"), where)
        try:
            front = _SkillFront.model_validate(front_raw)
            skill = SkillSpec(
                skill_id=front.skill_id,
                version=front.version,
                applies_to=front.applies_to,
                operations=front.operations,
                text=body,
                sha256=sha256_text(body),
            )
        except ValidationError as exc:
            raise ConfigError(f"{where}: invalid skill ({exc.error_count()} problem(s))") from None
        if (skill.skill_id, skill.version) != (match["id"], int(match["version"])):
            raise ConfigError(f"{where}: front matter does not match the file name")
        skills.append(skill)
    return InMemorySkillRegistry(tuple(skills))


def build_file_registries(
    config_dir: Path, *, root: Path | None = None
) -> tuple[FilePromptRegistry, InMemorySkillRegistry]:
    base = (root or code_root()) / "prompts"
    return FilePromptRegistry(base, load_prompt_config(config_dir)), load_skills(base / "skills")


def eval_required(config: PromptConfig) -> Iterable[tuple[str, int, bool]]:
    """``(prompt_id, active version, eval_required)`` for the CI eval gate (D-56)."""
    return ((pid, e.active, e.eval_required) for pid, e in sorted(config.prompts.items()))


__all__ = [
    "FilePromptRegistry",
    "PromptConfig",
    "PromptEntry",
    "build_file_registries",
    "code_root",
    "eval_required",
    "load_prompt_config",
    "load_skills",
    "split_front_matter",
]

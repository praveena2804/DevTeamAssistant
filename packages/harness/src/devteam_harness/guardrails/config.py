"""Loading and validating ``config/guardrails/*.yaml`` (HC-01, HS-08).

Fail closed: a missing, malformed or over-permissive file stops the harness from loading at all.

Policy manipulation is bounded by **ceilings in code** taken from the specification (roadmap §4.1,
Spec §6). The YAML can narrow what an agent may do; it can never grant:

- a tool outside the Part C tool catalogue (no shell, ever);
- a write scope outside the agent's ceiling (e.g. the Head Agent or a discovery agent writing files);
- any access to the platform-protected areas, whose minimum list is built in;
- stages outside the agent's lifecycle (e.g. the BRD Analyst in the build stage).

The loaded configuration is immutable (frozen dataclasses and read-only mappings) and carries a SHA-256
digest of the four files, so every decision can be tied to the exact policy that made it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from devteam_core.contracts._base import check_relative_path, path_in_scope
from devteam_core.contracts.statuses import AgentRole, Stage
from devteam_core.errors import ConfigError

FILES = ("policies.yaml", "protected_paths.yaml", "input_guardrails.yaml", "output_guardrails.yaml")

# ------------------------------------------------------------------------------------------ ceilings

GENERATED = "generated/"
READ_TOOLS = frozenset({"read_project_file", "list_project_files"})
WRITE_TOOLS = frozenset({"write_project_file"})
NON_PATH_TOOLS = frozenset({"read_extracted_document", "search_project_knowledge"})
# Part E: execute tools run allowlisted sandbox recipes (no path, no command). Per-role ceilings follow
# docs/architecture/HARNESS_ASSESSMENT.md (B = build, T = tests, M = migrations).
EXECUTE_TOOLS = frozenset({"request_build", "request_test_run", "request_migration_check"})
KNOWN_TOOLS = READ_TOOLS | WRITE_TOOLS | NON_PATH_TOOLS | EXECUTE_TOOLS
EXECUTE_CEILINGS: Mapping[AgentRole, frozenset[str]] = MappingProxyType(
    {
        AgentRole.DATABASE_DEVELOPER: frozenset({"request_migration_check"}),
        AgentRole.BACKEND_DEVELOPER: frozenset({"request_build", "request_test_run"}),
        AgentRole.FRONTEND_DEVELOPER: frozenset({"request_build"}),
        AgentRole.TESTER: frozenset({"request_test_run"}),
        AgentRole.DEBUGGER_REVIEWER: frozenset({"request_build", "request_test_run"}),
    }
)

_BUILD = frozenset({Stage.BUILD, Stage.CHANGE_REQUEST})
STAGE_CEILINGS: Mapping[AgentRole, frozenset[Stage]] = MappingProxyType(
    {
        AgentRole.BRD_ANALYST: frozenset({Stage.ANALYSIS}),
        AgentRole.REQUIREMENTS_FINALIZER: frozenset({Stage.REQUIREMENTS}),
        AgentRole.HEAD: frozenset({Stage.BUILD, Stage.DELIVERY, Stage.CHANGE_REQUEST}),
        AgentRole.ARCHITECT: _BUILD,
        AgentRole.DATABASE_DEVELOPER: _BUILD,
        AgentRole.BACKEND_DEVELOPER: _BUILD,
        AgentRole.FRONTEND_DEVELOPER: _BUILD,
        AgentRole.TESTER: _BUILD,
        AgentRole.DEBUGGER_REVIEWER: _BUILD,
        AgentRole.DOCUMENTER: frozenset({Stage.DELIVERY, Stage.CHANGE_REQUEST}),
    }
)
WRITE_CEILINGS: Mapping[AgentRole, tuple[str, ...]] = MappingProxyType(
    {
        AgentRole.BRD_ANALYST: (),
        AgentRole.REQUIREMENTS_FINALIZER: (),
        AgentRole.HEAD: (),
        AgentRole.ARCHITECT: ("generated/docs/", "generated/contracts/"),
        AgentRole.DATABASE_DEVELOPER: ("generated/database/", "generated/backend/models/"),
        AgentRole.BACKEND_DEVELOPER: ("generated/backend/", "generated/config/"),
        AgentRole.FRONTEND_DEVELOPER: ("generated/frontend/",),
        AgentRole.TESTER: ("generated/tests/",),
        AgentRole.DEBUGGER_REVIEWER: (
            "generated/backend/",
            "generated/frontend/",
            "generated/database/",
            "generated/config/",
        ),
        AgentRole.DOCUMENTER: (
            "generated/docs/",
            "generated/README.md",
            "generated/PROJECT_DOCUMENTATION.md",
        ),
    }
)
MIN_NEVER_WRITABLE = frozenset({"uploads/", "extracted/", "runs/", "package/"})
MIN_NEVER_READABLE = frozenset({"uploads/"})
REQUIRED_CLAIM_IDS = frozenset({"tests_passed", "build_passed", "verified_claim", "approval_claim"})
MAX_PATTERN_CHARS = 400

# --------------------------------------------------------------------------------- YAML schemas


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _scope(value: str) -> str:
    try:
        return check_relative_path(value, allow_directory=True)
    except ValueError as exc:
        raise ValueError(f"invalid scope {value!r}: {exc}") from None


class _ToolGrant(_Strict):
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()

    @field_validator("read", "write")
    @classmethod
    def _scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_scope(v) for v in value)


class _RoleFile(_Strict):
    stages: tuple[Stage, ...] = Field(min_length=1)
    tools: dict[str, _ToolGrant] = Field(default_factory=dict)


class _PolicyFile(_Strict):
    version: Literal[1]
    roles: dict[AgentRole, _RoleFile]


class _ProtectedFile(_Strict):
    version: Literal[1]
    never_writable: tuple[str, ...]
    never_readable: tuple[str, ...]

    @field_validator("never_writable", "never_readable")
    @classmethod
    def _scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_scope(v) for v in value)


class _Pattern(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    pattern: str = Field(min_length=1, max_length=MAX_PATTERN_CHARS)


class _InputFile(_Strict):
    version: Literal[1]
    max_untrusted_chars: int = Field(ge=1_000, le=100_000)
    injection_patterns: tuple[_Pattern, ...] = Field(min_length=1)


class _OutputFile(_Strict):
    version: Literal[1]
    max_output_chars: int = Field(ge=1_000, le=200_000)
    forbidden_claims: tuple[_Pattern, ...] = Field(min_length=1)


# ----------------------------------------------------------------------------- runtime structures


@dataclass(frozen=True, slots=True)
class ToolGrant:
    read: tuple[str, ...]
    write: tuple[str, ...]

    @property
    def needs_path(self) -> bool:
        return bool(self.read or self.write)


@dataclass(frozen=True, slots=True)
class RolePolicy:
    stages: frozenset[Stage]
    tools: Mapping[str, ToolGrant]


@dataclass(frozen=True, slots=True)
class PolicyRules:
    roles: Mapping[AgentRole, RolePolicy]
    never_writable: tuple[str, ...]
    never_readable: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledPattern:
    id: str
    regex: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class InputRules:
    max_untrusted_chars: int
    injection_patterns: tuple[CompiledPattern, ...]


@dataclass(frozen=True, slots=True)
class OutputRules:
    max_output_chars: int
    forbidden_claims: tuple[CompiledPattern, ...]


@dataclass(frozen=True, slots=True)
class Guardrails:
    policy: PolicyRules
    input: InputRules
    output: OutputRules
    digest: str  # sha256 of the four configuration files


# ---------------------------------------------------------------------------------------- loading


def _read(directory: Path, name: str) -> tuple[bytes, Any]:
    path = directory / name
    if not path.is_file():
        raise ConfigError(f"guardrail configuration missing: config/guardrails/{name}")
    raw = path.read_bytes()
    try:
        return raw, yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config/guardrails/{name} is not valid YAML: {exc}") from None


def _compile(patterns: tuple[_Pattern, ...], name: str) -> tuple[CompiledPattern, ...]:
    ids = [p.id for p in patterns]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"config/guardrails/{name}: pattern ids must be unique")
    compiled = []
    for p in patterns:
        try:
            compiled.append(CompiledPattern(p.id, re.compile(p.pattern, re.MULTILINE)))
        except re.error as exc:
            raise ConfigError(
                f"config/guardrails/{name}: pattern '{p.id}' is not a valid regex: {exc}"
            ) from None
    return tuple(compiled)


def _within(scope: str, ceilings: tuple[str, ...]) -> bool:
    return any(path_in_scope(scope, ceiling) for ceiling in ceilings)


def _overlaps(scope: str, areas: tuple[str, ...]) -> bool:
    return any(path_in_scope(scope, area) or path_in_scope(area, scope) for area in areas)


def _check_policy(policy: _PolicyFile, protected: _ProtectedFile) -> None:  # noqa: PLR0912
    missing = set(AgentRole) - set(policy.roles)
    if missing:
        raise ConfigError(f"policies.yaml must list every agent; missing: {sorted(r.value for r in missing)}")
    if not {p.casefold() for p in protected.never_writable} >= MIN_NEVER_WRITABLE:
        raise ConfigError(f"protected_paths.yaml must keep never_writable ⊇ {sorted(MIN_NEVER_WRITABLE)}")
    if not {p.casefold() for p in protected.never_readable} >= MIN_NEVER_READABLE:
        raise ConfigError(f"protected_paths.yaml must keep never_readable ⊇ {sorted(MIN_NEVER_READABLE)}")
    for role, rules in policy.roles.items():
        where = f"policies.yaml roles.{role.value}"
        extra_stages = set(rules.stages) - STAGE_CEILINGS[role]
        if extra_stages:
            raise ConfigError(
                f"{where}: stages {sorted(s.value for s in extra_stages)} exceed the specification"
            )
        for tool, grant in rules.tools.items():
            if tool not in KNOWN_TOOLS:
                raise ConfigError(
                    f"{where}: unknown tool '{tool}' (tools are added in code, not configuration)"
                )
            if tool in EXECUTE_TOOLS and (
                grant.read or grant.write or tool not in EXECUTE_CEILINGS.get(role, ())
            ):
                raise ConfigError(
                    f"{where}.{tool}: execute tools take no path scopes and need the role's ceiling"
                )
            if tool in NON_PATH_TOOLS and (grant.read or grant.write):
                raise ConfigError(f"{where}.{tool}: this tool takes no path scopes")
            if tool in READ_TOOLS and (grant.write or not grant.read):
                raise ConfigError(f"{where}.{tool}: a read tool needs read scopes only")
            if tool in WRITE_TOOLS and (grant.read or not grant.write):
                raise ConfigError(f"{where}.{tool}: a write tool needs write scopes only")
            for scope in grant.read:
                if not path_in_scope(scope, GENERATED) or _overlaps(scope, protected.never_readable):
                    raise ConfigError(f"{where}.{tool}: read scope '{scope}' is not allowed")
            for scope in grant.write:
                if not _within(scope, WRITE_CEILINGS[role]):
                    raise ConfigError(f"{where}.{tool}: write scope '{scope}' exceeds the agent's ceiling")
                if _overlaps(scope, protected.never_writable):
                    raise ConfigError(f"{where}.{tool}: write scope '{scope}' overlaps a protected area")


def _check_claims(output: _OutputFile) -> None:
    missing = REQUIRED_CLAIM_IDS - {c.id for c in output.forbidden_claims}
    if missing:
        raise ConfigError(f"output_guardrails.yaml must keep the forbidden-claim checks {sorted(missing)}")


def load_guardrails(config_dir: Path) -> Guardrails:
    """Load ``<config_dir>/guardrails/*.yaml``; raises :class:`ConfigError` on any problem."""
    directory = config_dir / "guardrails"
    digest = hashlib.sha256()
    parsed: dict[str, Any] = {}
    for name in FILES:
        raw, data = _read(directory, name)
        digest.update(name.encode() + b"\0" + raw + b"\0")
        parsed[name] = data
    try:
        policy = _PolicyFile.model_validate(parsed["policies.yaml"])
        protected = _ProtectedFile.model_validate(parsed["protected_paths.yaml"])
        input_file = _InputFile.model_validate(parsed["input_guardrails.yaml"])
        output_file = _OutputFile.model_validate(parsed["output_guardrails.yaml"])
    except ValidationError as exc:
        raise ConfigError(f"invalid guardrail configuration: {exc}") from None
    _check_policy(policy, protected)
    _check_claims(output_file)

    roles = {
        role: RolePolicy(
            stages=frozenset(rules.stages),
            tools=MappingProxyType(
                {tool: ToolGrant(read=grant.read, write=grant.write) for tool, grant in rules.tools.items()}
            ),
        )
        for role, rules in policy.roles.items()
    }
    return Guardrails(
        policy=PolicyRules(
            roles=MappingProxyType(roles),
            never_writable=protected.never_writable,
            never_readable=protected.never_readable,
        ),
        input=InputRules(
            max_untrusted_chars=input_file.max_untrusted_chars,
            injection_patterns=_compile(input_file.injection_patterns, "input_guardrails.yaml"),
        ),
        output=OutputRules(
            max_output_chars=output_file.max_output_chars,
            forbidden_claims=_compile(output_file.forbidden_claims, "output_guardrails.yaml"),
        ),
        digest=digest.hexdigest(),
    )

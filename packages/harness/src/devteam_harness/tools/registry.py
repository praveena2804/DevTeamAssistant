"""``ToolSpec`` and ``ToolRegistry`` (HC-06, Spec §11.2).

Every tool declares: unique name and version; typed input and output schemas; allowed agent roles;
the workspace path argument it touches (paths are granted by the central policy); a time limit; its
side-effect class; whether it needs a human approval; and its audit behaviour.

Tools are added in code, never in configuration. The registry refuses to start if the central policy
grants a tool to a role the tool itself does not allow (both must agree; the effective permission is
their intersection).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tools import SideEffect
from devteam_core.errors import ConfigError
from devteam_harness.guardrails.config import Guardrails
from devteam_harness.guardrails.policy import Access

if TYPE_CHECKING:
    from devteam_harness.tools.layer import ToolInvocation


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: int
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    side_effect: SideEffect
    access: Access
    path_arg: str | None
    allowed_roles: frozenset[AgentRole]
    timeout_s: float
    handler: Callable[[ToolInvocation], BaseModel] = field(repr=False)
    requires_approval: bool = False
    audit: Literal["full", "summary"] = "full"
    summary_exclude: frozenset[str] = frozenset()  # argument names summarised by size only (e.g. content)

    def __post_init__(self) -> None:
        if self.side_effect is SideEffect.EXTERNAL:
            raise ValueError("external tools arrive with publishing (P)")
        if self.side_effect is SideEffect.EXECUTE and (self.access is not Access.NONE or self.path_arg):
            # Part E: execute tools run allowlisted sandbox recipes on the task's generated tree; they never
            # take a path or a command from the caller.
            raise ValueError("execute tools take no path argument and no file access")
        if self.timeout_s <= 0:
            raise ValueError("a tool needs a positive time limit")
        if (self.access is Access.NONE) != (self.path_arg is None):
            raise ValueError("path tools declare read/write access and their path argument")


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[tuple[str, int], ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        key = (spec.name, spec.version)
        if key in self._specs:
            raise ValueError(f"tool {spec.name} v{spec.version} is already registered")
        self._specs[key] = spec

    def get(self, name: str, version: int) -> ToolSpec | None:
        return self._specs.get((name, version))

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def check_consistency(self, guardrails: Guardrails) -> None:
        names = {spec.name: spec for spec in self._specs.values()}
        for role, policy in guardrails.policy.roles.items():
            for tool, grant in policy.tools.items():
                spec = names.get(tool)
                if spec is None:
                    raise ConfigError(f"policy grants unknown tool '{tool}' to {role.value}")
                if role not in spec.allowed_roles:
                    raise ConfigError(
                        f"policy grants '{tool}' to {role.value}, which the tool does not allow"
                    )
                wants_write = bool(grant.write)
                if wants_write != (spec.access is Access.WRITE):
                    raise ConfigError(f"policy access for '{tool}' does not match the tool's declared access")


def describe(spec: ToolSpec) -> dict[str, Any]:
    """Human/agent-readable tool description (prompt registry, C.4/F)."""
    return {
        "name": spec.name,
        "version": spec.version,
        "input_schema": spec.input_model.model_json_schema(),
        "output_schema": spec.output_model.model_json_schema(),
        "side_effect": spec.side_effect.value,
        "requires_approval": spec.requires_approval,
    }

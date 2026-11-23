"""``AgentRegistry`` (HS-16; ten-agent enforcement layer L2, roadmap §4.4).

Accepts only ``AgentSpec`` objects for ``AgentRole`` members, rejects duplicates, and refuses to seal
(and therefore to be used) unless exactly the ten canonical agents are registered. When the central
policy is given, every declared tool must be granted to that role in the policy, and every declared write
path must be writable under the policy, so a spec cannot silently disagree with the policy.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from devteam_core.contracts._base import path_in_scope
from devteam_core.contracts.statuses import EXPECTED_AGENT_COUNT, AgentRole
from devteam_core.errors import ConfigError
from devteam_harness.agent_sdk.spec import AgentSpec
from devteam_harness.guardrails.config import Guardrails


class AgentRegistryError(ConfigError):
    code = "agent_registry"


class AgentRegistry:
    def __init__(self) -> None:
        self._specs: dict[AgentRole, AgentSpec] = {}
        self._sealed = False

    def register(self, spec: AgentSpec) -> None:
        if self._sealed:
            raise AgentRegistryError("the agent registry is sealed")
        if not isinstance(spec, AgentSpec):
            raise AgentRegistryError("only AgentSpec declarations can be registered")
        if not isinstance(spec.role, AgentRole) or spec.role.value not in {r.value for r in AgentRole}:
            raise AgentRegistryError("agents must be AgentRole members (ADR-0002)")
        if spec.role in self._specs:
            raise AgentRegistryError(f"{spec.role.value} is already registered")
        self._specs[spec.role] = spec

    def seal(self, guardrails: Guardrails | None = None) -> AgentRegistry:
        if len(self._specs) != EXPECTED_AGENT_COUNT or set(self._specs) != set(AgentRole):
            missing = sorted(r.value for r in set(AgentRole) - set(self._specs))
            raise AgentRegistryError(
                f"exactly {EXPECTED_AGENT_COUNT} agents must be registered; have {len(self._specs)}, "
                f"missing {missing}"
            )
        if guardrails is not None:
            problems = list(_policy_problems(self._specs.values(), guardrails))
            if problems:
                raise AgentRegistryError(
                    "agent specs disagree with the central policy: " + "; ".join(problems)
                )
        self._sealed = True
        return self

    @property
    def sealed(self) -> bool:
        return self._sealed

    def get(self, role: AgentRole) -> AgentSpec:
        if not self._sealed:
            raise AgentRegistryError("the agent registry must be sealed before use")
        return self._specs[role]

    def __len__(self) -> int:
        return len(self._specs)

    def __iter__(self) -> Iterator[AgentSpec]:
        return iter(self._specs.values())


def _policy_problems(specs: Iterable[AgentSpec], guardrails: Guardrails) -> Iterator[str]:
    for spec in specs:
        role_policy = guardrails.policy.roles.get(spec.role)
        granted = role_policy.tools if role_policy is not None else {}
        for tool in spec.tools:
            if tool not in granted:
                yield f"{spec.role.value} declares {tool}, which the policy does not grant"
        writable = [scope for grant in granted.values() for scope in grant.write]
        for path in spec.write_paths:
            if not any(path_in_scope(path, scope) for scope in writable):
                yield f"{spec.role.value} declares write path {path}, which the policy does not allow"


__all__ = ["AgentRegistry", "AgentRegistryError"]

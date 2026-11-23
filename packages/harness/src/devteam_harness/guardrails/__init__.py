"""HC-01 guardrails and deterministic policy enforcement (HS-08).

- :mod:`.config`: loads ``config/guardrails/*.yaml`` with code-level ceilings (fail closed).
- :mod:`.policy`: deny-by-default role x tool x path x stage engine + audited enforcer.
- :mod:`.input`: untrusted-content delimiting, size limits, secret refusal, injection flags.
- :mod:`.output`: schema, cross-checks, forbidden claims, secret detection on agent outputs.
"""

from devteam_harness.guardrails.config import Guardrails, load_guardrails
from devteam_harness.guardrails.input import InputGuard, InputRejectedError, InputScreening
from devteam_harness.guardrails.output import OutputGuard, OutputRejectedError
from devteam_harness.guardrails.policy import (
    Access,
    AuditSink,
    PolicyDecision,
    PolicyEnforcer,
    PolicyEngine,
    PolicyRequest,
)
from devteam_harness.guardrails.secret_scan import secret_findings

__all__ = [
    "Access",
    "AuditSink",
    "Guardrails",
    "InputGuard",
    "InputRejectedError",
    "InputScreening",
    "OutputGuard",
    "OutputRejectedError",
    "PolicyDecision",
    "PolicyEnforcer",
    "PolicyEngine",
    "PolicyRequest",
    "load_guardrails",
    "secret_findings",
]

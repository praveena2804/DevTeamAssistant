"""HC-15 identity: human, agent and service principals; roles (Spec §4); the per-install API token."""

from devteam_harness.iam.principals import (
    AgentPrincipal,
    AnyPrincipal,
    ServiceName,
    ServicePrincipal,
    has_approval_authority,
    issue_agent_principal,
)
from devteam_harness.iam.roles import LOCAL_HUMAN_ROLES, Principal, Role
from devteam_harness.iam.token import (
    API_TOKEN_REF,
    MIN_TOKEN_LENGTH,
    TokenMigrationRequiredError,
    generate_token,
    load_api_token,
    session_value,
    token_matches,
)

__all__ = [
    "API_TOKEN_REF",
    "LOCAL_HUMAN_ROLES",
    "MIN_TOKEN_LENGTH",
    "AgentPrincipal",
    "AnyPrincipal",
    "Principal",
    "Role",
    "ServiceName",
    "ServicePrincipal",
    "TokenMigrationRequiredError",
    "generate_token",
    "has_approval_authority",
    "issue_agent_principal",
    "load_api_token",
    "session_value",
    "token_matches",
]

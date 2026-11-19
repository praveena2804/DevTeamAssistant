"""Human roles from Spec §4.

The MVP is single-user (D-11 (a)): the one local human holds every human role, but every route still
declares the role it needs, so multi-user support can be added without rewriting routes.
"""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    PROJECT_USER = "project_user"
    PROJECT_ADMIN = "project_admin"
    REVIEWER = "reviewer"


LOCAL_HUMAN_ROLES: frozenset[str] = frozenset(role.value for role in Role)


@dataclass(frozen=True)
class Principal:
    id: str
    kind: str
    display_name: str
    roles: frozenset[str]

    def has_role(self, role: Role) -> bool:
        return role.value in self.roles

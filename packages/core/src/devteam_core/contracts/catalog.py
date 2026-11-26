"""Capability Catalog contracts (Spec §13.1, §3.4; roadmap §13 Part G; PART-G-PLAN §6.1).

The catalog says what the platform can build. It is configuration **and** code:

- ``CatalogKey`` is a closed enum. ``capabilities.yaml`` must list exactly these keys, so neither a YAML
  edit alone nor a model answer can add a capability (HC-01 closed-enum guardrail);
- the *declared* status in the file is never trusted on its own. The Capability Catalog service computes
  the *effective* status from the starter kit, the registered recipes and a passing, current template
  verification (honest demotion, AC-G-02).

Every Spec §13.1 field is present. ``generation_adapter`` is recorded here; the generators themselves
arrive with the developer agents (Parts K-L).
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from devteam_core.contracts._base import Contract, Name, RelPath, ShortText

# Kit-provided versions, written as ``major`` or ``major.minor`` (a subset of PEP 440 and npm range syntax).
VersionPrefix = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,4}(\.[0-9]{1,4}){0,2}$")]
# Exact, normalised phrases used for deterministic mapping: lower case, single spaces.
Alias = Annotated[str, StringConstraints(pattern=r"^[a-z0-9.][a-z0-9 .+#/-]{0,58}[a-z0-9+#]$|^[a-z0-9]$")]
RecipeRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,39}$")]
TemplateRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
PartLetter = Annotated[str, StringConstraints(pattern=r"^[H-R]$")]


class CatalogKey(StrEnum):
    """Every capability the platform knows. Adding one is a reviewed code change (enum + YAML + tests)."""

    # frontend
    HTML_CSS_JS = "html_css_js"
    REACT_VITE = "react_vite"
    ANGULAR = "angular"
    VUE = "vue"
    POWER_APPS_CANVAS = "power_apps_canvas"
    # backend
    FASTAPI_PYTHON = "fastapi_python"
    DOTNET = "dotnet"
    JAVA_SPRING = "java_spring"
    POWER_AUTOMATE = "power_automate"
    # database
    SQLITE = "sqlite"
    POSTGRESQL_DOCKER_LOCAL = "postgresql_docker_local"
    USER_PROVIDED_POSTGRESQL = "user_provided_postgresql"
    SUPABASE_HOSTED = "supabase_hosted"
    SQL_SERVER = "sql_server"
    ORACLE = "oracle"
    AZURE_SQL = "azure_sql"
    # deployment
    GITHUB_PUBLIC_REPO = "github_public_repo"
    GITHUB_ACTIONS_CI = "github_actions_ci"
    GITHUB_PAGES = "github_pages"
    POWER_APPS = "power_apps"
    AZURE = "azure"
    AWS = "aws"
    SHAREPOINT = "sharepoint"
    PAID_HOSTING = "paid_hosting"
    # integration
    PUBLIC_REST_NO_CREDENTIALS = "public_rest_no_credentials"
    SSO = "sso"
    SAP = "sap"
    PAYMENT_GATEWAY = "payment_gateway"
    CREDENTIALED_API = "credentialed_api"
    # authentication
    NO_AUTH = "no_auth"
    ENTERPRISE_IDENTITY_PROVIDER = "enterprise_identity_provider"
    LOCAL_PASSWORD_RBAC = "local_password_rbac"  # noqa: S105 - a capability name, not a password


class CapabilityCategory(StrEnum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"
    DEPLOYMENT = "deployment"
    INTEGRATION = "integration"
    AUTH = "auth"


class CatalogStatus(StrEnum):
    """Declared status (Spec §13.1/§13.2). Never used on its own: see ``EffectiveStatus``."""

    SUPPORTED = "supported"
    SUPPORTED_FOR_LOCAL_TESTING = "supported_for_local_testing"
    SUPPORTED_FOR_STATIC_FRONTEND = "supported_for_static_frontend"
    LATER_CHANGE_REQUEST = "later_change_request"
    UNSUPPORTED = "unsupported"


DECLARED_SUPPORTED = frozenset(
    {
        CatalogStatus.SUPPORTED,
        CatalogStatus.SUPPORTED_FOR_LOCAL_TESTING,
        CatalogStatus.SUPPORTED_FOR_STATIC_FRONTEND,
    }
)


class EffectiveStatus(StrEnum):
    """What the platform can honestly offer now (computed, never declared)."""

    AVAILABLE = "available"
    AVAILABLE_LIMITED = "available_limited"
    DEMOTED = "demoted"
    UNSUPPORTED = "unsupported"
    LATER_CHANGE_REQUEST = "later_change_request"


OFFERABLE = frozenset({EffectiveStatus.AVAILABLE, EffectiveStatus.AVAILABLE_LIMITED})


class GenerationAdapter(StrEnum):
    """Closed list of generation methods. Recorded now; the generators arrive in Parts K-L."""

    HTML_FRONTEND = "html_frontend"
    REACT_FRONTEND = "react_frontend"
    FASTAPI_BACKEND = "fastapi_backend"
    SQLALCHEMY_SQLITE = "sqlalchemy_sqlite"
    NONE = "none"


class NetworkRequirement(StrEnum):
    NONE = "none"
    PUBLIC_NO_CREDENTIALS = "public_no_credentials"
    CREDENTIALED = "credentialed"


class CapabilityEntry(Contract):
    """One catalog entry: every Spec §13.1 field."""

    key: CatalogKey
    display_name: Name
    category: CapabilityCategory
    status: CatalogStatus
    compatible_versions: tuple[VersionPrefix, ...] = Field(default=(), max_length=20)
    templates: tuple[TemplateRef, ...] = Field(default=(), max_length=10)
    generation_adapter: GenerationAdapter = GenerationAdapter.NONE
    build_recipe: RecipeRef | None = None
    test_recipes: tuple[RecipeRef, ...] = Field(default=(), max_length=20)
    runtime_requirements: tuple[ShortText, ...] = Field(default=(), max_length=20)
    required_credentials: tuple[Name, ...] = Field(default=(), max_length=20)
    network_requirement: NetworkRequirement = NetworkRequirement.NONE
    known_limitations: tuple[ShortText, ...] = Field(default=(), max_length=20)
    substitutions: tuple[CatalogKey, ...] = Field(default=(), max_length=10)
    aliases: tuple[Alias, ...] = Field(default=(), max_length=50)
    documentation_ref: RelPath
    available_from_part: PartLetter | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        supported = self.status in DECLARED_SUPPORTED
        credentialed = self.network_requirement is NetworkRequirement.CREDENTIALED
        if supported and (self.required_credentials or credentialed):
            raise ValueError(
                f"{self.key.value}: an entry that needs credentials cannot be supported (NFR-013, Spec §3.4)"
            )
        if supported and not self.templates and self.available_from_part is None:
            raise ValueError(
                f"{self.key.value}: a supported entry needs a starter kit, or the part that delivers it"
            )
        if supported and self.templates and self.build_recipe is None:
            raise ValueError(f"{self.key.value}: a supported entry with a starter kit needs its build recipe")
        if not supported and (self.templates or self.build_recipe or self.test_recipes):
            raise ValueError(f"{self.key.value}: only supported entries reference starter kits or recipes")
        if self.key in self.substitutions:
            raise ValueError(f"{self.key.value}: an entry cannot substitute itself")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError(f"{self.key.value}: duplicate alias")
        if len(set(self.substitutions)) != len(self.substitutions):
            raise ValueError(f"{self.key.value}: duplicate substitution")
        return self


class CapabilityCatalog(Contract):
    """``capability_catalog/capabilities.yaml``."""

    version: Literal[1]
    # Spec §13.2 "SQL" rule: wording that names no engine. Never mapped to an enterprise product.
    ambiguous_database_aliases: tuple[Alias, ...] = Field(min_length=1, max_length=50)
    entries: tuple[CapabilityEntry, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _closed_and_consistent(self) -> Self:
        keys = [e.key for e in self.entries]
        duplicates = sorted(k.value for k, n in Counter(keys).items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate catalog keys: {duplicates}")
        missing = sorted(k.value for k in set(CatalogKey) - set(keys))
        if missing:
            raise ValueError(f"catalog keys without an entry: {missing}")
        by_key = {e.key: e for e in self.entries}
        seen: dict[tuple[CapabilityCategory, str], CatalogKey] = {}
        for entry in self.entries:
            for alias in entry.aliases:
                other = seen.setdefault((entry.category, alias), entry.key)
                if other is not entry.key:
                    raise ValueError(f"alias '{alias}' is used by {other.value} and {entry.key.value}")
            for sub in entry.substitutions:
                if by_key[sub].category is not entry.category:
                    raise ValueError(f"{entry.key.value}: substitution {sub.value} is in another category")
        databases = [e for e in self.entries if e.category is CapabilityCategory.DATABASE]
        database_aliases = {a for e in databases for a in e.aliases}
        clash = sorted(database_aliases & set(self.ambiguous_database_aliases))
        if clash:
            raise ValueError(f"ambiguous database wording also maps to an engine: {clash}")
        return self

    def entry(self, key: CatalogKey) -> CapabilityEntry:
        return next(e for e in self.entries if e.key is key)


CATALOG_MODELS: tuple[type[Contract], ...] = (CapabilityEntry, CapabilityCatalog)

__all__ = [
    "CATALOG_MODELS",
    "DECLARED_SUPPORTED",
    "OFFERABLE",
    "CapabilityCatalog",
    "CapabilityCategory",
    "CapabilityEntry",
    "CatalogKey",
    "CatalogStatus",
    "EffectiveStatus",
    "GenerationAdapter",
    "NetworkRequirement",
]

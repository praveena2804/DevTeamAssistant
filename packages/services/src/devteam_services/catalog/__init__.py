"""Capability Catalog service (Spec §13; roadmap §13 Part G G.1; PART-G-PLAN §6.1-§6.2, §7).

Deterministic: no model, no network, no sandbox (import contract). Two steps:

1. :func:`load_catalog` reads ``capability_catalog/capabilities.yaml`` and **fails closed**
   (:class:`CatalogError`) on any problem: YAML or schema error, a key missing from or not in the closed
   ``CatalogKey`` enum, a duplicate alias, an unknown starter kit or recipe, a missing documentation file,
   or a checked-in JSON Schema that no longer matches the contract.
2. :func:`effective_catalog` computes what can honestly be offered **now**. A declared-supported entry is
   ``available`` only if at least one of its starter kits is present with a valid manifest, every recipe it
   names is registered, and the kit's newest finished verification **passed** and is **current** (same kit
   checksum and image IDs). Otherwise it is ``demoted`` with the reason (AC-G-02). Entries whose assets
   arrive in a later part are demoted with "arrives in Part X" (D-62).

The catalog file is a code asset: it is found next to the platform code (like ``prompts/``), never under
a throw-away data root (lesson of KI-040). Starter kits are looked up where the runtime finds them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.catalog import (
    DECLARED_SUPPORTED,
    CapabilityCatalog,
    CapabilityEntry,
    CatalogKey,
    CatalogStatus,
    EffectiveStatus,
    NetworkRequirement,
)
from devteam_core.contracts.sandbox import TemplateVerification, TemplateVerificationStatus
from devteam_core.errors import ConfigError
from devteam_core.logging import get_logger
from devteam_harness.iam.principals import ServiceName, ServicePrincipal
from devteam_harness.sandbox.recipes import RECIPES
from devteam_services.integration_builder import load_manifest, template_ids
from devteam_state.repositories import audit
from devteam_state.repositories import sandbox as sandbox_repo

CATALOG_DIR = "capability_catalog"
CATALOG_FILE = "capabilities.yaml"
SCHEMA_FILE = "schemas/capabilities.schema.json"
PRINCIPAL = ServicePrincipal(ServiceName.FEASIBILITY_GATE).id
DEMOTED_ACTION = "catalog.entry_demoted"
RESTORED_ACTION = "catalog.entry_restored"
_log = get_logger("devteam_services.catalog")

Fingerprint = Callable[[str], tuple[str, dict[str, str]]]


class CatalogError(ConfigError):
    code = "catalog_invalid"


def code_root() -> Path:
    """The repository holding this code (the catalog is a reviewed code asset, found next to the code)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / CATALOG_DIR).is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    raise CatalogError(f"the {CATALOG_DIR}/ folder was not found next to the platform code")


def render_catalog_schema() -> str:
    """The JSON Schema of ``capabilities.yaml`` exactly as it is checked in (generated from the contract)."""
    schema = CapabilityCatalog.model_json_schema(mode="validation")
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass(frozen=True)
class LoadedCatalog:
    catalog: CapabilityCatalog
    digest: str  # sha256 of the file bytes: recorded on every feasibility report

    def entry(self, key: CatalogKey) -> CapabilityEntry:
        return self.catalog.entry(key)


def parse_catalog(raw: bytes) -> CapabilityCatalog:
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise CatalogError(f"{CATALOG_FILE} is not valid YAML: {type(exc).__name__}") from None
    try:
        return CapabilityCatalog.model_validate(data)
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        location = ".".join(str(p) for p in first["loc"]) or "<root>"
        raise CatalogError(f"{CATALOG_FILE} is invalid at {location}: {first['msg']}") from None


def load_catalog(root: Path | None = None) -> LoadedCatalog:
    """Load and validate the catalog. Every problem raises :class:`CatalogError` (fail closed)."""
    base = root or code_root()
    folder = base / CATALOG_DIR
    try:
        raw = (folder / CATALOG_FILE).read_bytes()
    except OSError:
        raise CatalogError(f"{CATALOG_DIR}/{CATALOG_FILE} is missing: feasibility is unchecked") from None
    catalog = parse_catalog(raw)
    try:
        checked_in = (folder / SCHEMA_FILE).read_text(encoding="utf-8")
    except OSError:
        raise CatalogError(f"{CATALOG_DIR}/{SCHEMA_FILE} is missing") from None
    if checked_in != render_catalog_schema():
        raise CatalogError(f"{CATALOG_DIR}/{SCHEMA_FILE} does not match the catalog contract (regenerate it)")
    problems = reference_problems(catalog, base)
    if problems:
        more = f" (+{len(problems) - 1} more)" if problems[1:] else ""
        raise CatalogError(f"{CATALOG_FILE}: {problems[0]}{more}")
    return LoadedCatalog(catalog=catalog, digest=hashlib.sha256(raw).hexdigest())


def reference_problems(catalog: CapabilityCatalog, root: Path) -> list[str]:
    """Static references: starter kits shipped with the code, registered recipes, documentation files."""
    kits = set(template_ids(root / "templates" / "target_apps"))
    problems: list[str] = []
    for entry in catalog.entries:
        missing = [t for t in entry.templates if t not in kits]
        problems += [f"{entry.key.value}: unknown starter kit {t!r}" for t in missing]
        recipes = [*([entry.build_recipe] if entry.build_recipe else []), *entry.test_recipes]
        problems += [f"{entry.key.value}: unknown recipe {r!r}" for r in recipes if r not in RECIPES]
        if not (root / entry.documentation_ref).is_file():
            problems.append(f"{entry.key.value}: documentation {entry.documentation_ref!r} does not exist")
    return problems


# --------------------------------------------------------------------------------------- effective status


@dataclass(frozen=True)
class EffectiveEntry:
    entry: CapabilityEntry
    status: EffectiveStatus
    reason: str | None
    verification_ids: tuple[str, ...] = ()  # the passing, current verifications that make it available

    @property
    def key(self) -> CatalogKey:
        return self.entry.key


@dataclass(frozen=True)
class CatalogSnapshot:
    catalog: CapabilityCatalog
    catalog_hash: str
    entries: Mapping[CatalogKey, EffectiveEntry] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def verification_ids(self) -> tuple[str, ...]:
        return tuple(sorted({v for e in self.entries.values() for v in e.verification_ids}))

    def demoted(self) -> list[EffectiveEntry]:
        return [e for e in self.entries.values() if e.status is EffectiveStatus.DEMOTED]


def _kit_problem(  # noqa: PLR0911 - one return per demotion reason
    template_id: str,
    kits_root: Path,
    verification: TemplateVerification | None,
    fingerprint: Fingerprint,
) -> str | None:
    if template_id not in template_ids(kits_root):
        return f"starter kit {template_id} is not installed"
    try:
        manifest = load_manifest(template_id, kits_root)
    except (ValueError, OSError, yaml.YAMLError):  # TemplateError, ValidationError, bad encoding
        return f"starter kit {template_id} has an invalid manifest"
    unknown = [r for r in manifest.self_test if r not in RECIPES]
    if unknown:
        return f"starter kit {template_id} uses unregistered recipes: {', '.join(unknown)}"
    if verification is None:
        return f"starter kit {template_id} has never been verified"
    if verification.status is TemplateVerificationStatus.STALE:
        return f"the verification of starter kit {template_id} is stale (the kit or an image changed)"
    if verification.status is not TemplateVerificationStatus.PASSED:
        return f"the latest verification of starter kit {template_id} {verification.status.value}"
    try:
        checksum, digests = fingerprint(template_id)
    except Exception:  # e.g. no image lock: currency cannot be confirmed, so never available
        return f"the verification of starter kit {template_id} cannot be confirmed as current"
    same_images = all(digests.get(name) == value for name, value in verification.image_digests.items())
    if verification.template_checksum != checksum or not same_images:
        return f"the verification of starter kit {template_id} is stale (the kit or an image changed)"
    return None


def evaluate_entry(  # noqa: PLR0911 - one return per effective status
    entry: CapabilityEntry,
    *,
    kits_root: Path,
    verifications: Mapping[str, TemplateVerification],
    fingerprint: Fingerprint,
) -> EffectiveEntry:
    if entry.status is CatalogStatus.UNSUPPORTED:
        reason = entry.known_limitations[0] if entry.known_limitations else "not supported by the platform"
        return EffectiveEntry(entry, EffectiveStatus.UNSUPPORTED, reason)
    if entry.status is CatalogStatus.LATER_CHANGE_REQUEST:
        return EffectiveEntry(
            entry,
            EffectiveStatus.LATER_CHANGE_REQUEST,
            "can be added later through a change request (Part Q)",
        )
    assert entry.status in DECLARED_SUPPORTED  # noqa: S101 - the enum has no other value
    if entry.available_from_part is not None:
        return EffectiveEntry(
            entry, EffectiveStatus.DEMOTED, f"not available yet: arrives in Part {entry.available_from_part}"
        )
    recipes = [*([entry.build_recipe] if entry.build_recipe else []), *entry.test_recipes]
    unknown = [r for r in recipes if r not in RECIPES]
    if unknown:
        return EffectiveEntry(entry, EffectiveStatus.DEMOTED, f"unregistered recipes: {', '.join(unknown)}")
    reasons: list[str] = []
    passing: list[str] = []
    for template_id in entry.templates:
        verification = verifications.get(template_id)
        problem = _kit_problem(template_id, kits_root, verification, fingerprint)
        if problem is None and verification is not None:
            passing.append(verification.verification_id)
        elif problem is not None:
            reasons.append(problem)
    if not passing:
        return EffectiveEntry(entry, EffectiveStatus.DEMOTED, "; ".join(reasons) or "no verified starter kit")
    limited = (
        entry.status is not CatalogStatus.SUPPORTED
        or entry.network_requirement is NetworkRequirement.PUBLIC_NO_CREDENTIALS
    )
    if limited:
        default = entry.status.value.replace("_", " ")
        reason = entry.known_limitations[0] if entry.known_limitations else default
        return EffectiveEntry(entry, EffectiveStatus.AVAILABLE_LIMITED, reason, tuple(sorted(passing)))
    return EffectiveEntry(entry, EffectiveStatus.AVAILABLE, None, tuple(sorted(passing)))


def default_fingerprint(settings: Any) -> Fingerprint:
    """The kit checksum and locked image IDs, as computed for template verification (Part E)."""
    from devteam_services.templates import current_fingerprint  # noqa: PLC0415 - keeps imports light

    return lambda template_id: current_fingerprint(settings, template_id)


def effective_catalog(
    loaded: LoadedCatalog,
    session: Session,
    *,
    kits_root: Path,
    fingerprint: Fingerprint,
) -> CatalogSnapshot:
    """Recompute every entry's effective status from SQLite and the kits (never cached across runs)."""
    verifications = sandbox_repo.latest_finished_verifications(session)
    entries = {
        entry.key: evaluate_entry(
            entry, kits_root=kits_root, verifications=verifications, fingerprint=fingerprint
        )
        for entry in loaded.catalog.entries
    }
    return CatalogSnapshot(
        catalog=loaded.catalog, catalog_hash=loaded.digest, entries=MappingProxyType(entries)
    )


def record_status_changes(session: Session, snapshot: CatalogSnapshot, *, clock: Clock = system_clock) -> int:
    """Audit a demotion (or a restoration) once per change, not on every start-up. Returns events written."""
    written = 0
    for item in snapshot.entries.values():
        if item.entry.status not in DECLARED_SUPPORTED:
            continue
        last = audit.latest_for_target(
            session,
            actions=(DEMOTED_ACTION, RESTORED_ACTION),
            target_type="catalog_entry",
            target_id=item.key.value,
        )
        was_demoted = last is not None and last.action == DEMOTED_ACTION
        last_reason = (last.details or {}).get("reason") if last is not None else None
        now_demoted = item.status is EffectiveStatus.DEMOTED
        if now_demoted and (not was_demoted or last_reason != item.reason):
            action = DEMOTED_ACTION
        elif was_demoted and not now_demoted:
            action = RESTORED_ACTION
        else:
            continue
        audit.append(
            session,
            action=action,
            outcome="success",
            principal_id=PRINCIPAL,
            target_type="catalog_entry",
            target_id=item.key.value,
            details={
                "status": item.status.value,
                "reason": (item.reason or "")[:300] or None,
                "catalog_hash": snapshot.catalog_hash,
                "verification_ids": list(item.verification_ids),
            },
            clock=clock,
        )
        if action == DEMOTED_ACTION:
            _log.warning("catalog_entry_demoted", key=item.key.value, reason=item.reason)
        written += 1
    return written


__all__ = [
    "CATALOG_DIR",
    "CATALOG_FILE",
    "PRINCIPAL",
    "SCHEMA_FILE",
    "CatalogError",
    "CatalogSnapshot",
    "EffectiveEntry",
    "Fingerprint",
    "LoadedCatalog",
    "code_root",
    "default_fingerprint",
    "effective_catalog",
    "evaluate_entry",
    "load_catalog",
    "parse_catalog",
    "record_status_changes",
    "reference_problems",
    "render_catalog_schema",
]

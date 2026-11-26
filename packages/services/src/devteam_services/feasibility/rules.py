"""Status rules, dependency propagation and coverage (Spec §5.3, §13.2; PART-G-PLAN §6.3-§6.6; D-02, D-60,
D-63, D-64). Pure functions: the same analysis, catalog snapshot and approvals always give the same result.

Status per subject (§6.3):

- effectively ``available``: ``supported`` (``partial`` if the requested version is outside the kit's);
- ``available_limited``: ``partial`` (the limitation is the reason);
- unsupported, later change request or demoted: ``supported_with_substitution`` when an available
  alternative exists (**offered**, pending approval), else ``not_supported``;
- not in the catalog, ambiguous or a rejected key: ``not_supported`` plus a gap for the clarification form;
- "SQL" without an engine (D-64): SQLite offered (pending) plus a technology gap; never an engine.

Propagation (D-60): explicit ``requirement_ids``; otherwise a whole-application layer affects every in-scope
FR and BR key; an unlinked integration or authentication need is treated conservatively the same way, with
a gap "affected requirements not identified". A business rule also takes the effects of the FRs it
references. Per requirement the worst effect wins: blocked > pending_substitution > partial > buildable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from devteam_core.contracts.agents import BrdAnalystOutput
from devteam_core.contracts.base import SourceRef
from devteam_core.contracts.catalog import OFFERABLE, CapabilityCategory, CatalogKey, EffectiveStatus
from devteam_core.contracts.feasibility import EFFECT_PRECEDENCE, RequirementEffectKind
from devteam_core.contracts.records import (
    FeasibilitySubjectKind,
    GapCategory,
    GapKind,
    RequirementType,
    SubstitutionState,
    TechnologyLayer,
)
from devteam_core.contracts.statuses import FeasibilityStatus
from devteam_services.catalog import CatalogSnapshot
from devteam_services.feasibility.mapping import (
    INTEGRATION_CATEGORIES,
    LAYER_CATEGORIES,
    Mapped,
    map_subject,
    requested_version,
    version_compatible,
)

S = FeasibilityStatus
L = TechnologyLayer
Effect = RequirementEffectKind

# Layers whose choice shapes the whole application (an unlinked need affects every requirement).
WHOLE_APPLICATION_LAYERS = frozenset(
    {L.FRONTEND, L.BACKEND, L.DATABASE, L.DEPLOYMENT, L.LANGUAGE, L.FRAMEWORK, L.RUNTIME}
)
# One choice per application: two different answers are a conflict (Spec §21 "never silently choose").
SINGLE_CHOICE_LAYERS = frozenset({L.FRONTEND, L.BACKEND, L.DATABASE})
SQL_GAP = (
    "Database engine not specified; if accepted, SQLite is used and disclosed in the summary (Spec §13.2)."
)
LINKS_GAP = "Affected requirements not identified; treated as affecting every requirement until clarified."

_GAP_CATEGORY = {L.AUTHENTICATION: GapCategory.LOGIN, L.DEPLOYMENT: GapCategory.DEPLOYMENT}


@dataclass(frozen=True)
class Subject:
    kind: FeasibilitySubjectKind
    subject_id: str
    name: str
    layer: TechnologyLayer | None
    version_spec: str | None
    proposed_key: str | None
    requirement_ids: tuple[str, ...]
    source_refs: tuple[SourceRef, ...]

    @property
    def categories(self) -> tuple[CapabilityCategory, ...]:
        return INTEGRATION_CATEGORIES if self.layer is None else LAYER_CATEGORIES[self.layer]

    @property
    def gap_category(self) -> GapCategory:
        if self.layer is None:
            return GapCategory.INTEGRATIONS
        return _GAP_CATEGORY.get(self.layer, GapCategory.TECHNOLOGY)


@dataclass(frozen=True)
class GapDraft:
    category: GapCategory
    kind: GapKind
    description: str
    source_refs: tuple[SourceRef, ...]


@dataclass(frozen=True)
class Assessment:
    subject: Subject
    mapped: Mapped
    status: FeasibilityStatus
    reason: str | None
    alternative: CatalogKey | None
    substitution: SubstitutionState
    affected: tuple[str, ...]
    whole_application: bool
    gap: GapDraft | None

    @property
    def effect(self) -> RequirementEffectKind:
        if self.status is S.NOT_SUPPORTED:
            return Effect.BLOCKED
        if self.status is S.SUPPORTED_WITH_SUBSTITUTION:
            return (
                Effect.BUILDABLE
                if self.substitution is SubstitutionState.APPROVED
                else Effect.PENDING_SUBSTITUTION
            )
        return Effect.PARTIAL if self.status is S.PARTIAL else Effect.BUILDABLE


def subjects_of(analysis: BrdAnalystOutput) -> list[Subject]:
    needs = [
        Subject(FeasibilitySubjectKind.TECHNOLOGY_NEED, n.tech_id, n.name, n.layer, n.version_spec,
                n.catalog_key, tuple(n.requirement_ids), tuple(n.source_refs))
        for n in analysis.technology_needs
    ]  # fmt: skip
    integrations = [
        Subject(FeasibilitySubjectKind.INTEGRATION, i.integration_id, i.name, None, None, i.catalog_key,
                tuple(i.requirement_ids), tuple(i.source_refs))
        for i in analysis.integrations
    ]  # fmt: skip
    return needs + integrations


@dataclass(frozen=True)
class Scope:
    functional: tuple[str, ...]
    non_functional: tuple[str, ...]
    rules: tuple[str, ...]
    rule_links: Mapping[str, tuple[str, ...]]

    @property
    def counted(self) -> tuple[str, ...]:  # D-02: the coverage denominator
        return self.functional + self.rules

    @property
    def every(self) -> tuple[str, ...]:
        return self.functional + self.non_functional + self.rules


def scope_of(analysis: BrdAnalystOutput, excluded: frozenset[str] = frozenset()) -> Scope:
    fr = tuple(r.requirement_id for r in analysis.requirements if r.type is RequirementType.FUNCTIONAL)
    nfr = tuple(r.requirement_id for r in analysis.requirements if r.type is RequirementType.NON_FUNCTIONAL)
    rules = tuple(b.rule_id for b in analysis.business_rules)
    links = {b.rule_id: tuple(b.requirement_ids) for b in analysis.business_rules}
    return Scope(
        functional=tuple(k for k in fr if k not in excluded),
        non_functional=tuple(k for k in nfr if k not in excluded),
        rules=tuple(k for k in rules if k not in excluded),
        rule_links=links,
    )


def _offer(
    snapshot: CatalogSnapshot, candidates: tuple[CatalogKey, ...], approved: CatalogKey | None
) -> tuple[CatalogKey | None, SubstitutionState]:
    """The substitution to offer: the approved one if it is still offerable, else the first offerable."""
    offerable = [s for s in candidates if snapshot.entries[s].status in OFFERABLE]
    if approved is not None and approved in offerable:
        return approved, SubstitutionState.APPROVED
    return (offerable[0], SubstitutionState.OFFERED) if offerable else (None, SubstitutionState.NONE)


def _status_text(snapshot: CatalogSnapshot, key: CatalogKey) -> str:
    item = snapshot.entries[key]
    name = item.entry.display_name
    match item.status:
        case EffectiveStatus.UNSUPPORTED:
            return f"{name} is not supported: {item.reason}"
        case EffectiveStatus.LATER_CHANGE_REQUEST:
            return f"{name} is not available now: {item.reason}"
        case _:
            return f"{name} is not available: {item.reason}"


def assess(  # noqa: PLR0911 - one return per rule row
    subject: Subject, snapshot: CatalogSnapshot, approved: CatalogKey | None
) -> tuple[Mapped, FeasibilityStatus, str | None, CatalogKey | None, SubstitutionState, GapDraft | None]:
    mapped = map_subject(subject.name, subject.categories, subject.proposed_key, snapshot.catalog)
    unclear = GapDraft(subject.gap_category, GapKind.UNCLEAR, "", subject.source_refs)
    if mapped.ambiguous_database:  # D-64: never an enterprise engine, never silent
        gap = GapDraft(GapCategory.TECHNOLOGY, GapKind.UNCLEAR, SQL_GAP, subject.source_refs)
        alternative, state = _offer(snapshot, (CatalogKey.SQLITE,), approved)
        if alternative is None:
            reason = f"'{subject.name}' names no database engine, and SQLite is not available: " + (
                snapshot.entries[CatalogKey.SQLITE].reason or "demoted"
            )
            return mapped, S.NOT_SUPPORTED, reason, None, SubstitutionState.NONE, gap
        reason = f"'{subject.name}' names no database engine; SQLite (the platform default) is offered"
        return mapped, S.SUPPORTED_WITH_SUBSTITUTION, reason, alternative, state, gap
    if mapped.key is None:
        if mapped.ambiguous_categories:
            kinds = ", ".join(c.value for c in mapped.ambiguous_categories)
            reason = f"'{subject.name}' could mean several kinds of capability ({kinds}); it was not mapped"
        else:
            reason = f"'{subject.name}' is not in the Capability Catalog"
        gap = GapDraft(unclear.category, GapKind.UNCLEAR, f"{reason}; what is needed?", subject.source_refs)
        return mapped, S.NOT_SUPPORTED, reason, None, SubstitutionState.NONE, gap
    item = snapshot.entries[mapped.key]
    if item.status is EffectiveStatus.AVAILABLE:
        version = requested_version(subject.name, subject.version_spec)
        kit_versions = item.entry.compatible_versions
        if version is not None and not version_compatible(version, kit_versions):
            reason = f"version {version} requested; the starter kit provides {', '.join(kit_versions)}"
            return mapped, S.PARTIAL, reason, None, SubstitutionState.NONE, None
        return mapped, S.SUPPORTED, None, None, SubstitutionState.NONE, None
    if item.status is EffectiveStatus.AVAILABLE_LIMITED:
        return (
            mapped,
            S.PARTIAL,
            f"{item.entry.display_name}: {item.reason}",
            None,
            SubstitutionState.NONE,
            None,
        )
    reason = _status_text(snapshot, mapped.key)
    alternative, state = _offer(snapshot, item.entry.substitutions, approved)
    if alternative is None:
        return mapped, S.NOT_SUPPORTED, reason, None, SubstitutionState.NONE, None
    return mapped, S.SUPPORTED_WITH_SUBSTITUTION, reason, alternative, state, None


def assess_all(
    analysis: BrdAnalystOutput,
    snapshot: CatalogSnapshot,
    scope: Scope,
    approved: Mapping[str, CatalogKey],
) -> list[Assessment]:
    known = set(scope.every)
    out: list[Assessment] = []
    for subject in subjects_of(analysis):
        mapped, status, reason, alternative, state, gap = assess(
            subject, snapshot, approved.get(subject.subject_id)
        )
        linked = tuple(k for k in subject.requirement_ids if k in known)
        whole = not linked
        affected = linked or scope.counted
        conservative = whole and (subject.layer is None or subject.layer not in WHOLE_APPLICATION_LAYERS)
        if conservative and status is not S.SUPPORTED and gap is None:
            gap = GapDraft(subject.gap_category, GapKind.UNCLEAR, LINKS_GAP, subject.source_refs)
        out.append(Assessment(subject, mapped, status, reason, alternative, state, affected, whole, gap))
    return _with_conflicts(out)


def _with_conflicts(assessments: list[Assessment]) -> list[Assessment]:
    """Two different whole-application choices on a single-choice layer: a conflicting gap, never a pick."""
    first: dict[TechnologyLayer, Assessment] = {}
    out: list[Assessment] = []
    for a in assessments:
        layer = a.subject.layer
        if layer in SINGLE_CHOICE_LAYERS and a.whole_application and a.mapped.key is not None:
            earlier = first.setdefault(layer, a)
            if earlier is not a and earlier.mapped.key is not a.mapped.key and a.gap is None:
                description = (
                    f"Conflicting {layer.value} choices: '{earlier.subject.name}' and '{a.subject.name}'; "
                    "the platform never chooses silently (Spec §21)"
                )
                refs = (*earlier.subject.source_refs, *a.subject.source_refs)[:50]
                gap = GapDraft(GapCategory.TECHNOLOGY, GapKind.CONFLICTING, description, refs)
                a = Assessment(**{**a.__dict__, "gap": gap})  # noqa: PLW2901 - rebinding to the copy
        out.append(a)
    return out


def effects_of(
    assessments: list[Assessment], scope: Scope, item_id: Callable[[Assessment], str]
) -> dict[str, tuple[RequirementEffectKind, tuple[str, ...]]]:
    """Every analysed key's effect and the items that cause it (worst effect wins)."""
    direct: dict[str, list[tuple[RequirementEffectKind, str]]] = {k: [] for k in scope.every}
    for a in assessments:
        for key in a.affected:
            direct[key].append((a.effect, item_id(a)))
    for rule, frs in scope.rule_links.items():  # a business rule follows the FRs it references
        if rule in direct:
            for fr in frs:
                direct[rule].extend(direct.get(fr, []))
    approved = {item_id(a) for a in assessments if a.substitution is SubstitutionState.APPROVED}
    result: dict[str, tuple[RequirementEffectKind, tuple[str, ...]]] = {}
    for key, entries in direct.items():
        worst = max((e for e, _ in entries), key=EFFECT_PRECEDENCE.__getitem__, default=Effect.BUILDABLE)
        if worst is Effect.BUILDABLE:  # a buildable key names only the approved substitutions it relies on
            causes = {i for _, i in entries if i in approved}
        else:
            causes = {i for e, i in entries if e is worst}
        result[key] = (worst, tuple(sorted(causes)))
    return result


@dataclass(frozen=True)
class Coverage:
    numerator: int
    denominator: int
    blocked: tuple[str, ...]
    pending: tuple[str, ...]
    partial: tuple[str, ...]
    pending_counted: int  # FR/BR keys waiting for an offered substitution (DV-034)


def coverage_of_effects(
    effects: Mapping[str, tuple[RequirementEffectKind, tuple[str, ...]]], scope: Scope
) -> Coverage:
    """D-02 (a): FR + BR keys; buildable = supported or every cause substitution approved; partial apart."""
    counted = scope.counted
    numerator = sum(1 for k in counted if effects[k][0] is Effect.BUILDABLE)

    def keys(kind: RequirementEffectKind) -> tuple[str, ...]:
        return tuple(k for k in scope.every if effects[k][0] is kind)

    pending_counted = sum(1 for k in counted if effects[k][0] is Effect.PENDING_SUBSTITUTION)
    return Coverage(
        numerator,
        len(counted),
        keys(Effect.BLOCKED),
        keys(Effect.PENDING_SUBSTITUTION),
        keys(Effect.PARTIAL),
        pending_counted,
    )


__all__ = [
    "LINKS_GAP",
    "SQL_GAP",
    "Assessment",
    "Coverage",
    "GapDraft",
    "Scope",
    "Subject",
    "assess",
    "assess_all",
    "coverage_of_effects",
    "effects_of",
    "scope_of",
    "subjects_of",
]

"""Deterministic mapping of an extracted subject to a catalog key (Spec §5.3; PART-G-PLAN §6.3, §6.5).

Only the *names the BRD Analyst extracted* are mapped, never raw BRD text (so prose like "react to events"
cannot match). A name is normalised (lower case, single spaces, outer punctuation removed) and compared
exactly with the aliases of the catalog categories that fit the subject's layer. A model-proposed key is
accepted only if it is a ``CatalogKey`` member of one of those categories; anything else is rejected
(guardrail HC-01) and reported so the caller can audit it. Database wording that names no engine ("SQL")
is flagged, never mapped to an enterprise product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from devteam_core.contracts.catalog import CapabilityCatalog, CapabilityCategory, CatalogKey
from devteam_core.contracts.records import TechnologyLayer

C = CapabilityCategory
L = TechnologyLayer
ALL_CATEGORIES = (C.FRONTEND, C.BACKEND, C.DATABASE, C.AUTH, C.DEPLOYMENT, C.INTEGRATION)

LAYER_CATEGORIES: dict[TechnologyLayer, tuple[CapabilityCategory, ...]] = {
    L.FRONTEND: (C.FRONTEND,),
    L.BACKEND: (C.BACKEND,),
    L.DATABASE: (C.DATABASE,),
    L.AUTHENTICATION: (C.AUTH,),
    L.DEPLOYMENT: (C.DEPLOYMENT,),
    L.LANGUAGE: ALL_CATEGORIES,
    L.FRAMEWORK: ALL_CATEGORIES,
    L.RUNTIME: ALL_CATEGORIES,
    L.OTHER: ALL_CATEGORIES,
}
INTEGRATION_CATEGORIES = (C.INTEGRATION, C.AUTH)

_SPACES = re.compile(r"\s+")
_OUTER = re.compile(r"^[\s\"'`.,;:()\[\]]+|[\s\"'`.,;:()\[\]]+$")
_TRAILING_VERSION = re.compile(r"\s+v?[0-9]+(\.[0-9x*]+)*(\s*(lts|or later|\+))?$")
_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*")


def normalise(name: str) -> str:
    return _SPACES.sub(" ", _OUTER.sub("", name.casefold())).strip()


def without_version(name: str) -> str:
    return _TRAILING_VERSION.sub("", name).strip()


def requested_version(name: str, version_spec: str | None) -> str | None:
    """The first dotted number of the version field, or trailing the name ("React 18"); else None."""
    if version_spec:
        match = _VERSION.search(version_spec)
        return match.group(0) if match else None
    text = normalise(name)
    stripped = without_version(text)
    if stripped and stripped != text:
        match = _VERSION.search(text[len(stripped) :])
        return match.group(0) if match else None
    return None


def version_compatible(requested: str, compatible: tuple[str, ...]) -> bool:
    """Component-wise prefix match: "19.2" fits "19"; "3.11" does not fit "3.12". No list: no constraint."""
    if not compatible:
        return True
    parts = requested.split(".")
    return any(parts[: len(c.split("."))] == c.split(".") for c in compatible)


@dataclass(frozen=True)
class Mapped:
    key: CatalogKey | None
    ambiguous_database: bool = False  # Spec §13.2 "SQL" rule applies
    ambiguous_categories: tuple[CapabilityCategory, ...] = ()  # the name fits several categories
    rejected_proposal: str | None = None  # a model-proposed key that was refused (to be audited)
    by: str = "none"  # "alias" | "proposed" | "none"


def map_subject(
    name: str,
    categories: tuple[CapabilityCategory, ...],
    proposed: str | None,
    catalog: CapabilityCatalog,
) -> Mapped:
    rejected: str | None = None
    if proposed is not None:
        try:
            key = CatalogKey(proposed)
        except ValueError:
            key = None
        if key is not None and catalog.entry(key).category in categories:
            return Mapped(key=key, by="proposed")
        rejected = proposed[:80]
    candidates = [normalise(name)]
    stripped = without_version(candidates[0])
    if stripped and stripped != candidates[0]:
        candidates.append(stripped)
    for text in candidates:
        if C.DATABASE in categories and text in catalog.ambiguous_database_aliases:
            return Mapped(key=None, ambiguous_database=True, rejected_proposal=rejected)
        matches = [e for e in catalog.entries if e.category in categories and text in e.aliases]
        if len(matches) == 1:
            return Mapped(key=matches[0].key, rejected_proposal=rejected, by="alias")
        if len(matches) > 1:
            return Mapped(
                key=None,
                ambiguous_categories=tuple(sorted({m.category for m in matches}, key=list(C).index)),
                rejected_proposal=rejected,
            )
    return Mapped(key=None, rejected_proposal=rejected)


__all__ = [
    "INTEGRATION_CATEGORIES",
    "LAYER_CATEGORIES",
    "Mapped",
    "map_subject",
    "normalise",
    "requested_version",
    "version_compatible",
    "without_version",
]

"""Licence policy for sandbox images (Part E plan §12.3). Pure functions; no I/O except reading the policy.

A package row is ``(ecosystem, name, version, raw licence, tier)``. :func:`evaluate` returns the problems
(empty = compliant). Tier 1 (operating-system packages) accepts any free licence and is only recorded;
tiers 2-4 must normalise to a known identifier; tier 4 must be permissive; tier 3 may additionally use a
``tier3_review`` licence only for packages listed under ``tier3_reviewed``. Unknown licences are refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

Ecosystem = Literal["debian", "python", "node"]


@dataclass(frozen=True, slots=True)
class PackageLicence:
    ecosystem: Ecosystem
    name: str
    version: str
    licence: str  # raw text from metadata (empty if none)
    tier: int


@dataclass(frozen=True, slots=True)
class LicencePolicy:
    permissive: frozenset[str]
    tier3_review: frozenset[str]
    refused_markers: tuple[str, ...]
    aliases: dict[str, str]
    tier3_reviewed: dict[str, str]
    tier4_python: frozenset[str]
    tier4_node: frozenset[str]

    @classmethod
    def load(cls, path: Path) -> LicencePolicy:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            permissive=frozenset(data["permissive"]),
            tier3_review=frozenset(data["tier3_review"]),
            refused_markers=tuple(data["refused_markers"]),
            aliases=dict(data["aliases"]),
            tier3_reviewed=dict(data["tier3_reviewed"]),
            tier4_python=frozenset(n.lower() for n in data["tier4"]["python"]),
            tier4_node=frozenset(data["tier4"]["node"]),
        )

    def is_tier4(self, ecosystem: Ecosystem, name: str) -> bool:
        if ecosystem == "python":
            return name.lower().replace("_", "-") in self.tier4_python
        return ecosystem == "node" and name in self.tier4_node


_SPLIT = re.compile(r"\s+(OR|AND)\s+|\s*/\s*|\s*;\s*")


def normalise(raw: str, policy: LicencePolicy) -> tuple[list[list[str]], bool]:
    """Turn a licence string into alternatives (OR) of conjunctions (AND) of identifiers.

    Returns ``(alternatives, parsed)``; ``parsed`` is False when the text could not be understood.
    ``/`` and ``;`` (used when several classifiers are joined) count as OR, as in package metadata.
    """
    text = raw.strip()
    if text in policy.aliases:  # whole-text aliases first: some contain parentheses
        return [[policy.aliases[text]]], True
    text = text.strip("()")
    if not text:
        return [], False
    alternatives: list[list[str]] = [[]]
    tokens = _SPLIT.split(text)
    operator = "OR"
    for token in tokens:
        if token is None:
            continue
        piece = token.strip().strip("()")
        if piece in {"OR", "AND"}:
            operator = piece
            continue
        if not piece:
            continue
        identifier = policy.aliases.get(piece, piece)
        if operator == "AND" and alternatives[-1]:
            alternatives[-1].append(identifier)
        elif alternatives[-1]:
            alternatives.append([identifier])
        else:
            alternatives[-1].append(identifier)
        operator = "OR"
    known = policy.permissive | policy.tier3_review
    parsed = all(all(i in known or _refused(i, policy) for i in alt) for alt in alternatives)
    return alternatives, parsed


def _refused(identifier: str, policy: LicencePolicy) -> bool:
    lowered = identifier.lower()
    return any(marker.lower() in lowered for marker in policy.refused_markers) or (
        "gpl" in lowered and "lgpl" not in lowered
    )


def evaluate(packages: list[PackageLicence], policy: LicencePolicy) -> list[str]:
    problems: list[str] = []
    for pkg in packages:
        if pkg.tier == 1:
            continue  # recorded only; any free OS licence is allowed (plan §12.3)
        label = f"{pkg.ecosystem}:{pkg.name}=={pkg.version} ({pkg.licence or 'no licence'})"
        alternatives, parsed = normalise(pkg.licence, policy)
        if not parsed:
            problems.append(f"{label}: unknown licence")
            continue
        acceptable: set[str] = set(policy.permissive)
        if pkg.tier == 3 and pkg.name in policy.tier3_reviewed:
            acceptable |= policy.tier3_review
        if not any(all(i in acceptable for i in alt) for alt in alternatives):
            reason = "not permissive (tier 4)" if pkg.tier == 4 else "not allowed for this tier"
            problems.append(f"{label}: {reason}")
    return problems


__all__ = ["LicencePolicy", "PackageLicence", "evaluate", "normalise"]

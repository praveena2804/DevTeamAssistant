"""Feasibility Gate engine (Part G G.3; AC-G-01, AC-G-03, AC-G-04; D-02, D-60, D-63, D-64).

Rules are tested on validated fixture analyses (D-65: the Part D analyst extracts no technologies, so these
scenarios cannot come from a real run before Part H). The catalog snapshot is computed by the real catalog
service from verification records; only the fingerprint is fixed.
"""

from __future__ import annotations

from typing import Any

import pytest

from devteam_core.contracts.agents import BrdAnalystOutput
from devteam_core.contracts.catalog import CatalogKey
from devteam_core.contracts.feasibility import CoverageRecommendation, RequirementEffectKind
from devteam_core.contracts.records import (
    GapCategory,
    GapKind,
    SubstitutionState,
)
from devteam_core.contracts.statuses import FeasibilityStatus
from devteam_services import catalog as cat
from devteam_services.feasibility.mapping import map_subject, normalise, requested_version, version_compatible
from devteam_services.feasibility.rules import assess_all, coverage_of_effects, effects_of, scope_of
from tests.fixtures.feasibility_engine import COUNTED, fixture, snapshot

S = FeasibilityStatus
E = RequirementEffectKind
R = CoverageRecommendation
K = CatalogKey


def evaluate(
    name: str, snap: cat.CatalogSnapshot | None = None, approved: dict[str, Any] | None = None
) -> Any:
    analysis = fixture(name)
    scope = scope_of(analysis)
    assessments = assess_all(analysis, snap or snapshot(), scope, approved or {})
    effects = effects_of(assessments, scope, lambda a: f"item:{a.subject.name}")
    return assessments, effects, coverage_of_effects(effects, scope)


# --------------------------------------------------------------------------------------------- mapping


def test_mapping_is_exact_on_extracted_names_and_never_on_prose() -> None:
    from devteam_core.contracts.catalog import CapabilityCategory as C  # noqa: PLC0415

    catalog = cat.load_catalog().catalog
    assert map_subject("React", (C.FRONTEND,), None, catalog).key is K.REACT_VITE
    assert map_subject("  ReactJS. ", (C.FRONTEND,), None, catalog).key is K.REACT_VITE
    assert map_subject("React 18", (C.FRONTEND,), None, catalog).key is K.REACT_VITE  # version stripped
    assert map_subject("react to events quickly", (C.FRONTEND,), None, catalog).key is None  # near miss
    assert map_subject("Power Apps", (C.DEPLOYMENT,), None, catalog).key is K.POWER_APPS
    assert map_subject("Power Apps", (C.FRONTEND,), None, catalog).key is K.POWER_APPS_CANVAS
    both = map_subject("Power Apps", (C.FRONTEND, C.DEPLOYMENT), None, catalog)
    assert both.key is None and both.ambiguous_categories == (C.FRONTEND, C.DEPLOYMENT)
    assert map_subject("SQL", (C.DATABASE,), None, catalog).ambiguous_database
    assert not map_subject("SQL", (C.FRONTEND,), None, catalog).ambiguous_database
    assert normalise('"Angular" ') == "angular"


def test_a_proposed_key_is_accepted_only_if_it_is_a_catalog_key_of_the_right_category() -> None:
    from devteam_core.contracts.catalog import CapabilityCategory as C  # noqa: PLC0415

    catalog = cat.load_catalog().catalog
    accepted = map_subject("our web UI", (C.FRONTEND,), "react_vite", catalog)
    assert accepted.key is K.REACT_VITE and accepted.by == "proposed" and accepted.rejected_proposal is None
    for proposal in ("svelte_kit", "sqlite", "REACT_VITE; DROP TABLE"):
        mapped = map_subject("React", (C.FRONTEND,), proposal, catalog)
        assert mapped.rejected_proposal == proposal and mapped.key is K.REACT_VITE  # alias still applies


@pytest.mark.parametrize(
    ("name", "spec", "expected"),
    [("React", "18", "18"), ("React 18.2", None, "18.2"), ("React", None, None), ("Angular", "latest", None)],
)
def test_the_requested_version_is_read_from_the_version_field_or_the_name(
    name: str, spec: str | None, expected: str | None
) -> None:
    assert requested_version(name, spec) == expected


def test_version_compatibility_is_a_component_prefix_match() -> None:
    assert version_compatible("19.2", ("19",)) and version_compatible("3.12.1", ("3.12", "0.165"))
    assert not version_compatible("18", ("19",)) and not version_compatible("3.1", ("3.12",))
    assert version_compatible("1", ())


# ----------------------------------------------------------------------------------- scenarios (fixtures)


def test_power_apps_deployment_is_not_supported_and_blocks_every_requirement() -> None:
    """Spec scenario #3 / AC-G-01 (engine level)."""
    assessments, effects, coverage = evaluate("power_apps")
    (item,) = assessments
    assert item.mapped.key is K.POWER_APPS and item.status is S.NOT_SUPPORTED and item.alternative is None
    assert "Power Apps (deployment platform) is not supported" in (item.reason or "")
    assert item.whole_application and item.affected == COUNTED
    assert all(effects[k][0] is E.BLOCKED for k in COUNTED)
    assert effects["NFR-001"][0] is E.BUILDABLE  # NFRs are reported, not counted (and not whole-app targets)
    assert (coverage.numerator, coverage.denominator, coverage.blocked) == (0, 5, COUNTED)
    assert coverage.pending_counted == 0  # nothing to build: proceed disabled (D-63)


def test_angular_gets_a_pending_react_offer_and_is_not_buildable_until_approved() -> None:
    assessments, effects, coverage = evaluate("angular")
    (item,) = assessments
    assert item.status is S.SUPPORTED_WITH_SUBSTITUTION and item.alternative is K.REACT_VITE
    assert item.substitution is SubstitutionState.OFFERED and "Angular is not supported" in (
        item.reason or ""
    )
    assert all(effects[k][0] is E.PENDING_SUBSTITUTION for k in COUNTED)
    assert (
        coverage.numerator == 0 and coverage.pending_counted == 5
    )  # stop recommended, not "nothing" (DV-034)
    tech_id = fixture("angular").technology_needs[0].tech_id
    _, approved_effects, approved_cov = evaluate("angular", approved={tech_id: K.REACT_VITE})
    assert approved_cov.numerator == 5 and approved_effects["FR-001"] == (E.BUILDABLE, ("item:Angular",))


def test_an_offer_falls_back_to_the_next_available_alternative() -> None:
    (item,), _, _ = evaluate("angular", snapshot("html_fastapi_sqlite"))  # React kit not verified
    assert item.alternative is K.HTML_CSS_JS and item.substitution is SubstitutionState.OFFERED
    (item,), _, _ = evaluate("angular", snapshot("html_fastapi_sqlite"), {"x": K.REACT_VITE})
    assert item.alternative is K.HTML_CSS_JS  # an approval for something not offerable is not used


def test_sql_server_is_unsupported_with_a_sqlite_offer() -> None:
    (item,), _, _ = evaluate("sql_server")
    assert item.mapped.key is K.SQL_SERVER and item.status is S.SUPPORTED_WITH_SUBSTITUTION
    assert item.alternative is K.SQLITE and "Microsoft SQL Server is not supported" in (item.reason or "")


def test_ambiguous_sql_offers_sqlite_and_raises_a_technology_gap() -> None:
    """D-64: never mapped to an engine, never silent."""
    (item,), effects, _ = evaluate("sql_ambiguous")
    assert item.mapped.key is None and item.mapped.ambiguous_database
    assert item.status is S.SUPPORTED_WITH_SUBSTITUTION and item.alternative is K.SQLITE
    assert item.substitution is SubstitutionState.OFFERED
    assert (
        item.gap is not None
        and item.gap.category is GapCategory.TECHNOLOGY
        and item.gap.kind is GapKind.UNCLEAR
    )
    assert "SQLite is used and disclosed" in item.gap.description
    assert effects["FR-001"][0] is E.PENDING_SUBSTITUTION
    (blocked,), effects, _ = evaluate("sql_ambiguous", snapshot(""))  # no verified kit: SQLite is demoted too
    assert blocked.status is S.NOT_SUPPORTED and "SQLite is not available" in (blocked.reason or "")
    assert blocked.gap is not None and effects["FR-001"][0] is E.BLOCKED


def test_postgresql_is_demoted_until_part_k_with_a_sqlite_offer() -> None:
    (item,), _, _ = evaluate("postgresql")
    assert item.mapped.key is K.POSTGRESQL_DOCKER_LOCAL and item.status is S.SUPPORTED_WITH_SUBSTITUTION
    assert "arrives in Part K" in (item.reason or "") and item.alternative is K.SQLITE


def test_the_mixed_scenario_combines_partial_pending_and_blocked_per_requirement() -> None:
    assessments, effects, coverage = evaluate("mixed")
    by = {a.subject.name: a for a in assessments}
    assert by["React 18"].status is S.PARTIAL and "version 18 requested" in (by["React 18"].reason or "")
    assert by["FastAPI"].status is S.SUPPORTED
    assert by["a relational database"].status is S.SUPPORTED_WITH_SUBSTITUTION
    assert by["Stripe"].status is S.NOT_SUPPORTED and by["Stripe"].affected == ("FR-003",)
    assert by["Azure AD"].status is S.NOT_SUPPORTED and by["Azure AD"].affected == ("FR-004",)
    assert not by["Stripe"].whole_application and by["Stripe"].gap is None  # linked: no "not identified" gap
    assert effects["FR-001"][0] is E.PENDING_SUBSTITUTION  # pending outranks partial
    assert effects["FR-003"] == (E.BLOCKED, ("item:Stripe",))  # blocked outranks pending
    assert effects["BR-001"][0] is E.PENDING_SUBSTITUTION  # follows FR-001
    assert coverage.blocked == ("FR-003", "FR-004") and coverage.numerator == 0


def test_everything_supported_gives_full_coverage() -> None:
    assessments, _, coverage = evaluate("all_supported")
    assert {a.status for a in assessments} == {S.SUPPORTED}
    assert (coverage.numerator, coverage.denominator) == (5, 5)


def test_no_needs_means_nothing_blocked_because_nothing_was_identified() -> None:
    assessments, _, coverage = evaluate("none")
    assert assessments == [] and (coverage.numerator, coverage.denominator) == (5, 5)


def test_unverified_kits_make_even_react_not_buildable() -> None:
    """Honest demotion flows into feasibility: without a verified kit nothing is 'supported'."""
    (react, *_), _, coverage = evaluate("all_supported", snapshot("html_fastapi_sqlite"))
    assert coverage.numerator == 0
    assert react.status is S.NOT_SUPPORTED and react.alternative is None  # React declares no alternative
    assert "React (Vite, TypeScript) is not available" in (react.reason or "")
    assert "has never been verified" in (react.reason or "")


# -------------------------------------------------------------------------------------- propagation rules


def _with(name: str, **changes: Any) -> BrdAnalystOutput:
    data = fixture(name).model_dump(mode="json")
    for key, value in changes.items():
        data[key] = value
    return BrdAnalystOutput.model_validate(data)


def test_an_unlinked_integration_is_conservative_and_raises_a_gap() -> None:
    analysis = fixture("mixed")
    data = analysis.model_dump(mode="json")
    data["integrations"][0]["requirement_ids"] = []
    analysis = BrdAnalystOutput.model_validate(data)
    scope = scope_of(analysis)
    stripe = next(a for a in assess_all(analysis, snapshot(), scope, {}) if a.subject.name == "Stripe")
    assert stripe.whole_application and stripe.affected == COUNTED
    assert stripe.gap is not None and "Affected requirements not identified" in stripe.gap.description


def test_two_different_frontends_raise_a_conflicting_gap_and_neither_is_chosen() -> None:
    data = fixture("all_supported").model_dump(mode="json")
    angular = {**data["technology_needs"][0], "tech_id": "tec_01KFE000000000000000000099", "name": "Angular"}
    data["technology_needs"].append(angular)
    analysis = BrdAnalystOutput.model_validate(data)
    assessments = assess_all(analysis, snapshot(), scope_of(analysis), {})
    react, angular_item = assessments[0], assessments[-1]
    assert react.status is S.SUPPORTED and react.gap is None
    assert angular_item.gap is not None and angular_item.gap.kind is GapKind.CONFLICTING
    assert "'React' and 'Angular'" in angular_item.gap.description


def test_excluded_keys_leave_the_denominator() -> None:
    analysis = fixture("power_apps")
    scope = scope_of(analysis, frozenset({"FR-003", "FR-004"}))
    assert scope.counted == ("FR-001", "FR-002", "BR-001")


def test_unknown_linked_keys_fall_back_to_the_whole_application() -> None:
    data = fixture("mixed").model_dump(mode="json")
    data["integrations"][0]["requirement_ids"] = ["FR-099"]
    analysis = BrdAnalystOutput.model_validate(data)
    stripe = next(
        a for a in assess_all(analysis, snapshot(), scope_of(analysis), {}) if a.subject.name == "Stripe"
    )
    assert stripe.whole_application

"""Capability Catalog (Part G G.1; AC-G-02): start-up validation fails closed, and the effective status is
honest. A declared-supported entry is available only with an installed kit, registered recipes and a
passing, current verification; each demotion cause is tested on its own."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.contracts.catalog import (
    CapabilityCatalog,
    CatalogKey,
    CatalogStatus,
    EffectiveStatus,
)
from devteam_core.contracts.sandbox import (
    RecipeOutcome,
    SandboxExecutionStatus,
    TemplateVerification,
    TemplateVerificationStatus,
    TestCounts,
)
from devteam_core.ids import new_id
from devteam_harness.sandbox.recipes import RECIPES
from devteam_services import catalog as svc
from devteam_services.catalog import CatalogError, load_catalog, parse_catalog
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.models import AuditEvent
from devteam_state.repositories import sandbox as repo

ROOT = svc.code_root()
KITS = ROOT / "templates" / "target_apps"
NOW = datetime(2026, 11, 26, tzinfo=UTC)
CHECKSUM = "a" * 64
DIGESTS = {"python_api": "sha256:" + "b" * 64, "node_web": "sha256:" + "c" * 64}
V = TemplateVerificationStatus
E = EffectiveStatus
K = CatalogKey


def same_fingerprint(_template: str) -> tuple[str, dict[str, str]]:
    return CHECKSUM, dict(DIGESTS)


def raw_catalog() -> dict[str, Any]:
    data = yaml.safe_load((ROOT / "capability_catalog" / "capabilities.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def entry_of(data: dict[str, Any], key: str) -> dict[str, Any]:
    return next(e for e in data["entries"] if e["key"] == key)


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    yield session_factory(engine)
    engine.dispose()


_COUNTER = iter(range(1, 10_000))


def store(
    sessions: sessionmaker[Session],
    template: str,
    status: V,
    *,
    checksum: str = CHECKSUM,
    digests: dict[str, str] | None = None,
) -> str:
    """A verification record as Part E writes it (queued → final status)."""
    at = NOW + timedelta(seconds=next(_COUNTER))
    queued = TemplateVerification(
        verification_id=new_id("tvf"), template_id=template, template_version="1.0.0", status=V.QUEUED,
        requested_by="prn_test", requested_at=at,
    )  # fmt: skip
    ok = RecipeOutcome(
        recipe="pytest_api", status=SandboxExecutionStatus.COMPLETED, exit_code=0, reports_tests=True,
        tests=TestCounts(tests=2, passed=2, failed=0, errors=0, skipped=0),
    )  # fmt: skip
    bad = ok.model_copy(update={"exit_code": 1, "status": SandboxExecutionStatus.FAILED, "reason": "tests"})
    final_status = V.PASSED if status is V.STALE else status
    final = queued.model_copy(
        update={
            "status": final_status,
            "recipes": (ok,) if final_status is V.PASSED else ((bad,) if final_status is V.FAILED else ()),
            "template_checksum": checksum,
            "image_digests": digests if digests is not None else dict(DIGESTS),
            "finished_at": at,
            "reason": None if final_status is V.PASSED else "did not pass",
        }
    )
    final = TemplateVerification.model_validate(final.model_dump())
    with sessions.begin() as session:
        repo.add_verification(session, queued)
        claimed = repo.claim_next_verification(session)  # queued -> running -> final, as the worker does
        assert claimed is not None and claimed.verification_id == queued.verification_id
        repo.finish_verification(session, final)
        if status is V.STALE:
            repo.mark_stale(session, template_id=template, current_checksum="f" * 64, current_digests={})
    return queued.verification_id


def snapshot(
    sessions: sessionmaker[Session],
    *,
    kits: Path = KITS,
    fingerprint: Any = same_fingerprint,
    loaded: svc.LoadedCatalog | None = None,
) -> Any:
    loaded = loaded or load_catalog()
    with sessions() as session:
        return svc.effective_catalog(loaded, session, kits_root=kits, fingerprint=fingerprint)


# ------------------------------------------------------------------------------------- start-up checks


def test_the_shipped_catalog_is_valid_and_lists_every_catalog_key_once() -> None:
    loaded = load_catalog()
    keys = [e.key for e in loaded.catalog.entries]
    assert sorted(keys) == sorted(CatalogKey) and len(keys) == len(set(keys)) == 32
    assert len(loaded.digest) == 64


def test_the_checked_in_schema_file_matches_the_contract() -> None:
    text = (ROOT / svc.CATALOG_DIR / svc.SCHEMA_FILE).read_text(encoding="utf-8")
    assert text == svc.render_catalog_schema()


def test_a_yaml_key_without_an_enum_member_is_refused() -> None:
    """Negative control for the closed enum: a planted extra entry cannot add a capability."""
    data = raw_catalog()
    planted = dict(entry_of(data, "angular"), key="svelte", aliases=["svelte"])
    data["entries"].append(planted)
    with pytest.raises(CatalogError, match="entries"):
        parse_catalog(yaml.safe_dump(data).encode())


def test_an_enum_member_without_a_yaml_entry_is_refused() -> None:
    data = raw_catalog()
    data["entries"] = [e for e in data["entries"] if e["key"] != "vue"]
    with pytest.raises(CatalogError, match="vue"):
        parse_catalog(yaml.safe_dump(data).encode())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["entries"].append(entry_of(d, "vue")), "duplicate catalog keys"),
        (lambda d: entry_of(d, "vue")["aliases"].append("angular"), "alias 'angular'"),
        (lambda d: entry_of(d, "vue").update(substitutions=["fastapi_python"]), "another category"),
        (lambda d: entry_of(d, "vue").update(substitutions=["vue"]), "cannot substitute itself"),
        (lambda d: entry_of(d, "sqlite")["aliases"].append("sql"), "ambiguous database wording"),
        (lambda d: entry_of(d, "react_vite").update(required_credentials=["key"]), "needs credentials"),
        (lambda d: entry_of(d, "react_vite").update(network_requirement="credentialed"), "needs credentials"),
        (lambda d: entry_of(d, "react_vite").update(build_recipe=None), "build recipe"),
        (lambda d: entry_of(d, "vue").update(templates=["react_fastapi_sqlite"]), "only supported entries"),
        (lambda d: entry_of(d, "vue").update(status="maybe"), "status"),
        (lambda d: entry_of(d, "vue").update(colour="green"), "colour"),
        (lambda d: d.update(version=2), "version"),
    ],
)
def test_an_inconsistent_catalog_is_refused(mutate: Any, message: str) -> None:
    data = raw_catalog()
    mutate(data)
    with pytest.raises(CatalogError, match=message):
        parse_catalog(yaml.safe_dump(data).encode())


def test_unknown_kit_recipe_or_document_references_are_refused() -> None:
    catalog = load_catalog().catalog
    entries = list(catalog.entries)
    i = next(n for n, e in enumerate(entries) if e.key is K.REACT_VITE)
    entries[i] = entries[i].model_copy(
        update={"templates": ("angular_kit",), "test_recipes": ("rm_rf",), "documentation_ref": "nope.md"}
    )
    broken = CapabilityCatalog.model_validate(
        catalog.model_copy(update={"entries": tuple(entries)}).model_dump()
    )
    problems = svc.reference_problems(broken, ROOT)
    assert problems == [
        "react_vite: unknown starter kit 'angular_kit'",
        "react_vite: unknown recipe 'rm_rf'",
        "react_vite: documentation 'nope.md' does not exist",
    ]
    assert svc.reference_problems(catalog, ROOT) == []  # control: the shipped catalog is clean


def _copy_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    shutil.copytree(ROOT / "capability_catalog", root / "capability_catalog")
    shutil.copytree(KITS, root / "templates" / "target_apps")
    return root


def test_a_tampered_file_stops_loading(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    assert load_catalog(root).digest == load_catalog().digest  # control: an untouched copy loads
    target = root / "capability_catalog" / "capabilities.yaml"
    target.write_text(target.read_text(encoding="utf-8").replace("key: vue", "key: vue2"), encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(root)


def test_a_stale_schema_file_or_missing_catalog_stops_loading(tmp_path: Path) -> None:
    root = _copy_root(tmp_path)
    schema = root / "capability_catalog" / "schemas" / "capabilities.schema.json"
    schema.write_text(schema.read_text(encoding="utf-8").replace('"version"', '"versio"'), encoding="utf-8")
    with pytest.raises(CatalogError, match="does not match"):
        load_catalog(root)
    schema.unlink()
    with pytest.raises(CatalogError, match="missing"):
        load_catalog(root)
    (root / "capability_catalog" / "capabilities.yaml").unlink()
    with pytest.raises(CatalogError, match="missing"):
        load_catalog(root)


def test_invalid_yaml_is_refused() -> None:
    with pytest.raises(CatalogError, match="not valid YAML"):
        parse_catalog(b"entries: [unclosed")


# ------------------------------------------------------------------------------------ effective status

EXPECTED_WHEN_BOTH_KITS_PASS = {
    K.HTML_CSS_JS: E.AVAILABLE,
    K.REACT_VITE: E.AVAILABLE,
    K.FASTAPI_PYTHON: E.AVAILABLE,
    K.SQLITE: E.AVAILABLE,
    K.NO_AUTH: E.AVAILABLE,
    K.PUBLIC_REST_NO_CREDENTIALS: E.AVAILABLE_LIMITED,
    K.POSTGRESQL_DOCKER_LOCAL: E.DEMOTED,
    K.GITHUB_PUBLIC_REPO: E.DEMOTED,
    K.GITHUB_ACTIONS_CI: E.DEMOTED,
    K.GITHUB_PAGES: E.DEMOTED,
    K.USER_PROVIDED_POSTGRESQL: E.LATER_CHANGE_REQUEST,
    K.SUPABASE_HOSTED: E.LATER_CHANGE_REQUEST,
}


@pytest.mark.parametrize("key", list(CatalogKey))
def test_every_entry_has_the_expected_effective_status(
    key: CatalogKey, sessions: sessionmaker[Session]
) -> None:
    store(sessions, "html_fastapi_sqlite", V.PASSED)
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    item = snapshot(sessions).entries[key]
    assert item.status is EXPECTED_WHEN_BOTH_KITS_PASS.get(key, E.UNSUPPORTED)
    if item.status is E.UNSUPPORTED:
        assert item.entry.status is CatalogStatus.UNSUPPORTED and item.reason
    if item.status in {E.AVAILABLE, E.AVAILABLE_LIMITED}:
        assert item.verification_ids  # availability always names the verification that proves it


def test_later_part_assets_are_demoted_with_the_part_that_delivers_them(
    sessions: sessionmaker[Session],
) -> None:
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    entries = snapshot(sessions).entries
    assert entries[K.POSTGRESQL_DOCKER_LOCAL].reason == "not available yet: arrives in Part K"
    assert entries[K.GITHUB_PAGES].reason == "not available yet: arrives in Part P"


def test_nothing_is_available_before_any_verification(sessions: sessionmaker[Session]) -> None:
    entries = snapshot(sessions).entries
    for key in (K.HTML_CSS_JS, K.REACT_VITE, K.FASTAPI_PYTHON, K.SQLITE, K.NO_AUTH):
        assert entries[key].status is E.DEMOTED
        assert "has never been verified" in (entries[key].reason or "")


def test_the_backend_is_available_when_either_kit_is_verified(sessions: sessionmaker[Session]) -> None:
    store(sessions, "html_fastapi_sqlite", V.PASSED)
    entries = snapshot(sessions).entries
    assert entries[K.FASTAPI_PYTHON].status is E.AVAILABLE
    assert entries[K.HTML_CSS_JS].status is E.AVAILABLE
    assert entries[K.REACT_VITE].status is E.DEMOTED  # its own kit was not verified


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (V.FAILED, "latest verification of starter kit react_fastapi_sqlite failed"),
        (V.BLOCKED, "latest verification of starter kit react_fastapi_sqlite blocked"),
        (V.STALE, "is stale"),
    ],
)
def test_a_failed_blocked_or_stale_verification_demotes(
    status: V, reason: str, sessions: sessionmaker[Session]
) -> None:
    store(sessions, "react_fastapi_sqlite", status)
    item = snapshot(sessions).entries[K.REACT_VITE]
    assert item.status is E.DEMOTED and reason in (item.reason or "")


def test_the_newest_finished_result_wins(sessions: sessionmaker[Session]) -> None:
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    store(sessions, "react_fastapi_sqlite", V.FAILED)
    assert snapshot(sessions).entries[K.REACT_VITE].status is E.DEMOTED
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    assert snapshot(sessions).entries[K.REACT_VITE].status is E.AVAILABLE


def test_a_changed_kit_or_image_since_the_pass_demotes(sessions: sessionmaker[Session]) -> None:
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    changed_kit = snapshot(sessions, fingerprint=lambda _t: ("d" * 64, dict(DIGESTS))).entries[K.REACT_VITE]
    assert changed_kit.status is E.DEMOTED and "stale" in (changed_kit.reason or "")
    other = {**DIGESTS, "node_web": "sha256:" + "e" * 64}
    changed_image = snapshot(sessions, fingerprint=lambda _t: (CHECKSUM, other)).entries[K.REACT_VITE]
    assert changed_image.status is E.DEMOTED


def test_a_fingerprint_that_cannot_be_computed_never_fails_open(sessions: sessionmaker[Session]) -> None:
    store(sessions, "react_fastapi_sqlite", V.PASSED)

    def no_lock(_template: str) -> tuple[str, dict[str, str]]:
        raise FileNotFoundError("image lock")

    item = snapshot(sessions, fingerprint=no_lock).entries[K.REACT_VITE]
    assert item.status is E.DEMOTED and "cannot be confirmed as current" in (item.reason or "")


def test_a_missing_kit_or_invalid_manifest_demotes(tmp_path: Path, sessions: sessionmaker[Session]) -> None:
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    empty = tmp_path / "no_kits"
    empty.mkdir()
    item = snapshot(sessions, kits=empty).entries[K.REACT_VITE]
    assert item.status is E.DEMOTED and "is not installed" in (item.reason or "")
    copied = tmp_path / "kits"
    shutil.copytree(KITS, copied)
    manifest = copied / "react_fastapi_sqlite" / "template.yaml"
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text(original.replace("backend: fastapi", "backend: django"), encoding="utf-8")
    item = snapshot(sessions, kits=copied).entries[K.REACT_VITE]
    assert item.status is E.DEMOTED and "invalid manifest" in (item.reason or "")
    manifest.write_bytes(original.encode("utf-8") + bytes([10, 35, 32, 0xA7, 10]))  # not UTF-8: never a crash
    item = snapshot(sessions, kits=copied).entries[K.REACT_VITE]
    assert item.status is E.DEMOTED and "invalid manifest" in (item.reason or "")


def test_an_unregistered_recipe_demotes(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    loaded = load_catalog()  # valid at start-up; the recipe registry changes afterwards
    monkeypatch.delitem(RECIPES, "vitest_run")
    with pytest.raises(CatalogError, match="unknown recipe 'vitest_run'"):
        load_catalog()  # the next start-up refuses the catalog (fail closed)
    item = snapshot(sessions, loaded=loaded).entries[K.REACT_VITE]
    assert item.status is E.DEMOTED and item.reason == "unregistered recipes: vitest_run"
    item = snapshot(sessions, loaded=loaded).entries[
        K.SQLITE
    ]  # sqlite's own recipes exist, but the kit's self-test does not
    assert item.status is E.DEMOTED and "unregistered recipes: vitest_run" in (item.reason or "")


# --------------------------------------------------------------------------------------- demotion audit


def _catalog_events(sessions: sessionmaker[Session]) -> list[AuditEvent]:
    with sessions() as session:
        return [
            e for e in session.query(AuditEvent).order_by(AuditEvent.seq) if e.action.startswith("catalog.")
        ]


def test_demotions_are_audited_once_per_change_and_restorations_too(sessions: sessionmaker[Session]) -> None:
    def record() -> int:
        current = snapshot(sessions)
        with sessions.begin() as session:
            return svc.record_status_changes(session, current)

    assert record() == 10  # every declared-supported entry is demoted before any verification
    assert record() == 0  # a second start-up with no change writes nothing
    store(sessions, "react_fastapi_sqlite", V.PASSED)
    assert record() == 5  # react, fastapi, sqlite, public REST and no-auth are restored
    events = _catalog_events(sessions)
    assert [e.action for e in events].count(svc.DEMOTED_ACTION) == 10
    restored = {e.target_id for e in events if e.action == svc.RESTORED_ACTION}
    assert restored == {"react_vite", "fastapi_python", "sqlite", "public_rest_no_credentials", "no_auth"}
    assert all(e.principal_id == "service:feasibility_gate" for e in events)
    store(sessions, "react_fastapi_sqlite", V.FAILED)
    assert record() == 5  # demoted again, with the new reason


def test_a_limitation_with_a_comma_stays_one_sentence() -> None:
    """Regression (G-G demo): an unquoted YAML flow item split "a proprietary, licensed …" in two."""
    entries = {e.key: e for e in load_catalog().catalog.entries}
    assert entries[CatalogKey.POWER_APPS_CANVAS].known_limitations == (
        "a proprietary, licensed low-code platform; it cannot be generated or tested locally",
    )
    assert entries[CatalogKey.ANGULAR].known_limitations == (
        "no Angular starter kit, generator or verification recipe",
    )
    raw = yaml.safe_load((svc.code_root() / "capability_catalog" / "capabilities.yaml").read_text("utf-8"))
    fragments = [
        (e["key"], item)
        for e in raw["entries"]
        for item in e.get("known_limitations", [])
        if item[:1].islower() and len(e["known_limitations"]) > 1
    ]
    assert fragments == []  # every limitation is one complete statement

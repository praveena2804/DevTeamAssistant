"""E.4 starter kits without Docker: manifests, consistency with the image locks, materialisation,
protected files, ``.env`` rendering, verification requests (Part E plan §3.6; D-04, D-16, D-49).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from devteam_core.contracts.sandbox import TemplateManifest, TemplateVerificationStatus
from devteam_harness.sandbox.recipes import RECIPES
from devteam_services.integration_builder import (
    TemplateError,
    in_slots,
    load_manifest,
    materialise,
    protected_changes,
    template_checksum,
    template_files,
    template_ids,
)
from devteam_services.templates import VerificationRefused, request_verification
from devteam_state import create_sqlite_engine, session_factory, upgrade
from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.integration

IMAGES = REPO_ROOT / "sandbox" / "images"
NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)
KITS = ("html_fastapi_sqlite", "react_fastapi_sqlite")


def test_exactly_the_two_mvp_kits_exist() -> None:
    assert tuple(template_ids()) == KITS


@pytest.mark.parametrize("kit", KITS)
def test_manifest_is_valid_and_consistent(kit: str) -> None:
    manifest = load_manifest(kit)
    assert manifest.auth == "none" and manifest.database == "sqlite"  # D-04, D-16
    assert all(recipe in RECIPES for recipe in manifest.self_test)
    files = template_files(kit)
    assert all(path in files for path in manifest.protected_files)
    assert not any(path.startswith(("node_modules/", "dist/")) for path in files)
    assert "{{APP_TITLE}}" not in b"".join(files.values()).decode("utf-8", "replace")


@pytest.mark.parametrize("kit", KITS)
def test_dependencies_equal_the_locked_image_versions(kit: str) -> None:
    manifest = load_manifest(kit)
    python_lock = {
        m.group(1): m.group(2)
        for m in re.finditer(
            r"^([a-z0-9._-]+)==(\S+)", (IMAGES / "python_api" / "requirements.lock").read_text(), re.M
        )
    }
    for pin in manifest.dependencies.python:
        name, version = pin.split("==")
        assert python_lock.get(name.lower()) == version, pin
    node = json.loads((IMAGES / "node_web" / "package.json").read_text(encoding="utf-8"))
    node_lock = {**node["dependencies"], **node["devDependencies"]}
    for pin in manifest.dependencies.node:
        name, version = pin.rsplit("==", 1)
        assert node_lock.get(name) == version, pin


def test_react_recipes_use_the_node_image_and_html_has_no_node_recipe() -> None:
    react, html = load_manifest("react_fastapi_sqlite"), load_manifest("html_fastapi_sqlite")
    assert {RECIPES[r].image for r in react.self_test} == {"python_api", "node_web"}
    assert {RECIPES[r].image for r in html.self_test} == {"python_api"}


def test_checksum_changes_with_content() -> None:
    files = template_files("html_fastapi_sqlite")
    changed = {**files, "frontend/app.js": files["frontend/app.js"] + b"\n// edit"}
    assert template_checksum(files) == template_checksum(dict(files))
    assert template_checksum(files) != template_checksum(changed)


def test_materialise_renders_env_records_protected_files_and_detects_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "prj"
    result = materialise(workspace, "html_fastapi_sqlite", app_title="Library", now=NOW)
    generated = workspace / "generated"
    env = (generated / ".env").read_text(encoding="utf-8")
    assert "APP_NAME=Library" in env and "DATABASE_URL=sqlite:///./data/app.db" in env
    assert "Library" in (generated / "frontend" / "index.html").read_text(encoding="utf-8")
    assert (generated / "README.md").is_file() and not (generated / "README.md.tmpl").exists()
    assert protected_changes(workspace) == []
    (generated / "backend" / "app" / "main.py").write_text("tampered", encoding="utf-8")
    (generated / "pytest.ini").unlink()
    assert sorted(protected_changes(workspace)) == ["backend/app/main.py", "pytest.ini"]
    assert result.files == len(template_files("html_fastapi_sqlite")) + 1  # + rendered .env
    with pytest.raises(TemplateError, match="not empty"):
        materialise(workspace, "html_fastapi_sqlite", app_title="Again", now=NOW)


def test_slots() -> None:
    manifest = load_manifest("react_fastapi_sqlite")
    assert in_slots("backend/app/api/books.py", manifest.writable_slots)
    assert not in_slots("backend/app/main.py", manifest.writable_slots)
    assert in_slots("tests/backend/test_books.py", manifest.tester_slots)


def _manifest_with(tmp_path: Path, **changes: object) -> Path:
    root = tmp_path / "kits"
    for name in ("_common", "html_fastapi_sqlite"):
        src = REPO_ROOT / "templates" / "target_apps" / name
        for path in src.rglob("*"):
            if path.is_file():
                target = root / name / path.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
    data = yaml.safe_load((root / "html_fastapi_sqlite" / "template.yaml").read_text(encoding="utf-8"))
    data.update(changes)
    (root / "html_fastapi_sqlite" / "template.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "changes",
    [
        {"env_placeholders": {"API_TOKEN": "abc"}},
        {"env_placeholders": {"DATABASE_URL": "postgresql://user:pw@host/db"}},
        {"database": "postgresql"},
        {"auth": "local_password_rbac"},
        {"protected_files": ["does/not/exist.py"]},
        {"dependencies": {"python": ["fastapi>=0.1"]}},
    ],
)
def test_unsafe_or_out_of_scope_manifests_are_refused(tmp_path: Path, changes: dict[str, object]) -> None:
    root = _manifest_with(tmp_path, **changes)
    with pytest.raises((TemplateError, ValueError)):
        template_files("html_fastapi_sqlite", root=root)


def test_manifest_contract_rejects_unknown_keys() -> None:
    data = yaml.safe_load(
        (REPO_ROOT / "templates" / "target_apps" / "html_fastapi_sqlite" / "template.yaml").read_text()
    )
    with pytest.raises(ValueError):
        TemplateManifest.model_validate({**data, "shell": "rm -rf /"})


def test_verification_request_queues_once(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    upgrade(db)
    sessions = session_factory(create_sqlite_engine(db))
    queued = request_verification(sessions, "react_fastapi_sqlite", requested_by="prn_test")
    assert queued.status is TemplateVerificationStatus.QUEUED
    with pytest.raises(VerificationRefused):
        request_verification(sessions, "react_fastapi_sqlite", requested_by="prn_test")
    with pytest.raises(VerificationRefused):
        request_verification(sessions, "unknown_kit", requested_by="prn_test")


def test_a_root_without_starter_kits_has_none(tmp_path: Path) -> None:
    assert template_ids(tmp_path / "missing") == []

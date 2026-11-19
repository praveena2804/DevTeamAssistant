"""Shared fixtures for cross-package suites."""

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from devteam_api.main import create_app
from devteam_core.config import Settings, load_settings
from devteam_state import create_sqlite_engine, session_factory
from devteam_state.models import AuditEvent

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_ORIGIN = "http://127.0.0.1:5173"


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Settings:
    """Settings for a throw-away repository root: the real config files, data under ``tmp_path``."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    return load_settings(root=tmp_path, profile="test", environ={})


@pytest.fixture
def make_client(isolated_settings: Settings) -> Iterator[Callable[..., TestClient]]:
    """Start an app (optionally with settings overrides); returns a TestClient with ``.token`` set."""
    clients: list[TestClient] = []

    def factory(**section_updates: dict[str, Any]) -> TestClient:
        settings = isolated_settings
        for section, values in section_updates.items():
            current = getattr(settings, section)
            settings = settings.model_copy(update={section: current.model_copy(update=values)})
        client = TestClient(create_app(settings, configure_logs=False))
        client.__enter__()
        client.token = client.app.state.api_token  # type: ignore[attr-defined]
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


def bearer(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {client.token}"}  # type: ignore[attr-defined,unused-ignore]


def audit_rows(settings: Settings) -> list[AuditEvent]:
    engine = create_sqlite_engine(settings.state_db_path)
    try:
        with session_factory(engine)() as session:
            return list(session.scalars(select(AuditEvent).order_by(AuditEvent.ts, AuditEvent.id)))
    finally:
        engine.dispose()


def create_project(client: TestClient, name: str = "Demo") -> str:
    response = client.post("/api/projects", json={"name": name}, headers=bearer(client))
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def upload(client: TestClient, project_id: str, *files: tuple[str, bytes]) -> dict[str, Any]:
    response = client.post(
        f"/api/projects/{project_id}/documents",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=bearer(client),
    )
    assert response.status_code == 200, response.text
    return dict(response.json())

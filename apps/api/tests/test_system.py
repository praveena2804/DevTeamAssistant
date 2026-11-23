import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devteam_api.main import create_app
from devteam_core.config import Settings, load_settings
from devteam_core.tracing import format_traceparent, new_root, parse_traceparent

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    return load_settings(root=tmp_path, profile="test", environ={})


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, configure_logs=False)) as test_client:
        yield test_client


def test_health_ok_on_fresh_database(client: TestClient, settings: Settings) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"state_db": {"status": "ok", "detail": None}}}
    assert settings.state_db_path.is_file()


def test_version_reports_platform_and_schema(client: TestClient) -> None:
    body = client.get("/version").json()
    assert body["name"] == "DevTeam AI"
    assert body["api_version"] == "v1"
    assert body["profile"] == "test"
    assert body["schema_revision"] == body["schema_head"] == "0004_execution"
    assert re.fullmatch(r"3\.12\.\d+", body["python_version"])


def test_health_degraded_when_schema_is_behind(settings: Settings) -> None:
    from devteam_state import downgrade  # noqa: PLC0415

    with TestClient(create_app(settings, configure_logs=False)) as test_client:
        downgrade(settings.state_db_path, "base")
        response = test_client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert "expected" in response.json()["checks"]["state_db"]["detail"]


def test_request_id_is_generated_or_propagated(client: TestClient) -> None:
    generated = client.get("/health").headers["x-request-id"]
    assert generated.startswith("req_")
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"


@pytest.mark.parametrize("bad", ["x" * 65, "has space", "<script>", "a;b", "é-unicode"])
def test_unsafe_request_ids_are_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": bad.encode("utf-8")})
    assert response.headers["x-request-id"].startswith("req_")


def test_traceparent_is_continued(client: TestClient) -> None:
    incoming = new_root()
    response = client.get("/health", headers={"traceparent": format_traceparent(incoming)})
    returned = parse_traceparent(response.headers["traceparent"])
    assert returned is not None
    assert returned.trace_id == incoming.trace_id
    assert returned.span_id != incoming.span_id


def test_new_trace_when_no_traceparent(client: TestClient) -> None:
    assert parse_traceparent(client.get("/version").headers["traceparent"]) is not None


def test_only_health_and_version_live_outside_the_authenticated_api(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {p for p in paths if not p.startswith("/api/")} == {"/health", "/version"}

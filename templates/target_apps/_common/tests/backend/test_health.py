"""Starter-kit API smoke tests: health, readiness and the standard error format."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.api
@pytest.mark.smoke
def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


@pytest.mark.api
def test_ready_checks_the_database(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "reachable"


@pytest.mark.api
def test_unknown_route_uses_the_standard_error_format(client: TestClient) -> None:
    response = client.get("/does-not-exist", headers={"x-request-id": "req-123"})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "http_404"
    assert error["request_id"] == "req-123"
    assert set(error) == {"code", "message", "request_id"}

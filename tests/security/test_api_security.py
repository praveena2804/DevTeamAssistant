"""API security suite for Part B (AC-B-03, AC-B-04): authentication, origin checks, route inventory,
rate limits, audit, isolation and secret hygiene."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from devteam_api.deps import audited, authenticate
from devteam_api.routers import audit as audit_router
from devteam_api.routers import documents, projects, runs, session
from devteam_core.config import Settings
from tests.conftest import UI_ORIGIN, audit_rows, bearer, create_project, upload
from tests.fixtures import brds

pytestmark = pytest.mark.security
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
SIGN_IN = ("POST", "/api/session")
FAKE_PROJECT = "prj_01JD3X5Q0000000000000000AA"
FAKE_DOCUMENT = "doc_01JD3X5Q0000000000000000AA"
FAKE_RUN = "run_01JD3X5Q0000000000000000AA"


def _calls(dependant: Any) -> set[Callable[..., Any]]:
    found = set()
    stack = list(dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            found.add(dep.call)
        stack.extend(dep.dependencies)
    return found


def _api_routes(client: TestClient) -> list[tuple[str, str, APIRoute]]:
    """All /api routes, taken from the router modules and cross-checked against the live OpenAPI document,
    so a route registered anywhere else cannot escape this inventory."""
    routes: list[tuple[str, str, APIRoute]] = []
    for module in (session, projects, documents, audit_router, runs):
        for route in module.router.routes:
            assert isinstance(route, APIRoute)
            routes.extend((method, route.path, route) for method in sorted(route.methods or ()))
    live = {
        (method.upper(), path)
        for path, item in client.get("/openapi.json").json()["paths"].items()
        if path.startswith("/api")
        for method in item
    }
    assert {(m, p) for m, p, _ in routes} == live
    return routes


def _concrete(path: str) -> str:
    return (
        path.replace("{project_id}", FAKE_PROJECT)
        .replace("{document_id}", FAKE_DOCUMENT)
        .replace("{run_id}", FAKE_RUN)
        .replace("{gate}", "project_approval")
    )


# ------------------------------------------------------------------ route inventory (AC-B-04)
def test_every_api_route_requires_authentication_and_every_mutation_is_audited(client: TestClient) -> None:
    routes = _api_routes(client)
    assert len(routes) >= 10
    for method, path, route in routes:
        calls = _calls(route.dependant)
        if (method, path) == SIGN_IN:
            continue  # signs in; audits itself (tested below)
        assert authenticate in calls, f"{method} {path} is not authenticated"
        if method in UNSAFE:
            assert audited in calls, f"{method} {path} is not audited"


def test_known_mutating_routes(client: TestClient) -> None:
    """A new mutating route must be added here deliberately (and therefore reviewed)."""
    mutating = {(m, p) for m, p, _ in _api_routes(client) if m in UNSAFE}
    assert mutating == {
        ("POST", "/api/session"),
        ("DELETE", "/api/session"),
        ("POST", "/api/projects"),
        ("PATCH", "/api/projects/{project_id}"),
        ("POST", "/api/projects/{project_id}/documents"),
        # Part D (LG-08): run control and gate decisions
        ("POST", "/api/projects/{project_id}/runs"),
        ("POST", "/api/projects/{project_id}/runs/{run_id}/pause"),
        ("POST", "/api/projects/{project_id}/runs/{run_id}/resume"),
        ("POST", "/api/projects/{project_id}/runs/{run_id}/cancel"),
        ("POST", "/api/projects/{project_id}/runs/{run_id}/gates/{gate}/decision"),
    }


@pytest.mark.parametrize("credentials", ["none", "wrong-bearer", "wrong-cookie", "token-as-cookie"])
def test_every_route_rejects_missing_or_wrong_credentials(
    client: TestClient, isolated_settings: Settings, credentials: str
) -> None:
    token: str = client.token  # type: ignore[attr-defined,unused-ignore]
    headers = {"Origin": UI_ORIGIN}
    if credentials == "wrong-bearer":
        headers["Authorization"] = "Bearer " + "x" * 43
    cookie_name = isolated_settings.auth.cookie_name
    if credentials == "wrong-cookie":
        client.cookies.set(cookie_name, "y" * 64)
    if credentials == "token-as-cookie":
        client.cookies.set(cookie_name, token)  # the raw token is not a valid session value
    for method, path, _ in _api_routes(client):
        if (method, path) == SIGN_IN:
            continue
        before = len(audit_rows(isolated_settings))
        response = client.request(method, _concrete(path), headers=headers, json={})
        assert response.status_code == 401, f"{method} {path}: {response.status_code}"
        assert response.json()["detail"]["code"] == "auth.required"
        rows = audit_rows(isolated_settings)
        assert len(rows) == before + 1 and rows[-1].action == "auth.denied" and rows[-1].outcome == "denied"
    client.cookies.clear()


def test_bearer_token_works_without_origin(client: TestClient) -> None:
    assert client.get("/api/projects", headers=bearer(client)).status_code == 200
    assert client.post("/api/projects", json={"name": "Script"}, headers=bearer(client)).status_code == 201


def test_disallowed_origin_is_refused_even_with_a_valid_token(
    client: TestClient, isolated_settings: Settings
) -> None:
    response = client.post(
        "/api/projects", json={"name": "Evil"}, headers={**bearer(client), "Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth.origin_denied"
    assert audit_rows(isolated_settings)[-1].details["reason"] == "origin_not_allowed"
    assert client.get("/api/projects", headers=bearer(client)).json() == []  # nothing was created


# ------------------------------------------------------------------ browser session (D-29 (a))
def test_browser_session_flow(client: TestClient, isolated_settings: Settings) -> None:
    token: str = client.token  # type: ignore[attr-defined,unused-ignore]
    signed_in = client.post("/api/session", json={"token": token}, headers={"Origin": UI_ORIGIN})
    assert signed_in.status_code == 200
    assert set(signed_in.json()["roles"]) == {"project_user", "project_admin", "reviewer"}
    set_cookie = signed_in.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie and "Path=/api" in set_cookie
    assert token not in set_cookie  # the cookie is derived from the token, never the token itself

    assert client.get("/api/session").status_code == 200
    no_origin = client.post("/api/projects", json={"name": "A"})
    assert no_origin.status_code == 403 and no_origin.json()["detail"]["code"] == "auth.origin_denied"
    assert client.post("/api/projects", json={"name": "A"}, headers={"Origin": UI_ORIGIN}).status_code == 201

    signed_out = client.delete("/api/session", headers={"Origin": UI_ORIGIN})
    assert signed_out.status_code == 204
    assert (
        'devteam_session=""' in signed_out.headers["set-cookie"]
        or "Max-Age=0" in signed_out.headers["set-cookie"]
    )
    actions = [row.action for row in audit_rows(isolated_settings)]
    assert actions.count("session.create") == 1 and "session.delete" in actions


def test_wrong_sign_in_key_is_refused_and_audited(client: TestClient, isolated_settings: Settings) -> None:
    response = client.post("/api/session", json={"token": "not-the-key"}, headers={"Origin": UI_ORIGIN})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    row = audit_rows(isolated_settings)[-1]
    assert (row.action, row.outcome, row.details["reason"]) == ("auth.denied", "denied", "invalid_token")
    assert "not-the-key" not in str(row.details)


def test_sign_in_from_another_website_is_refused(client: TestClient) -> None:
    token: str = client.token  # type: ignore[attr-defined,unused-ignore]
    response = client.post("/api/session", json={"token": token}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and "set-cookie" not in response.headers


def test_cors_preflight_only_allows_the_ui_origin(client: TestClient) -> None:
    def preflight(origin: str) -> Any:
        return client.options(
            "/api/projects", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
        )

    assert preflight(UI_ORIGIN).headers.get("access-control-allow-origin") == UI_ORIGIN
    assert "access-control-allow-origin" not in preflight("https://evil.example").headers


def test_secrets_never_reach_the_database(client: TestClient, isolated_settings: Settings) -> None:
    token: str = client.token  # type: ignore[attr-defined,unused-ignore]
    client.post("/api/session", json={"token": token}, headers={"Origin": UI_ORIGIN})
    canary = "canary-WRONG-0123456789"  # gitleaks:allow (synthetic, not a secret)
    client.post("/api/session", json={"token": canary}, headers={"Origin": UI_ORIGIN})
    cookie_value: str = client.app.state.session_cookie_value  # type: ignore[attr-defined,unused-ignore]
    stored = b"".join(p.read_bytes() for p in isolated_settings.data_dir.glob("state.db*"))
    assert token.encode() not in stored
    assert cookie_value.encode() not in stored
    assert b"canary-WRONG" not in stored


def test_audit_details_redact_registered_secrets(client: TestClient, isolated_settings: Settings) -> None:
    token: str = client.token  # type: ignore[attr-defined,unused-ignore]
    client.post("/api/projects", json={"name": f"x {token}"}, headers=bearer(client))  # pasted by mistake
    row = audit_rows(isolated_settings)[-1]
    assert row.action == "project.create"
    assert token not in str(row.details) and "[REDACTED]" in row.details["name"]


# ------------------------------------------------------------------ rate limits (HC-18)
def test_mutations_are_rate_limited_and_the_refusal_is_audited(
    make_client: Callable[..., TestClient], isolated_settings: Settings
) -> None:
    client = make_client(ratelimit={"mutation_per_minute": 3})
    codes = [
        client.post("/api/projects", json={"name": f"P{i}"}, headers=bearer(client)).status_code
        for i in range(4)
    ]
    assert codes == [201, 201, 201, 429]
    limited = client.post("/api/projects", json={"name": "P9"}, headers=bearer(client))
    assert int(limited.headers["retry-after"]) >= 1
    assert audit_rows(isolated_settings)[-1].action == "ratelimit.exceeded"
    assert client.get("/api/projects", headers=bearer(client)).status_code == 200  # reads are not limited


def test_uploads_have_their_own_tighter_limit(make_client: Callable[..., TestClient]) -> None:
    client = make_client(ratelimit={"upload_per_minute": 1})
    project = create_project(client)
    upload(client, project, ("a.txt", b"hello"))
    second = client.post(
        f"/api/projects/{project}/documents", files=[("files", ("b.txt", b"x"))], headers=bearer(client)
    )
    assert second.status_code == 429


def test_sign_in_attempts_are_rate_limited(make_client: Callable[..., TestClient]) -> None:
    client = make_client(ratelimit={"session_per_minute": 2})
    codes = [client.post("/api/session", json={"token": "wrong"}).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


# ------------------------------------------------------------------ audit of failures
def test_invalid_and_conflicting_requests_are_audited_as_errors(
    client: TestClient, isolated_settings: Settings
) -> None:
    assert client.post("/api/projects", json={"name": ""}, headers=bearer(client)).status_code == 422
    row = audit_rows(isolated_settings)[-1]
    assert (row.action, row.outcome, row.details["code"]) == ("project.create", "error", "request.invalid")
    create_project(client, "Same")
    assert client.post("/api/projects", json={"name": " same "}, headers=bearer(client)).status_code == 409
    row = audit_rows(isolated_settings)[-1]
    assert (row.outcome, row.details["code"]) == ("error", "project.name_taken")


def test_unknown_fields_are_rejected(client: TestClient) -> None:
    response = client.post("/api/projects", json={"name": "A", "owner": "someone"}, headers=bearer(client))
    assert response.status_code == 422


# ------------------------------------------------------------------ isolation (AC-B-03)
def test_projects_cannot_reach_each_others_documents(client: TestClient) -> None:
    project_a = create_project(client, "A")
    project_b = create_project(client, "B")
    doc_a = upload(client, project_a, ("a.md", brds.brd_markdown()))["results"][0]["document_id"]
    own = client.get(f"/api/projects/{project_a}/documents/{doc_a}/chunks", headers=bearer(client))
    assert own.status_code == 200 and own.json()["total"] > 0
    for suffix in ("", "/chunks"):
        cross = client.get(f"/api/projects/{project_b}/documents/{doc_a}{suffix}", headers=bearer(client))
        assert cross.status_code == 404 and cross.json()["detail"]["code"] == "document.not_found"
    assert client.get(f"/api/projects/{project_b}/documents", headers=bearer(client)).json() == []
    audit_b = client.get(f"/api/projects/{project_b}/audit", headers=bearer(client)).json()["items"]
    assert all(item["project_id"] == project_b for item in audit_b)


@pytest.mark.parametrize(
    "path",
    [
        "/api/projects/..%2F..%2Fetc",
        "/api/projects/prj_short",
        "/api/projects/doc_01JD3X5Q0000000000000000AA",
        f"/api/projects/{FAKE_PROJECT}",
        f"/api/projects/{FAKE_PROJECT}/documents/..%5C..%5Cwin.ini",
    ],
)
def test_malformed_or_unknown_ids_reveal_nothing(client: TestClient, path: str) -> None:
    response = client.get(path, headers=bearer(client))
    assert response.status_code == 404


def test_hostile_filenames_never_touch_the_filesystem(
    client: TestClient, isolated_settings: Settings
) -> None:
    project = create_project(client)
    hostile = ["../../escape.txt", "..\\..\\escape.txt", "C:\\escape.txt", "CON.txt", "brd.txt:stream"]
    result = upload(client, project, *[(name, f"t{i}".encode()) for i, name in enumerate(hostile)])
    codes = {item["filename"]: item["code"] for item in result["results"]}
    assert (
        codes["CON.txt"] == codes["brd.txt:stream"] == codes["../../escape.txt"] == "upload.unsafe_filename"
    )
    for item in result["results"]:
        # Either refused, or the folder part was already stripped by the multipart parser (a plain name).
        assert item["code"] == "upload.unsafe_filename" or (
            item["status"] == "extracted" and "/" not in item["filename"] and "\\" not in item["filename"]
        ), item
    assert not list(isolated_settings.root.rglob("escape.txt"))  # no file ever lands under a client name
    stored = [p.name for p in (isolated_settings.workspaces_dir / project / "uploads").iterdir()]
    assert all(name.startswith("doc_") for name in stored)


# ------------------------------------------------------------------ request size (HC-20)
def test_oversized_upload_requests_are_refused_before_reading(make_client: Callable[..., TestClient]) -> None:
    client = make_client(uploads={"max_file_mb": 1, "max_files_per_upload": 1})
    project = create_project(client)
    response = client.post(
        f"/api/projects/{project}/documents",
        files=[("files", ("big.txt", b"x" * (2 * 1024 * 1024 + 10)))],
        headers=bearer(client),
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "upload.request_too_large"


def test_single_file_over_the_limit_is_rejected_per_file(make_client: Callable[..., TestClient]) -> None:
    client = make_client(uploads={"max_file_mb": 1})
    project = create_project(client)
    result = upload(client, project, ("big.txt", b"x" * (1024 * 1024 + 1)), ("ok.txt", b"fine"))
    assert [r["code"] for r in result["results"]] == ["upload.too_large", None]


def test_too_many_files_in_one_request(client: TestClient, isolated_settings: Settings) -> None:
    project = create_project(client)
    response = client.post(
        f"/api/projects/{project}/documents",
        files=[("files", (f"f{i}.txt", f"t{i}".encode())) for i in range(11)],
        headers=bearer(client),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "upload.too_many_files"
    assert audit_rows(isolated_settings)[-1].outcome == "error"


@pytest.mark.anyio
async def test_upload_without_content_length_is_refused() -> None:
    from devteam_api.middleware.body_limit import UploadBodyLimitMiddleware  # noqa: PLC0415

    sent: list[dict[str, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover - must not be reached
        raise AssertionError("request reached the application")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = UploadBodyLimitMiddleware(app, max_bytes=100)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/projects/{FAKE_PROJECT}/documents",
        "headers": [],
    }
    await middleware(scope, None, send)  # type: ignore[arg-type]
    assert sent[0]["status"] == 411

"""Projects, upload, extraction detail and chunk paging through the API (AC-B-01, AC-B-02, AC-B-04)."""

import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings
from tests.conftest import audit_rows, bearer, create_project, upload
from tests.fixtures import brds

pytestmark = pytest.mark.integration


def test_project_lifecycle(client: TestClient, isolated_settings: Settings) -> None:
    project_id = create_project(client, "  Library   Demo ")
    listed = client.get("/api/projects", headers=bearer(client)).json()
    assert [(p["id"], p["name"], p["document_count"]) for p in listed] == [(project_id, "Library Demo", 0)]
    assert (isolated_settings.workspaces_dir / project_id / "uploads").is_dir()

    renamed = client.patch(
        f"/api/projects/{project_id}",
        json={"name": "Library", "description": "Pilot"},
        headers=bearer(client),
    )
    assert renamed.status_code == 200 and renamed.json()["description"] == "Pilot"
    create_project(client, "Other")
    clash = client.patch(f"/api/projects/{project_id}", json={"name": "OTHER"}, headers=bearer(client))
    assert clash.status_code == 409
    assert client.get(f"/api/projects/{project_id}", headers=bearer(client)).json()["name"] == "Library"
    assert client.delete(f"/api/projects/{project_id}", headers=bearer(client)).status_code == 405  # DV-006

    actions = [
        (r.action, r.outcome) for r in audit_rows(isolated_settings) if r.action != "audit.chain_anchor"
    ]
    assert actions == [
        ("project.create", "success"),
        ("project.update", "success"),
        ("project.create", "success"),
        ("project.update", "error"),
    ]


def test_upload_all_formats_and_review_extraction(client: TestClient) -> None:
    project_id = create_project(client)
    result = upload(
        client,
        project_id,
        ("brd.docx", brds.brd_docx()),
        ("brd.pdf", brds.brd_pdf()),
        ("brd.md", brds.brd_markdown()),
        ("brd.txt", brds.brd_text("cp1252")),
    )
    assert (result["extracted"], result["rejected"]) == (4, 0)
    assert [r["role"] for r in result["results"]] == ["primary_brd", "supporting", "supporting", "supporting"]
    txt = result["results"][3]
    assert any(w["code"] == "encoding" for w in txt["warnings"])

    documents = client.get(f"/api/projects/{project_id}/documents", headers=bearer(client)).json()
    assert [d["original_filename"] for d in documents] == ["brd.docx", "brd.pdf", "brd.md", "brd.txt"]
    pdf = documents[1]
    assert pdf["page_count"] == 3 and pdf["media_type"] == "application/pdf" and len(pdf["sha256"]) == 64

    detail = client.get(f"/api/projects/{project_id}/documents/{pdf['id']}", headers=bearer(client)).json()
    assert [o["page"] for o in detail["outline"] if o["title"] == "4 Business Rules"] == [3]

    first_page = client.get(
        f"/api/projects/{project_id}/documents/{pdf['id']}/chunks",
        params={"limit": 2},
        headers=bearer(client),
    ).json()
    total = first_page["total"]
    assert len(first_page["items"]) == 2 and total > 2
    rest = client.get(
        f"/api/projects/{project_id}/documents/{pdf['id']}/chunks",
        params={"offset": 2, "limit": 200},
        headers=bearer(client),
    ).json()
    ordinals = [c["ordinal"] for c in first_page["items"] + rest["items"]]
    assert ordinals == list(range(total))
    assert client.get(f"/api/projects/{project_id}", headers=bearer(client)).json()["document_count"] == 4


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_chunk_paging_bounds(client: TestClient, params: dict[str, int]) -> None:
    project_id = create_project(client)
    doc = upload(client, project_id, ("a.md", b"# A\n\ntext"))["results"][0]["document_id"]
    response = client.get(
        f"/api/projects/{project_id}/documents/{doc}/chunks", params=params, headers=bearer(client)
    )
    assert response.status_code == 422


def test_each_file_and_the_batch_are_audited(client: TestClient, isolated_settings: Settings) -> None:
    project_id = create_project(client)
    upload(client, project_id, ("good.md", b"# Good\n\ntext"), ("bad.exe", b"MZ"))
    rows = [r for r in audit_rows(isolated_settings) if r.action.startswith("document.")]
    assert [(r.action, r.outcome, r.details.get("filename")) for r in rows] == [
        ("document.upload", "success", "good.md"),
        ("document.upload", "error", "bad.exe"),
        ("document.upload_batch", "success", None),
    ]
    assert rows[-1].details == {"files": 2, "extracted": 1, "rejected": 1}
    assert all(r.request_id == rows[0].request_id for r in rows)  # one request, one correlation ID

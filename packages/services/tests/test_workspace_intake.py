"""Workspace isolation and the intake pipeline (AC-B-02, AC-B-03)."""

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from devteam_core.config import UploadSettings
from devteam_harness.iam import LOCAL_HUMAN_ROLES
from devteam_harness.validation import UploadRejected
from devteam_services.intake import IncomingFile, IntakeService
from devteam_services.workspace import SUBDIRECTORIES, WorkspaceError, WorkspaceManager
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.models import DocumentChunk, SourceDocument
from devteam_state.repositories import principals, projects
from tests.fixtures import brds

PROJECT_ID = "prj_01JD3X5Q0000000000000000AA"
DOC_ID = "doc_01JD3X5Q0000000000000000AA"


# ------------------------------------------------------------------ workspace
@pytest.mark.parametrize(
    "bad", ["../x", "prj_bad", "doc_01JD3X5Q0000000000000000AA", "..", "", "prj_01JD/../.."]
)
def test_workspace_refuses_anything_but_project_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(WorkspaceError):
        WorkspaceManager(tmp_path).project_dir(bad)


def test_workspace_layout(tmp_path: Path) -> None:
    base = WorkspaceManager(tmp_path / "ws").create(PROJECT_ID)
    assert sorted(p.name for p in base.iterdir()) == sorted(SUBDIRECTORIES)


def test_upload_streaming_enforces_the_size_cap_and_leaves_nothing(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path)
    with pytest.raises(UploadRejected) as info:
        manager.receive_upload(PROJECT_ID, DOC_ID, ".txt", io.BytesIO(b"x" * 3000), max_bytes=2048)
    assert info.value.code == "upload.too_large"
    assert list((tmp_path / PROJECT_ID / "uploads").iterdir()) == []


def test_empty_upload(tmp_path: Path) -> None:
    with pytest.raises(UploadRejected) as info:
        WorkspaceManager(tmp_path).receive_upload(PROJECT_ID, DOC_ID, ".txt", io.BytesIO(b""), max_bytes=10)
    assert info.value.code == "upload.empty"


def test_stored_name_comes_from_the_document_id(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path)
    stored = manager.receive_upload(PROJECT_ID, DOC_ID, ".txt", io.BytesIO(b"hello"), max_bytes=100)
    final = manager.finalise(stored)
    assert final == (tmp_path / PROJECT_ID / "uploads" / f"{DOC_ID}.txt").resolve()
    assert stored.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


# ------------------------------------------------------------------ intake
@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    path = tmp_path / "state.db"
    upgrade(path)
    engine = create_sqlite_engine(path)
    session = session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def setup(db: Session, tmp_path: Path) -> tuple[IntakeService, str, str]:
    owner = principals.ensure_local_human(db, display_name="Owner", roles=LOCAL_HUMAN_ROLES)
    project = projects.create(db, name="Demo", description="", created_by=owner.id)
    db.commit()
    service = IntakeService(UploadSettings(), WorkspaceManager(tmp_path / "workspaces"))
    return service, project.id, owner.id


def _files(*items: tuple[str | None, bytes]) -> list[IncomingFile]:
    return [IncomingFile(filename=name, stream=io.BytesIO(data)) for name, data in items]


def test_mixed_batch_good_files_continue_bad_files_are_explained(
    db: Session, setup: tuple[IntakeService, str, str], tmp_path: Path
) -> None:
    service, project_id, owner = setup
    outcomes = service.process(
        db,
        project_id=project_id,
        principal_id=owner,
        files=_files(
            ("sample_brd.docx", brds.brd_docx()),
            ("corrupt.docx", brds.corrupt_docx()),
            ("locked.pdf", brds.encrypted_pdf(user_password="secret")),
            ("invoice.pdf", brds.disguised_executable()),
            ("sample_brd.pdf", brds.brd_pdf()),
            ("../../evil.txt", b"text"),
            ("scanned.pdf", brds.image_only_pdf()),
            ("protected.docx", brds.ole_docx()),
            ("empty.txt", b""),
            ("bomb.docx", brds.zip_bomb_docx()),
        ),
    )
    db.commit()
    summary = [(o.filename, o.status, o.code) for o in outcomes]
    assert summary == [
        ("sample_brd.docx", "extracted", None),
        ("corrupt.docx", "rejected", "upload.corrupt"),
        ("locked.pdf", "rejected", "upload.encrypted"),
        ("invoice.pdf", "rejected", "upload.type_mismatch"),
        ("sample_brd.pdf", "extracted", None),
        ("../../evil.txt", "rejected", "upload.unsafe_filename"),
        ("scanned.pdf", "rejected", "upload.no_text"),
        ("protected.docx", "rejected", "upload.encrypted"),
        ("empty.txt", "rejected", "upload.empty"),
        ("bomb.docx", "rejected", "upload.archive_too_large"),
    ]
    assert all(o.message for o in outcomes if o.status == "rejected")
    assert [o.role for o in outcomes if o.status == "extracted"] == ["primary_brd", "supporting"]
    docs = db.scalars(select(SourceDocument)).all()
    assert len(docs) == 2
    uploads = tmp_path / "workspaces" / project_id / "uploads"
    assert sorted(p.name for p in uploads.iterdir()) == sorted(
        d.stored_name for d in docs
    )  # nothing else kept
    extracted = tmp_path / "workspaces" / project_id / "extracted"
    assert sorted(p.stem for p in extracted.iterdir()) == sorted(d.id for d in docs)
    assert db.scalar(select(DocumentChunk.id).where(DocumentChunk.project_id == project_id)) is not None


def test_duplicate_upload_is_detected(db: Session, setup: tuple[IntakeService, str, str]) -> None:
    service, project_id, owner = setup
    data = brds.brd_markdown()
    outcomes = service.process(
        db, project_id=project_id, principal_id=owner, files=_files(("a.md", data), ("copy.md", data))
    )
    assert [o.code for o in outcomes] == [None, "upload.duplicate"]
    assert "'a.md'" in (outcomes[1].message or "")


def test_too_many_files_is_refused_before_any_work(
    db: Session, setup: tuple[IntakeService, str, str]
) -> None:
    service, project_id, owner = setup
    files = _files(*[(f"f{i}.txt", f"text {i}".encode()) for i in range(11)])
    with pytest.raises(UploadRejected) as info:
        service.process(db, project_id=project_id, principal_id=owner, files=files)
    assert info.value.code == "upload.too_many_files"
    assert db.scalars(select(SourceDocument)).all() == []


def test_unexpected_errors_reject_only_that_file(
    db: Session, setup: tuple[IntakeService, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project_id, owner = setup
    import devteam_services.intake as intake_module  # noqa: PLC0415
    from devteam_services.extractor import extract as real  # noqa: PLC0415

    def flaky(path: Path, ext: str, **kwargs: int) -> object:
        if ext == ".md":
            raise RuntimeError("boom")
        return real(path, ext, **kwargs)

    monkeypatch.setattr(intake_module, "extract", flaky)
    outcomes = service.process(
        db,
        project_id=project_id,
        principal_id=owner,
        files=_files(("a.md", b"# A\n\nx"), ("b.txt", b"hello world")),
    )
    assert [(o.status, o.code) for o in outcomes] == [
        ("rejected", "upload.internal_error"),
        ("extracted", None),
    ]

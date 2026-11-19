"""Project workspace manager (NFR-004 data isolation).

Layout: ``workspaces/<project_id>/{uploads,extracted,generated,runs,package}``.

* Directory names come only from validated platform IDs (``prj_…``, ``doc_…``), never from user input.
* Every resolved path is checked to stay inside its project directory.
* Uploads are streamed to a temporary ``.part`` file with a hard size cap and a running SHA-256, then
  either finalised (atomic rename) or discarded.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from devteam_core.ids import is_valid_id
from devteam_harness.validation import UploadRejected

SUBDIRECTORIES = ("uploads", "extracted", "generated", "runs", "package")
_CHUNK = 1024 * 1024


class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class StoredUpload:
    project_id: str
    document_id: str
    ext: str
    temp_path: Path
    final_path: Path
    size: int
    sha256: str


class WorkspaceManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def project_dir(self, project_id: str) -> Path:
        if not is_valid_id(project_id, "prj"):
            raise WorkspaceError("invalid project id")
        path = (self.root / project_id).resolve()
        if path.parent != self.root:
            raise WorkspaceError("project path escapes the workspaces root")
        return path

    def create(self, project_id: str) -> Path:
        base = self.project_dir(project_id)
        for name in SUBDIRECTORIES:
            (base / name).mkdir(parents=True, exist_ok=True)
        return base

    def _inside(self, project_id: str, *parts: str) -> Path:
        base = self.project_dir(project_id)
        path = base.joinpath(*parts).resolve()
        if not path.is_relative_to(base):
            raise WorkspaceError("path escapes the project workspace")
        return path

    def receive_upload(
        self, project_id: str, document_id: str, ext: str, source: BinaryIO, *, max_bytes: int
    ) -> StoredUpload:
        """Stream ``source`` into the project's uploads folder, enforcing ``max_bytes``."""
        if not is_valid_id(document_id, "doc"):
            raise WorkspaceError("invalid document id")
        self.create(project_id)
        temp = self._inside(project_id, "uploads", f".{document_id}.part")
        final = self._inside(project_id, "uploads", f"{document_id}{ext}")
        digest = hashlib.sha256()
        size = 0
        try:
            with temp.open("xb") as out:
                while block := source.read(_CHUNK):
                    size += len(block)
                    if size > max_bytes:
                        raise UploadRejected(
                            "upload.too_large",
                            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.",
                        )
                    digest.update(block)
                    out.write(block)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        if size == 0:
            temp.unlink(missing_ok=True)
            raise UploadRejected("upload.empty", "The file is empty.")
        return StoredUpload(project_id, document_id, ext, temp, final, size, digest.hexdigest())

    @staticmethod
    def finalise(upload: StoredUpload) -> Path:
        upload.temp_path.replace(upload.final_path)
        return upload.final_path

    @staticmethod
    def discard(upload: StoredUpload) -> None:
        upload.temp_path.unlink(missing_ok=True)
        upload.final_path.unlink(missing_ok=True)

    def write_extraction(self, project_id: str, document_id: str, payload: dict[str, Any]) -> Path:
        if not is_valid_id(document_id, "doc"):
            raise WorkspaceError("invalid document id")
        path = self._inside(project_id, "extracted", f"{document_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return path


__all__ = ["SUBDIRECTORIES", "StoredUpload", "WorkspaceError", "WorkspaceManager"]

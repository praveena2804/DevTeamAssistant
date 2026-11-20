"""Controlled File Service (HC-06, HS-10; Spec §11.3): the only way agent tools touch workspace files.

The policy engine (C.2) decides whether a *path string* is allowed. This service makes sure the path
*on disk* is what the string says, inside the project workspace:

- structural rules first (``check_relative_path``): no ``..``, absolute, drive, UNC, ``:`` streams,
  backslashes, ``%`` encoding, device names, trailing dots/spaces;
- 8.3 short-name aliases (``GENERA~1``) are refused, so a path cannot name a folder in a different way
  from the one the policy checked;
- every existing component is inspected with ``lstat``: a junction, symlink or any other reparse point
  anywhere on the way is refused (Windows junctions need no privilege to create, so this matters);
- the resolved path must equal the requested one (case-insensitively, as NTFS is) and stay inside the
  workspace root, which itself must not be a link;
- hard-linked files (link count > 1) are neither read nor overwritten, because the other name may live
  outside the workspace;
- reads and writes have byte limits (F-9i); text must be UTF-8;
- writes are atomic: a new temporary file (exclusive create) is written, flushed, re-checked and moved
  into place with ``os.replace``; a cancelled write (tool timeout) leaves no trace;
- every write reports the SHA-256 before and after and a unified diff, for the audit trail.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import os
import re
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from devteam_core.contracts._base import check_relative_path
from devteam_core.errors import DevTeamError

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SHORT_NAME = re.compile(r"~\d")
_TEMP_PREFIX = ".devteam-"
_TEMP_SUFFIX = ".tmp"
MAX_DIFF_CHARS = 200_000


class FileSafetyError(DevTeamError):
    """The path is unsafe (escape, link, alias, hard link). Treated as a policy denial."""

    code = "file.unsafe"


class FileLimitError(DevTeamError):
    """The operation is safe but not possible (missing, too large, not text, cancelled)."""

    code = "file.limit"


@dataclass(frozen=True, slots=True)
class FileLimits:
    max_write_bytes: int
    max_read_bytes: int
    max_list_entries: int


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    path: str
    before_sha256: str | None
    after_sha256: str
    bytes_written: int
    diff: str


@dataclass(frozen=True, slots=True)
class DirEntry:
    path: str  # workspace-relative; directories end with "/"
    is_dir: bool
    size: int


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ControlledFileService:
    def __init__(self, root: Path, limits: FileLimits) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if _is_link(os.lstat(root)):
            raise FileSafetyError("the workspace root must not be a link or junction")
        resolved = root.resolve(strict=True)
        if not _same(resolved, root.absolute()):
            raise FileSafetyError("the workspace root resolves elsewhere (link in its path)")
        self._root = resolved
        self._limits = limits

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------------------------ resolution

    def _resolve(self, rel: str, *, allow_directory: bool) -> Path:
        try:
            check_relative_path(rel, allow_directory=allow_directory)
        except ValueError as exc:
            raise FileSafetyError(f"unsafe path: {exc}") from None
        parts = rel.rstrip("/").split("/")
        if any(_SHORT_NAME.search(part) for part in parts):
            raise FileSafetyError("8.3 short-name aliases (e.g. NAME~1) are not accepted")
        current = self._root
        for part in parts:
            current = current / part
            try:
                st = os.lstat(current)
            except FileNotFoundError:
                break  # nothing further exists, so nothing further can redirect
            if _is_link(st):
                raise FileSafetyError("the path passes through a link (junction, symlink or reparse point)")
        target = self._root.joinpath(*parts)
        resolved = target.resolve(strict=False)
        if not _same(resolved, target) or not os.path.normcase(str(resolved)).startswith(
            os.path.normcase(str(self._root)) + os.sep
        ):
            raise FileSafetyError("the path resolves outside the requested location")
        return target

    # ------------------------------------------------------------------------------------- reading

    def read_text(self, rel: str) -> str:
        path = self._resolve(rel, allow_directory=False)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise FileLimitError(f"'{rel}' does not exist") from None
        if not stat.S_ISREG(st.st_mode):
            raise FileLimitError(f"'{rel}' is not a file")
        if st.st_nlink > 1:
            raise FileSafetyError("refusing to read a hard link (it may point outside the workspace)")
        if st.st_size > self._limits.max_read_bytes:
            raise FileLimitError(f"'{rel}' exceeds the read limit of {self._limits.max_read_bytes} bytes")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(fd)
            if (opened.st_ino, opened.st_dev) != (st.st_ino, st.st_dev):
                raise FileSafetyError("the file changed while it was being opened")
            data = os.read(fd, self._limits.max_read_bytes + 1)
        finally:
            os.close(fd)
        if len(data) > self._limits.max_read_bytes:
            raise FileLimitError(f"'{rel}' exceeds the read limit of {self._limits.max_read_bytes} bytes")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise FileLimitError(f"'{rel}' is not valid UTF-8 text") from None

    def list_dir(self, rel: str) -> list[DirEntry]:
        path = self._resolve(rel, allow_directory=True)
        if not path.is_dir():
            raise FileLimitError(f"'{rel}' is not a directory")
        base = rel.rstrip("/")
        entries: list[DirEntry] = []
        with os.scandir(path) as scan:
            for item in sorted(scan, key=lambda e: e.name.casefold()):
                st = item.stat(follow_symlinks=False)
                if _is_link(st) or (item.name.startswith(_TEMP_PREFIX) and item.name.endswith(_TEMP_SUFFIX)):
                    continue  # links are never followed or shown; in-flight temp files are internal
                is_dir = stat.S_ISDIR(st.st_mode)
                entries.append(DirEntry(f"{base}/{item.name}{'/' if is_dir else ''}", is_dir, st.st_size))
                if len(entries) >= self._limits.max_list_entries:
                    break
        return entries

    # ------------------------------------------------------------------------------------- writing

    def write_text(
        self, rel: str, content: str, *, cancelled: Callable[[], bool] = lambda: False
    ) -> WriteOutcome:
        data = content.encode("utf-8")
        if len(data) > self._limits.max_write_bytes:
            raise FileLimitError(
                f"the content exceeds the write limit of {self._limits.max_write_bytes} bytes"
            )
        path = self._resolve(rel, allow_directory=False)
        before = self._existing(path, rel)
        self._make_parents(path)

        temp = path.parent / f"{_TEMP_PREFIX}{secrets.token_hex(8)}{_TEMP_SUFFIX}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if cancelled():
                raise FileLimitError("write cancelled (tool time limit reached)")
            self._existing(path, rel)  # re-check just before the move (narrows TOCTOU)
            self._resolve(rel, allow_directory=False)
            temp.replace(path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return WriteOutcome(
            path=rel,
            before_sha256=_sha(before) if before is not None else None,
            after_sha256=_sha(data),
            bytes_written=len(data),
            diff=self._diff(rel, before, data),
        )

    def _existing(self, path: Path, rel: str) -> bytes | None:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        if _is_link(st):
            raise FileSafetyError("the target is a link (junction, symlink or reparse point)")
        if stat.S_ISDIR(st.st_mode):
            raise FileLimitError(f"'{rel}' is a directory")
        if st.st_nlink > 1:
            raise FileSafetyError("refusing to overwrite a hard link (it may point outside the workspace)")
        return path.read_bytes() if st.st_size <= self._limits.max_read_bytes else b""

    def _make_parents(self, path: Path) -> None:
        current = self._root
        for part in path.relative_to(self._root).parts[:-1]:
            current = current / part
            with contextlib.suppress(FileExistsError):
                current.mkdir()
            st = os.lstat(current)
            if _is_link(st) or not stat.S_ISDIR(st.st_mode):
                raise FileSafetyError("a parent folder is a link or not a folder")

    @staticmethod
    def _diff(rel: str, before: bytes | None, after: bytes) -> str:
        try:
            old = (before or b"").decode("utf-8").splitlines(keepends=True)
            new = after.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            return "(binary change)"
        text = "".join(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        return text if len(text) <= MAX_DIFF_CHARS else text[:MAX_DIFF_CHARS] + "\n(diff truncated)\n"

"""C.3 Controlled File Service (HC-06, HS-10): Windows-aware path safety against the REAL filesystem.

Written before the implementation (test-first). The policy engine (C.2) checks path *strings*; this
service checks what the path *is* on disk: junctions, symlinks, hard links, 8.3 short names, device
names, streams, case and limits. Symlink and short-name tests skip only when Windows does not allow
the setup (no symlink privilege / 8.3 names disabled), and say so.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from devteam_harness.tools.files import (
    ControlledFileService,
    FileLimitError,
    FileLimits,
    FileSafetyError,
)

pytestmark = pytest.mark.security

LIMITS = FileLimits(max_write_bytes=64 * 1024, max_read_bytes=64 * 1024, max_list_entries=50)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspaces" / "prj_x"
    for area in ("generated/backend", "uploads", "extracted"):
        (workspace / area).mkdir(parents=True)
    (workspace / "generated" / "backend" / "app.py").write_bytes(b"print('hi')\n")  # LF, like the service
    (workspace / "uploads" / "brd.pdf").write_bytes(b"%PDF-1.7 original")
    (tmp_path / "outside.txt").write_text("outside secret", encoding="utf-8")
    return workspace


@pytest.fixture
def files(root: Path) -> ControlledFileService:
    return ControlledFileService(root, LIMITS)


# ------------------------------------------------------------------------------------------- basics


def test_read_write_list_round_trip(files: ControlledFileService, root: Path) -> None:
    assert files.read_text("generated/backend/app.py") == "print('hi')\n"
    outcome = files.write_text("generated/backend/new/orders.py", "x = 1\n")
    assert (root / "generated/backend/new/orders.py").read_text(encoding="utf-8") == "x = 1\n"
    assert outcome.before_sha256 is None
    assert outcome.after_sha256 == hashlib.sha256(b"x = 1\n").hexdigest()
    assert outcome.bytes_written == 6
    names = {entry.path for entry in files.list_dir("generated/backend")}
    assert names == {"generated/backend/app.py", "generated/backend/new/"}


def test_overwrite_records_before_checksum_and_diff(files: ControlledFileService) -> None:
    outcome = files.write_text("generated/backend/app.py", "print('bye')\n")
    assert outcome.before_sha256 == hashlib.sha256(b"print('hi')\n").hexdigest()
    assert "-print('hi')" in outcome.diff and "+print('bye')" in outcome.diff


def test_write_is_atomic_and_leaves_no_temp_files(files: ControlledFileService, root: Path) -> None:
    files.write_text("generated/backend/app.py", "v2\n")
    leftovers = [p.name for p in (root / "generated/backend").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_cancelled_write_changes_nothing(files: ControlledFileService, root: Path) -> None:
    with pytest.raises(FileLimitError, match="cancelled"):
        files.write_text("generated/backend/app.py", "v2\n", cancelled=lambda: True)
    assert (root / "generated/backend/app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert not [p for p in (root / "generated/backend").iterdir() if p.name.endswith(".tmp")]


# ------------------------------------------------------------------------------- string attacks


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "generated/../../outside.txt",
        "generated/backend/../../../outside.txt",
        "/outside.txt",
        "C:/Windows/win.ini",
        "C:\\Windows\\win.ini",
        "\\\\?\\C:\\Windows\\win.ini",
        "\\\\server\\share\\x",
        "generated/backend/app.py::$DATA",
        "generated/backend/app.py:hidden",
        "generated/backend/CON",
        "generated/backend/nul.txt",
        "generated/backend/app.py.",
        "generated/backend/app.py ",
        "%2e%2e/outside.txt",
        "generated/GENERA~1/x.py",
        "generated/backend/PROGRA~1",
        "",
    ],
)
def test_string_attacks_are_refused_for_every_operation(files: ControlledFileService, path: str) -> None:
    with pytest.raises(FileSafetyError):
        files.read_text(path)
    with pytest.raises(FileSafetyError):
        files.write_text(path, "pwned")
    with pytest.raises(FileSafetyError):
        files.list_dir(path)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)), min_size=1, max_size=40))
def test_fuzz_no_write_ever_lands_outside_the_workspace(
    files: ControlledFileService, root: Path, name: str
) -> None:
    before = set(root.parent.parent.rglob("*"))
    with contextlib.suppress(FileSafetyError, FileLimitError, OSError, ValueError):
        files.write_text(f"generated/{name}", "fuzz")
    created = set(root.parent.parent.rglob("*")) - before
    for path in created:
        assert path.resolve().is_relative_to(root.resolve()), path


# ------------------------------------------------------------------- real filesystem redirections


def _junction(link: Path, target: Path) -> None:
    import _winapi  # noqa: PLC0415  # Windows only; creating a junction needs no special privilege

    _winapi.CreateJunction(str(target), str(link))


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions exist only on Windows")
def test_junction_escape_is_refused(files: ControlledFileService, root: Path, tmp_path: Path) -> None:
    _junction(root / "generated" / "escape", tmp_path)
    assert (root / "generated/escape/outside.txt").exists()  # the redirection is real
    with pytest.raises(FileSafetyError, match="link"):
        files.read_text("generated/escape/outside.txt")
    with pytest.raises(FileSafetyError, match="link"):
        files.write_text("generated/escape/outside.txt", "pwned")
    with pytest.raises(FileSafetyError, match="link"):
        files.list_dir("generated/escape")
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "outside secret"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions exist only on Windows")
def test_junction_into_protected_area_is_refused(files: ControlledFileService, root: Path) -> None:
    _junction(root / "generated" / "docs", root / "uploads")
    with pytest.raises(FileSafetyError):
        files.write_text("generated/docs/brd.pdf", "overwritten")
    assert (root / "uploads/brd.pdf").read_bytes() == b"%PDF-1.7 original"


def test_junction_as_the_target_file_is_refused(
    files: ControlledFileService, root: Path, tmp_path: Path
) -> None:
    if sys.platform != "win32":
        pytest.skip("NTFS junctions exist only on Windows")
    _junction(root / "generated" / "backend" / "link", tmp_path)
    with pytest.raises(FileSafetyError):
        files.write_text("generated/backend/link", "x")


def test_symlink_escape_is_refused(files: ControlledFileService, root: Path, tmp_path: Path) -> None:
    try:
        (root / "generated" / "backend" / "sym.txt").symlink_to(tmp_path / "outside.txt")
    except OSError as exc:  # Windows without Developer Mode / SeCreateSymbolicLinkPrivilege
        pytest.skip(f"symlink creation not permitted on this machine: {exc.__class__.__name__}")
    with pytest.raises(FileSafetyError, match="link"):
        files.read_text("generated/backend/sym.txt")
    with pytest.raises(FileSafetyError, match="link"):
        files.write_text("generated/backend/sym.txt", "pwned")
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "outside secret"


def test_hard_link_to_an_outside_file_cannot_be_read(
    files: ControlledFileService, root: Path, tmp_path: Path
) -> None:
    os.link(tmp_path / "outside.txt", root / "generated" / "backend" / "hard.txt")
    with pytest.raises(FileSafetyError, match="hard link"):
        files.read_text("generated/backend/hard.txt")


def test_overwriting_a_hard_link_never_modifies_the_outside_file(
    files: ControlledFileService, root: Path, tmp_path: Path
) -> None:
    os.link(tmp_path / "outside.txt", root / "generated" / "backend" / "hard.txt")
    with pytest.raises(FileSafetyError, match="hard link"):
        files.write_text("generated/backend/hard.txt", "pwned")
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "outside secret"


def _short_name(path: Path) -> str | None:
    if sys.platform != "win32":
        return None
    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024):  # type: ignore[attr-defined]
        return None
    short = Path(buffer.value).name
    return short if short != path.name else None


def test_8dot3_short_name_alias_is_refused(files: ControlledFileService, root: Path) -> None:
    longdir = root / "generated" / "longdirectoryname"
    longdir.mkdir()
    (longdir / "f.txt").write_text("x", encoding="utf-8")
    short = _short_name(longdir)
    if short is None:
        pytest.skip("8.3 short names are disabled on this volume")
    with pytest.raises(FileSafetyError):
        files.read_text(f"generated/{short}/f.txt")


def test_case_variants_resolve_to_the_same_place_inside(files: ControlledFileService) -> None:
    files.write_text("Generated/Backend/case.py", "x\n")
    assert files.read_text("generated/backend/case.py") == "x\n"


def test_root_that_is_itself_a_link_is_refused(root: Path, tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("NTFS junctions exist only on Windows")
    alias = tmp_path / "alias"
    _junction(alias, root)
    with pytest.raises(FileSafetyError, match="workspace root"):
        ControlledFileService(alias, LIMITS)


# ------------------------------------------------------------------------------------------ limits


def test_write_size_limit(files: ControlledFileService) -> None:
    files.write_text("generated/backend/ok.txt", "x" * LIMITS.max_write_bytes)
    with pytest.raises(FileLimitError, match="exceeds"):
        files.write_text("generated/backend/big.txt", "x" * (LIMITS.max_write_bytes + 1))


def test_size_limit_counts_utf8_bytes_not_characters(files: ControlledFileService) -> None:
    with pytest.raises(FileLimitError):
        files.write_text("generated/backend/wide.txt", "€" * (LIMITS.max_write_bytes // 2))


def test_read_size_limit(files: ControlledFileService, root: Path) -> None:
    (root / "generated/backend/huge.txt").write_bytes(b"x" * (LIMITS.max_read_bytes + 1))
    with pytest.raises(FileLimitError):
        files.read_text("generated/backend/huge.txt")


def test_binary_files_are_not_returned_as_text(files: ControlledFileService, root: Path) -> None:
    (root / "generated/backend/blob.bin").write_bytes(b"\xff\xfe\x00\x00binary")
    with pytest.raises(FileLimitError, match="UTF-8"):
        files.read_text("generated/backend/blob.bin")


def test_listing_is_capped_and_does_not_follow_links(
    files: ControlledFileService, root: Path, tmp_path: Path
) -> None:
    for i in range(LIMITS.max_list_entries + 10):
        (root / f"generated/backend/f{i:03}.txt").write_text("x", encoding="utf-8")
    entries = files.list_dir("generated/backend")
    assert len(entries) == LIMITS.max_list_entries


def test_reading_a_directory_or_missing_file_fails_cleanly(files: ControlledFileService) -> None:
    with pytest.raises(FileLimitError, match="not a file"):
        files.read_text("generated/backend")
    with pytest.raises(FileLimitError, match="does not exist"):
        files.read_text("generated/backend/missing.py")


def test_writing_over_a_directory_is_refused(files: ControlledFileService) -> None:
    with pytest.raises(FileLimitError, match="directory"):
        files.write_text("generated/backend", "x")

"""The third-party licence inventory matches the installed packages (D.2; KI-023, CLAUDE.md §10)."""

from __future__ import annotations

import re
import subprocess
import tomllib
from importlib.metadata import PackageNotFoundError, distribution, distributions

import pytest

from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.contract

INVENTORY = REPO_ROOT / "docs" / "THIRD_PARTY_LICENSES.md"
ROW = re.compile(r"^\| (?P<name>[A-Za-z0-9_.\-]+) \| (?P<version>[^|]+?) \| (?P<licence>[^|]+?) \|$")
COPYLEFT_RULED = {"orjson", "certifi"}  # listed in §1 with their ruling status
DEV_ONLY_MPL = {"hypothesis", "pathspec"}


def _licence(name: str) -> str:
    meta = distribution(name).metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [
        c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    raw = (meta.get("License") or "").strip().splitlines()
    return " / ".join(classifiers) if classifiers else (raw[0][:60] if raw else "UNKNOWN")


def _all_licence_text(name: str) -> str:
    meta = distribution(name).metadata
    parts = [meta.get("License-Expression") or "", (meta.get("License") or "")[:300]]
    parts += [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
    return " ".join(parts).upper()


def _rows() -> list[dict[str, str]]:
    section = INVENTORY.read_text(encoding="utf-8").split("## 2. All runtime packages", 1)[1]
    rows = [m.groupdict() for line in section.splitlines() if (m := ROW.match(line))]
    return [r for r in rows if r["name"] != "Package"]  # the header row


def test_every_listed_runtime_package_matches_its_installed_version_and_licence() -> None:
    rows = _rows()
    assert len(rows) >= 70
    checked = 0
    for row in rows:
        if row["licence"].startswith("not installed on Windows"):
            with pytest.raises(PackageNotFoundError):
                distribution(row["name"])
            continue
        installed = distribution(row["name"])
        assert installed.version == row["version"], row
        assert _licence(row["name"]) == row["licence"], row
        checked += 1
    assert checked >= 70


def test_no_installed_package_is_gpl_family() -> None:
    """No AGPL, GPL or LGPL in the runtime or development environment (NFR-013, CLAUDE.md §10)."""
    offenders = [
        d.metadata["Name"] for d in distributions() if "GPL" in _all_licence_text(d.metadata["Name"])
    ]
    assert offenders == []


def test_every_mpl_package_is_listed_with_a_ruling_or_is_dev_only() -> None:
    mpl = {
        d.metadata["Name"].lower()
        for d in distributions()
        if "MPL" in _all_licence_text(d.metadata["Name"])
        or "MOZILLA" in _all_licence_text(d.metadata["Name"])
    }
    assert mpl <= COPYLEFT_RULED | DEV_ONLY_MPL, mpl - COPYLEFT_RULED - DEV_ONLY_MPL
    assert mpl >= COPYLEFT_RULED  # negative control: the scan does find the known MPL packages
    section_1 = INVENTORY.read_text(encoding="utf-8").split("## 2.", 1)[0]
    for name in COPYLEFT_RULED:
        assert f"| `{name}` |" in section_1, name


def test_orjson_is_the_ruled_version_with_its_notices_intact() -> None:
    """KI-023 conditions: exact version and licence expression; all three licence notices present."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    [locked] = [p["version"] for p in lock["package"] if p["name"] == "orjson"]
    installed = distribution("orjson")
    assert locked == installed.version == "3.13.0"
    assert installed.metadata["License-Expression"] == "MPL-2.0 AND (Apache-2.0 OR MIT)"
    files = {str(f).replace("\\", "/").rsplit("/", 1)[-1] for f in installed.files or ()}
    assert {"LICENSE-MPL-2.0", "LICENSE-APACHE", "LICENSE-MIT"} <= files
    section_1 = INVENTORY.read_text(encoding="utf-8").split("## 2.", 1)[0]
    assert "| `orjson` | 3.13.0 | `MPL-2.0 AND (Apache-2.0 OR MIT)` |" in section_1


def test_orjson_source_is_not_vendored_into_the_repository() -> None:
    """KI-023: orjson is used unmodified from PyPI; no copy or patch of it lives in this repository."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout.splitlines()
    assert len(listed) > 100  # negative control: the listing works
    assert [p for p in listed if "orjson" in p.lower()] == []


def test_certifi_is_the_ruled_version_with_its_notice_intact() -> None:
    """KI-026 conditions (owner ruling 2026-11-23): exact version and licence; notice present; unmodified."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    [locked] = [p["version"] for p in lock["package"] if p["name"] == "certifi"]
    installed = distribution("certifi")
    assert locked == installed.version == "2026.10.23"
    assert installed.metadata["License"] == "MPL-2.0"
    files = {str(f).replace("\\", "/").rsplit("/", 1)[-1] for f in installed.files or ()}
    assert "LICENSE" in files
    section_1 = INVENTORY.read_text(encoding="utf-8").split("## 2.", 1)[0]
    assert "| `certifi` | 2026.10.23 |" in section_1
    certifi_row = section_1.split("| `certifi`", 1)[1].splitlines()[0]
    assert "Accepted conditionally" in certifi_row

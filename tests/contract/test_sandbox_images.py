"""The committed sandbox image lock and licence report satisfy the licence policy (Part E plan §3.5, §12.3).

Runs without Docker on every test run, so a hand-edited lock or a policy change that would admit a
refused licence is caught even when no image is rebuilt.
"""

from __future__ import annotations

import json
import re

import pytest

from devteam_harness.sandbox.licences import LicencePolicy, PackageLicence, evaluate
from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.contract

IMAGES = REPO_ROOT / "sandbox" / "images"
POLICY = LicencePolicy.load(IMAGES / "licence_policy.yaml")
LOCK = json.loads((IMAGES / "images.lock.json").read_text(encoding="utf-8"))
REPORT = json.loads((IMAGES / "licence_report.json").read_text(encoding="utf-8"))


def test_lock_covers_both_images_with_ids_audits_and_tier_counts() -> None:
    assert LOCK["schema"] == 1
    assert set(LOCK["images"]) == {"python_api", "node_web"}
    for name, image in LOCK["images"].items():
        assert image["tag"] == f"devteam-sandbox/{name}:1"
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image["image_id"])
        assert image["audit"]["status"] == "clean", image["audit"]  # "not_run" or "vulnerable" never locks
        assert image["packages_by_tier"]["4"] >= 3


def test_every_reported_package_satisfies_the_policy() -> None:
    for name, rows in REPORT.items():
        packages = [
            PackageLicence(r["ecosystem"], r["name"], r["version"], r["licence"], r["tier"]) for r in rows
        ]
        assert evaluate(packages, POLICY) == [], name


def test_gpl_appears_only_in_the_operating_system_layer() -> None:
    for rows in REPORT.values():
        for row in rows:
            if "GPL" in row["licence"].upper() and row["tier"] != 1:
                pytest.fail(f"{row['name']}: {row['licence']} outside the OS layer")


def test_reviewed_tier3_exceptions_are_exactly_the_listed_ones() -> None:
    reviewed = {n for image in LOCK["images"].values() for n in image["reviewed_tier3"]}
    assert reviewed <= set(POLICY.tier3_reviewed)
    assert reviewed == {
        "certifi",
        "lightningcss",
        "lightningcss-linux-x64-gnu",
        "lightningcss-linux-x64-musl",
    }


def test_base_images_are_pinned_by_digest_and_install_scripts_are_off() -> None:
    for name in ("python_api", "node_web"):
        dockerfile = (IMAGES / name / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(r"^FROM [a-z0-9.:/_-]+@sha256:[0-9a-f]{64}$", dockerfile, re.M), name
        assert "USER 10001:10001" in dockerfile and 'ENTRYPOINT ["/opt/devteam/measure"]' in dockerfile
    assert "--require-hashes" in (IMAGES / "python_api" / "Dockerfile").read_text(encoding="utf-8")
    assert "--ignore-scripts" in (IMAGES / "node_web" / "Dockerfile").read_text(encoding="utf-8")


def test_python_lock_is_fully_hash_pinned() -> None:
    lines = (IMAGES / "python_api" / "requirements.lock").read_text(encoding="utf-8").splitlines()
    pins = [line for line in lines if re.match(r"^[a-z0-9]", line)]
    assert pins and all(re.match(r"^[a-z0-9._-]+==[^ ]+ \\$", line) for line in pins)
    assert sum("--hash=sha256:" in line for line in lines) >= len(pins)


def test_react_starter_kit_matches_the_node_image_exactly() -> None:
    image = json.loads((IMAGES / "node_web" / "package.json").read_text(encoding="utf-8"))
    kit = json.loads(
        (
            REPO_ROOT / "templates" / "target_apps" / "react_fastapi_sqlite" / "frontend" / "package.json"
        ).read_text(encoding="utf-8")
    )
    assert kit["dependencies"] == image["dependencies"]
    assert kit["devDependencies"] == image["devDependencies"]

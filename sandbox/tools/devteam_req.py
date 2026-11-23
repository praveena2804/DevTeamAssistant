"""pytest plugin ``devteam_req``: requirement markers for generated applications (Part E plan §3.6).

Usage in a generated test::

    @pytest.mark.req("FR-001", "BR-002")
    def test_borrow_limit(): ...

Each requirement ID is written into the JUnit XML report as a ``<property name="req" value="FR-001"/>`` of
the test case, so the platform's Test Runner (Part M) can map executed tests to requirements. A malformed
ID is a usage error and fails the test run at collection time; a marker cannot invent a pass.

Loaded with ``-p devteam_req``. Part of the DevTeam AI platform's sandbox image; not third-party code.
"""

from __future__ import annotations

import re

import pytest

REQUIREMENT_ID = re.compile(r"^(FR|NFR|BR)-[0-9]{3,4}$")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "req(*ids): requirement IDs (FR-xxx, NFR-xxx, BR-xxx) this test verifies"
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for marker in item.iter_markers("req"):
            if not marker.args:
                raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.req needs at least one requirement ID")
            for requirement in marker.args:
                if not isinstance(requirement, str) or not REQUIREMENT_ID.fullmatch(requirement):
                    raise pytest.UsageError(f"{item.nodeid}: invalid requirement ID {requirement!r}")
                if ("req", requirement) not in item.user_properties:
                    item.user_properties.append(("req", requirement))

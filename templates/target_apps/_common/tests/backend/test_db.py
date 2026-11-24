"""Starter-kit database tests: the migrations create a working schema."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AppInfo


@pytest.mark.db
def test_migrated_schema_stores_and_reads_rows(migrated_session: Session) -> None:
    migrated_session.add(AppInfo(key="starter", value="ok"))
    migrated_session.commit()
    stored = migrated_session.scalars(select(AppInfo).where(AppInfo.key == "starter")).one()
    assert stored.value == "ok"


@pytest.mark.db
def test_keys_are_unique(migrated_session: Session) -> None:
    migrated_session.add_all([AppInfo(key="dup", value="1"), AppInfo(key="dup", value="2")])
    with pytest.raises(IntegrityError):
        migrated_session.commit()

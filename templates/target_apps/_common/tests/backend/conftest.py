"""Shared fixtures for the backend tests of the generated application."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import load_settings
from app.db import make_engine
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def migrated_session() -> Iterator[Session]:
    """A session on a database migrated to the latest revision with the project's own migrations."""
    url = load_settings().database_url
    config = Config("backend/alembic.ini")
    config.set_main_option("script_location", "backend/alembic")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = make_engine(url)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        command.downgrade(config, "base")
        engine.dispose()

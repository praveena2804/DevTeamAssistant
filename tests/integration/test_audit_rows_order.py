"""KI-046: the shared ``audit_rows`` test helper returns events in append (chain) order, even in one tick."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from devteam_core.clock import FixedClock
from devteam_core.config import Settings
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.models import AuditEvent
from devteam_state.repositories import audit
from tests.conftest import audit_rows

pytestmark = pytest.mark.integration


def test_events_sharing_a_timestamp_come_back_in_append_order(isolated_settings: Settings) -> None:
    upgrade(isolated_settings.state_db_path)
    engine = create_sqlite_engine(isolated_settings.state_db_path)
    sessions = session_factory(engine)
    one_tick = FixedClock(datetime(2026, 11, 26, 12, 0, tzinfo=UTC))  # every event in the same clock tick
    try:
        for i in range(50):
            with sessions.begin() as session:
                audit.append(session, action=f"step.{i:02d}", outcome="success", clock=one_tick)
        with sessions() as session:
            query = select(AuditEvent).where(AuditEvent.action.like("step.%"))
            by_time = [e.action for e in session.scalars(query.order_by(AuditEvent.ts, AuditEvent.id))]
    finally:
        engine.dispose()

    appended = [f"step.{i:02d}" for i in range(50)]
    assert by_time != appended  # negative control: ordering by (ts, id) is random within a tick
    in_chain_order = [e.action for e in audit_rows(isolated_settings) if e.action.startswith("step.")]
    assert in_chain_order == appended

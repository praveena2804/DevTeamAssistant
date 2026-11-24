"""Resource Guard with the real local Ollama (Spec §7.2; marker ``llm``): a loaded model is unloaded before
a sandbox run may start. The model is loaded with an empty prompt (Ollama loads it without generating)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from devteam_core.config import Settings
from devteam_core.contracts.statuses import ModelSlot
from devteam_harness.budgets.resource_lease import ResourceLease
from devteam_harness.llm_gateway.providers.ollama import OllamaProvider
from devteam_harness.sandbox.guard import ResourceGuard
from devteam_state import create_sqlite_engine, session_factory, upgrade

pytestmark = pytest.mark.llm


def test_guard_unloads_the_loaded_model(live_settings: Settings, tmp_path: Path) -> None:
    model = live_settings.ollama.models.fallback  # the smallest configured model
    with httpx.Client(base_url=live_settings.ollama.base_url, trust_env=False, timeout=300) as client:
        client.post(
            "/api/generate", json={"model": model, "prompt": "", "keep_alive": "5m"}
        ).raise_for_status()
    provider = OllamaProvider(live_settings.ollama.base_url, {ModelSlot.INSTRUCT: model}, timeout_s=60)
    try:
        assert any(name.startswith(model.split(":")[0]) for name in provider.loaded_models())
        db = tmp_path / "state.db"
        upgrade(db)
        lease = ResourceLease(session_factory(create_sqlite_engine(db)), ttl_s=30, wait_s=5)
        guard = ResourceGuard(lease, min_free_ram_mb=256, models=provider, settle_s=0.5)
        with guard.acquire():
            assert provider.loaded_models() == ()
            assert lease.holder_now() == "sandbox"
    finally:
        provider.close()

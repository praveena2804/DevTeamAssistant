"""Resource Guard for sandbox runs (HC-05/HC-14; Spec §7.2; Part E plan §3.3 step 3; D-46).

Before a container starts:

1. take the cross-process resource lease as ``sandbox`` (an LLM call in any process holds the same lease,
   so the two never overlap); waiting too long → ``waiting for resources``;
2. ask Ollama to unload every loaded model and confirm nothing is loaded any more;
3. require at least ``sandbox.min_free_ram_mb`` of free memory;

otherwise the lease is released and ``ResourceUnavailableError`` is raised (the work waits; nothing runs).
Docker availability is checked by the engine connection (Blocked, never a fallback).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from devteam_harness.budgets.manager import free_ram_mb
from devteam_harness.budgets.resource_lease import LeaseHandle, ResourceLease
from devteam_harness.errors import ResourceUnavailableError


class ModelResidency(Protocol):
    def loaded_models(self) -> tuple[str, ...]: ...

    def unload(self, model: str) -> None: ...


class ResourceGuard:
    def __init__(
        self,
        lease: ResourceLease,
        *,
        min_free_ram_mb: int,
        models: ModelResidency | None = None,
        unload_models: bool = True,
        free_ram: Callable[[], int] = free_ram_mb,
        settle_s: float = 2.0,
        unload_timeout_s: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._lease = lease
        self._min_free = min_free_ram_mb
        self._models = models
        self._unload = unload_models
        self._free_ram = free_ram
        self._settle_s = settle_s
        self._unload_timeout_s = unload_timeout_s
        self._sleep = sleep

    def acquire(self, *, wait_s: float | None = None) -> LeaseHandle:
        handle = self._lease.acquire("sandbox", wait_s=wait_s)
        try:
            self._release_models()
            free = self._free_ram()
            if free < self._min_free:
                raise ResourceUnavailableError(
                    "resource.low_memory", f"{free} MB free, a sandbox run needs {self._min_free} MB"
                )
        except BaseException:
            handle.release()
            raise
        return handle

    def _release_models(self) -> None:
        if self._models is None:
            return
        loaded = self._models.loaded_models()
        if not loaded:
            return
        if not self._unload:
            raise ResourceUnavailableError("resource.model_loaded", "a model is still loaded")
        for model in loaded:
            self._models.unload(model)
        deadline = time.monotonic() + self._unload_timeout_s
        while self._models.loaded_models():
            if time.monotonic() > deadline:
                raise ResourceUnavailableError("resource.model_loaded", "Ollama did not release its model")
            self._sleep(0.5)
        self._sleep(self._settle_s)  # let the operating system reclaim the freed memory


__all__ = ["ModelResidency", "ResourceGuard"]

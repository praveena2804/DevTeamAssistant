"""Run one command in one disposable container (HC-14; Part E plan §3.3 steps 4-6; TS-04, TS-06).

Order, and what can stop it:

1. the image must be exactly the locked image (else **Blocked**);
2. create a fresh volume and container (not started); read the engine's record back and check every fixed
   control (:func:`locked_down_problems`); any difference → remove and **Blocked**;
3. copy the files in through the archive API (no host mount); size-capped, regular files only, owned by
   UID 10001;
4. start; a watchdog kills at the wall-clock limit; a cancel request kills at once; memory is sampled from
   the engine's stats stream (peak = usage minus reclaimable page cache);
5. collect exit code, OOM flag, capped stdout/stderr and the files under ``/work/.devteam/out``;
6. **always** remove the container and its volume (``finally``), whatever happened.

A container is never reused: every run gets a new container and a new volume.
"""

from __future__ import annotations

import io
import json
import posixpath
import tarfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.sandbox import ContainerLimits, SandboxExecutionStatus
from devteam_harness.sandbox.engine import Engine
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.images import ImageLock, verify_image
from devteam_harness.sandbox.spec import METRICS_FILE, OUT_DIR, WORK_DIR, ContainerSpec, locked_down_problems

SANDBOX_UID = 10001
_TRUNCATED = b"\n[... output truncated by the sandbox ...]\n"
MAX_COLLECTED_FILES = 200


class CancelSignal(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class RunRequest:
    image: str  # "python_api" | "node_web"
    argv: tuple[str, ...]
    workdir: str
    env: Mapping[str, str]
    files: Mapping[str, bytes]  # path relative to /work -> content
    limits: ContainerLimits
    execution_id: str
    labels: Mapping[str, str] = field(default_factory=dict)
    cancel: CancelSignal | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    status: SandboxExecutionStatus
    exit_code: int | None
    duration_ms: int
    peak_memory_mb: float | None
    stdout: bytes
    stderr: bytes
    outputs: dict[str, bytes]  # path relative to /work/.devteam/out -> content
    reason: str | None
    image_id: str
    engine_version: str
    started_at: datetime
    finished_at: datetime


def _safe_relative(path: str) -> str:
    if not path or path.startswith(("/", "\\")) or "\\" in path or ":" in path:
        raise SandboxBlockedError(
            "sandbox.bad_path", "files copied into the sandbox need relative POSIX paths"
        )
    normal = posixpath.normpath(path)
    if normal.startswith("..") or normal in {".", ""}:
        raise SandboxBlockedError("sandbox.bad_path", "a file path escapes /work")
    return normal


def build_tar(files: Mapping[str, bytes], max_bytes: int) -> bytes:
    """A tar of regular files and their parent folders only, all owned by the sandbox user."""
    total = sum(len(data) for data in files.values())
    if total > max_bytes:
        raise SandboxBlockedError(
            "sandbox.copy_in_too_large", f"{total} bytes exceed the {max_bytes}-byte cap"
        )
    buffer = io.BytesIO()
    folders: set[str] = set()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for raw_path in sorted(files):
            path = _safe_relative(raw_path)
            parent = posixpath.dirname(path)
            chain: list[str] = []
            while parent and parent not in folders:
                chain.append(parent)
                parent = posixpath.dirname(parent)
            for folder in reversed(chain):
                info = tarfile.TarInfo(folder)
                info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, SANDBOX_UID, SANDBOX_UID
                tar.addfile(info)
                folders.add(folder)
            data = files[raw_path]
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.uid, info.gid = len(data), 0o644, SANDBOX_UID, SANDBOX_UID
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _capped(chunks: Iterator[bytes], cap: int) -> bytes:
    head = bytearray()
    tail: deque[int] = deque(maxlen=cap // 2)
    truncated = False
    for chunk in chunks:
        for byte in chunk:
            if len(head) < cap // 2:
                head.append(byte)
            else:
                if len(tail) == tail.maxlen:
                    truncated = True
                tail.append(byte)
    return bytes(head) + (_TRUNCATED if truncated else b"") + bytes(tail)


def _read_outputs(chunks: Iterator[bytes] | None, max_bytes: int) -> dict[str, bytes]:
    """Regular files under the output folder only; links, devices and oversize content are dropped."""
    if chunks is None:
        return {}
    blob = b"".join(chunks)
    outputs: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tar:
        for member in tar:
            if not member.isreg() or len(outputs) >= MAX_COLLECTED_FILES:
                continue
            name = posixpath.normpath(member.name)
            if name.startswith(("/", "..")) or "\\" in name or ":" in name:
                continue  # absolute or escaping names are never collected
            parts = name.split("/", 1)
            if len(parts) != 2 or parts[0] != OUT_DIR.rsplit("/", 1)[1]:
                continue  # only entries inside the output folder ("out/...")
            total += member.size
            if total > max_bytes:
                break
            handle = tar.extractfile(member)
            if handle is not None:
                outputs[parts[1]] = handle.read()
    return outputs


def _read_metrics(chunks: Iterator[bytes] | None) -> dict[str, int]:
    """The launcher's cgroup counters (informational; never used to turn a failure into a success)."""
    if chunks is None:
        return {}
    try:
        with tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r:") as tar:
            member = tar.next()
            handle = tar.extractfile(member) if member is not None and member.isreg() else None
            raw = handle.read(4096) if handle is not None else b""
        data = json.loads(raw.decode("utf-8", "replace"))
    except (tarfile.TarError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, int) and value >= 0}


class _PeakMemory(threading.Thread):
    def __init__(self, stats: Iterator[dict[str, object]]) -> None:
        super().__init__(name="devteam-sandbox-stats", daemon=True)
        self._stats = stats
        self.peak_bytes = 0

    def run(self) -> None:
        for sample in self._stats:
            memory = sample.get("memory_stats")
            if not isinstance(memory, dict):
                continue
            usage = int(memory.get("usage") or 0)
            detail = memory.get("stats") if isinstance(memory.get("stats"), dict) else {}
            reclaimable = int(detail.get("inactive_file") or detail.get("total_inactive_file") or 0)  # type: ignore[union-attr]
            self.peak_bytes = max(self.peak_bytes, usage - reclaimable, 0)


class SandboxRunner:
    def __init__(
        self,
        engine: Engine,
        image_lock: ImageLock,
        *,
        clock: Clock = system_clock,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._lock = image_lock
        self._clock = clock
        self._monotonic = monotonic

    def run(self, request: RunRequest) -> RunResult:
        locked = self._lock.get(request.image)
        image_id = verify_image(self._engine.image_id, locked)
        spec = ContainerSpec(
            image=locked.tag,
            argv=request.argv,
            workdir=request.workdir,
            env=request.env,
            limits=request.limits,
            execution_id=request.execution_id,
            labels=request.labels,
        )
        tar = build_tar(request.files, request.limits.work_copy_mb * 1024 * 1024)
        container_id: str | None = None
        try:
            container_id = self._engine.create(spec)
            problems = locked_down_problems(self._engine.inspect(container_id), spec)
            if problems:
                raise SandboxBlockedError("sandbox.not_locked_down", "; ".join(problems))
            self._engine.put_tar(container_id, WORK_DIR, tar)
            return self._execute(container_id, request, image_id)
        finally:
            self._engine.remove(container_id, spec.volume_name)

    def _execute(self, container_id: str, request: RunRequest, image_id: str) -> RunResult:
        limits = request.limits
        stats = _PeakMemory(self._engine.stats_stream(container_id))
        started_at = self._clock.now()
        begin = self._monotonic()
        self._engine.start(container_id)
        stats.start()
        deadline = begin + limits.wall_clock_s
        exit_code: int | None = None
        timed_out = cancelled = False
        while exit_code is None:
            if request.cancel is not None and request.cancel.is_set():
                cancelled = True
                break
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                timed_out = True
                break
            exit_code = self._engine.wait(container_id, min(1.0, remaining))
        if exit_code is None:
            self._engine.kill(container_id)
            exit_code = self._engine.wait(container_id, 10)
        duration_ms = int((self._monotonic() - begin) * 1000)
        stats.join(timeout=5)
        state = self._engine.state(container_id)
        metrics = _read_metrics(self._engine.get_tar(container_id, METRICS_FILE))
        # Docker's flag misses an OOM kill of a child of the init process; the cgroup counter does not.
        oom = bool(state.get("OOMKilled")) or int(metrics.get("oom_kills") or 0) > 0
        peak_bytes = max(stats.peak_bytes, int(metrics.get("peak_bytes") or 0))
        cap = limits.output_kb * 1024
        stdout = _capped(self._engine.log_chunks(container_id, stdout=True), cap)
        stderr = _capped(self._engine.log_chunks(container_id, stdout=False), cap)
        outputs = _read_outputs(
            self._engine.get_tar(container_id, OUT_DIR), limits.work_copy_mb * 1024 * 1024
        )
        status, reason = _classify(
            exit_code, oom=oom, timed_out=timed_out, cancelled=cancelled, limits=limits
        )
        return RunResult(
            status=status,
            exit_code=None
            if status in {SandboxExecutionStatus.TIMED_OUT, SandboxExecutionStatus.CANCELLED}
            else exit_code,
            duration_ms=duration_ms,
            peak_memory_mb=round(peak_bytes / (1024 * 1024), 1) if peak_bytes else None,
            stdout=stdout,
            stderr=stderr,
            outputs=outputs,
            reason=reason,
            image_id=image_id,
            engine_version=self._engine.info.version,
            started_at=started_at,
            finished_at=self._clock.now(),
        )


def _classify(
    exit_code: int | None, *, oom: bool, timed_out: bool, cancelled: bool, limits: ContainerLimits
) -> tuple[SandboxExecutionStatus, str | None]:
    if cancelled:
        return SandboxExecutionStatus.CANCELLED, "stopped on request"
    if timed_out:
        return SandboxExecutionStatus.TIMED_OUT, f"killed after the {limits.wall_clock_s}s wall-clock limit"
    if oom:
        return SandboxExecutionStatus.OOM_KILLED, f"killed by the {limits.memory_mb} MB memory limit"
    if exit_code == 0:
        return SandboxExecutionStatus.COMPLETED, None
    return SandboxExecutionStatus.FAILED, f"exited with code {exit_code}"


__all__ = ["RunRequest", "RunResult", "SandboxRunner", "build_tar"]

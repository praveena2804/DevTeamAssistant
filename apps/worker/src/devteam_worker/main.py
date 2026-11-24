"""``python -m devteam_worker``: start the run worker (D.4; D-41). Started by ``scripts/dev.ps1``.

Fails closed (exit code 2, nothing runs) when:

* the settings are invalid, or cloud tracing is configured (D-30);
* the OS credential store is unavailable (D-18);
* the state database is not at the current migration (the API migrates it, with a backup, on start);
* the agent registry does not hold exactly ten agents with behaviours, or a config file is invalid;
* a configured local model is not installed (models are never downloaded automatically).

If Ollama itself is not running, the worker still starts: runs that need a model wait for resources and
continue when it is back. A second worker refuses to start while the first holds the lease (exit 3); a
worker restarted right after a crash waits up to one lease lifetime for the dead worker's lease to expire.
"""

from __future__ import annotations

import argparse
import signal
import sys
from types import FrameType

from devteam_core.config import load_settings
from devteam_core.errors import ConfigError
from devteam_core.logging import configure_logging, get_logger
from devteam_harness.secrets import secrets_manager_from_settings
from devteam_orchestration.execution import open_checkpointer
from devteam_orchestration.graph import build_graph
from devteam_orchestration.runtime import build_runtime, check_models
from devteam_orchestration.safety import CloudTracingConfiguredError
from devteam_state import create_sqlite_engine, session_factory
from devteam_state.migrate import current_revision, head_revision
from devteam_worker.worker import Worker

_log = get_logger("devteam_worker")
EXIT_REFUSED = 2
EXIT_LEASE_HELD = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devteam_worker", description="DevTeam AI run worker")
    parser.add_argument("--once", action="store_true", help="process the due jobs, then exit")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"worker refused to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    configure_logging(settings)
    try:
        secrets_manager_from_settings(settings)
        engine = create_sqlite_engine(
            settings.state_db_path, busy_timeout_ms=settings.database.busy_timeout_ms
        )
        if current_revision(engine) != head_revision():
            raise ConfigError(
                "the state database is not at the current migration; start the API once to migrate"
            )
        sessions = session_factory(engine)
        runtime = build_runtime(settings, sessions)
        models = check_models(runtime)
        if models.missing:
            raise ConfigError(f"configured local models are not installed: {', '.join(models.missing)}")
        if not models.ok:
            _log.warning("worker_model_unavailable", problem=models.problem)
        graph = build_graph(runtime, open_checkpointer(settings.checkpoints_db_path))
    except (ConfigError, CloudTracingConfiguredError) as exc:
        _log.error("worker_refused", reason=str(exc))
        print(f"worker refused to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    worker = Worker(runtime, graph)
    # after a crash the dead worker's lease may still look fresh: wait at most one lease lifetime
    wait = settings.orchestration.lease_ttl_s + settings.orchestration.heartbeat_interval_s
    if not worker.start(wait_s=wait):
        print("worker refused to start: another worker holds the lease", file=sys.stderr)
        return EXIT_LEASE_HELD

    def _stop(_signum: int, _frame: FrameType | None) -> None:
        worker.stop()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)
    _log.info("worker_started", owner=worker.owner, model_version=models.version)
    try:
        if args.once:
            while not worker.stopping and (worker.run_once() is not None or worker.run_verification_once()):
                pass
        else:
            worker.run_forever()
    finally:
        worker.close()
        runtime.close()
        engine.dispose()
        _log.info("worker_stopped", owner=worker.owner)
    return 0

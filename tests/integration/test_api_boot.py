"""API boots on a fresh database through the real uvicorn server (roadmap §13 Part A integration)."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from devteam_core.config import Settings

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_api_process_boots_on_fresh_database_and_binds_loopback(isolated_settings: Settings) -> None:
    port = _free_port()
    env = {
        **os.environ,
        "DEVTEAM_ROOT": str(isolated_settings.root),
        "DEVTEAM_PROFILE": "test",
        "DEVTEAM_API__PORT": str(port),
    }
    assert not isolated_settings.state_db_path.exists()
    proc = subprocess.Popen(
        [sys.executable, "-m", "devteam_api"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        response = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
                break
            except httpx.TransportError:
                if proc.poll() is not None:
                    break
                time.sleep(0.3)
        assert response is not None, (
            f"API did not start: {proc.stdout.read() if proc.poll() is not None else ''}"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert isolated_settings.state_db_path.is_file()
    finally:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
    assert '"event": "api_started"' in output
    assert '"event": "http_request"' in output
    assert Path(isolated_settings.state_db_path).exists()

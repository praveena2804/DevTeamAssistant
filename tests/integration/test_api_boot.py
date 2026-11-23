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
    # The server's output goes to a file, never an unread pipe: once a pipe buffer fills, the server blocks
    # on its next log write and stops answering (seen in C.4 when the extra migration added DEBUG lines).
    log_path = isolated_settings.root / "api-boot.log"
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "devteam_api"],
        env=env,
        stdout=log_file,
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
        assert response is not None, f"API did not start: {log_path.read_text(encoding='utf-8')[-2000:]}"
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert isolated_settings.state_db_path.is_file()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log_file.close()
    output = log_path.read_text(encoding="utf-8")
    assert '"event": "api_started"' in output
    assert '"event": "http_request"' in output
    assert Path(isolated_settings.state_db_path).exists()

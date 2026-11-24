"""Unit tests for scripts/ (loaded by path: scripts/ is not an installed package)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from devteam_core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"script_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent_count = load_script("check_agent_count")
doctor = load_script("doctor")


# ------------------------------------------------------------------ check_agent_count (L3)


def _root(tmp_path: Path, members: list[str]) -> Path:
    listed = ", ".join(f'"{m}"' for m in members)
    (tmp_path / "pyproject.toml").write_text(f"[tool.uv.workspace]\nmembers = [{listed}]\n", encoding="utf-8")
    return tmp_path


def _make_agents(root: Path, names: list[str]) -> None:
    for name in names:
        (root / "agents" / name).mkdir(parents=True)
        (root / "agents" / name / "pyproject.toml").write_text("", encoding="utf-8")
        spec = root / "agents" / name / "src" / "devteam_agents" / name / "spec.py"
        spec.parent.mkdir(parents=True)
        spec.write_text("", encoding="utf-8")


def test_canonical_list_has_exactly_ten_unique_agents() -> None:
    assert len(agent_count.CANONICAL_AGENTS) == 10
    assert len(set(agent_count.CANONICAL_AGENTS)) == 10


def test_skeleton_mode_passes_without_agents_dir(tmp_path: Path) -> None:
    errors, notes = agent_count.check(_root(tmp_path, ["packages/core"]), strict=False)
    assert errors == []
    assert any("skeleton" in n for n in notes)


def test_strict_mode_requires_agents_dir(tmp_path: Path) -> None:
    errors, _ = agent_count.check(_root(tmp_path, []), strict=True)
    assert errors


def test_strict_is_the_default_since_part_c(tmp_path: Path) -> None:
    errors, _ = agent_count.check(_root(tmp_path, []))
    assert any("agents/ directory is missing" in e for e in errors)
    assert agent_count.main(["--root", str(_root(tmp_path, []))]) == 1
    assert agent_count.main(["--root", str(tmp_path), "--skeleton"]) == 0


def test_agent_without_spec_module_is_rejected(tmp_path: Path) -> None:
    names = list(agent_count.CANONICAL_AGENTS)
    root = _root(tmp_path, [f"agents/{n}" for n in names])
    _make_agents(root, names)
    (root / "agents" / "tester" / "src" / "devteam_agents" / "tester" / "spec.py").unlink()
    errors, _ = agent_count.check(root)
    assert errors == ["agents/tester has no AgentSpec module (src/devteam_agents/tester/spec.py)"]


def test_exactly_ten_agents_pass(tmp_path: Path) -> None:
    names = list(agent_count.CANONICAL_AGENTS)
    root = _root(tmp_path, [f"agents/{n}" for n in names])
    _make_agents(root, names)
    assert agent_count.check(root, strict=True) == ([], [])


def test_eleventh_agent_is_rejected(tmp_path: Path) -> None:
    names = [*agent_count.CANONICAL_AGENTS, "security_agent"]
    root = _root(tmp_path, [f"agents/{n}" for n in names])
    _make_agents(root, names)
    errors, _ = agent_count.check(root, strict=True)
    assert any("security_agent" in e for e in errors)
    assert any("members" in e for e in errors)


def test_missing_agent_and_non_package_dir_rejected(tmp_path: Path) -> None:
    names = list(agent_count.CANONICAL_AGENTS[:-1])
    root = _root(tmp_path, [f"agents/{n}" for n in names])
    _make_agents(root, names)
    (root / "agents" / "documenter").mkdir()  # present but without pyproject.toml
    errors, _ = agent_count.check(root, strict=True)
    assert any("documenter is not a workspace package" in e for e in errors)
    assert any("members" in e for e in errors)


def test_members_without_agents_dir_rejected(tmp_path: Path) -> None:
    errors, _ = agent_count.check(_root(tmp_path, ["agents/head"]))
    assert errors


def test_real_repository_passes() -> None:
    assert agent_count.main(["--root", str(REPO_ROOT)]) == 0
    assert agent_count.main(["--root", str(REPO_ROOT), "--strict"]) == 0


# ------------------------------------------------------------------ doctor


def _fake_runner(outputs: dict[str, str | None]) -> object:
    def runner(args: list[str]) -> str | None:
        return outputs.get(args[0])

    return runner


def _ollama_client(ok: bool) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json={"version": "9.9.9"}) if ok else httpx.Response(500)

    return httpx.Client(transport=httpx.MockTransport(handler))


GOOD_TOOLS = {
    "uv": "uv 0.11.6",
    "node": "v22.20.0",
    "npm": "11.0.0",
    "git": "git version 2.51.2",
    "gitleaks": "8.30.1",
    "ollama": "ollama version is 0.40.16",
    "docker": "Docker version 29.6.2",
}
GOOD_ENV = {"OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_KEEP_ALIVE": "1m"}


def _by_name(checks: list[object]) -> dict[str, object]:
    return {c.name: c for c in checks}  # type: ignore[attr-defined]


def test_doctor_all_good(isolated_settings: Settings) -> None:
    checks = doctor.run_checks(
        isolated_settings, runner=_fake_runner(GOOD_TOOLS), environ=GOOD_ENV, client=_ollama_client(True)
    )
    named = _by_name(checks)
    assert named["ollama.server"].status == "ok"  # type: ignore[attr-defined]
    assert named["OLLAMA_MAX_LOADED_MODELS"].status == "ok"  # type: ignore[attr-defined]
    assert not any(c.status == "fail" for c in checks)


def test_doctor_flags_missing_required_tool_and_wrong_node(isolated_settings: Settings) -> None:
    tools = {**GOOD_TOOLS, "uv": None, "node": "v20.1.0"}
    checks = _by_name(
        doctor.run_checks(
            isolated_settings, runner=_fake_runner(tools), environ=GOOD_ENV, client=_ollama_client(True)
        )
    )
    assert checks["uv"].status == "fail"  # type: ignore[attr-defined]
    assert checks["node"].status == "warn"  # type: ignore[attr-defined]


def test_doctor_rejects_non_loopback_ollama_host(isolated_settings: Settings) -> None:
    env = {**GOOD_ENV, "OLLAMA_HOST": "0.0.0.0:11434"}
    checks = _by_name(
        doctor.run_checks(
            isolated_settings, runner=_fake_runner(GOOD_TOOLS), environ=env, client=_ollama_client(True)
        )
    )
    assert checks["OLLAMA_HOST"].status == "fail"  # type: ignore[attr-defined]


def test_doctor_warns_on_unset_ollama_limits_and_unreachable_server(isolated_settings: Settings) -> None:
    checks = _by_name(
        doctor.run_checks(
            isolated_settings, runner=_fake_runner(GOOD_TOOLS), environ={}, client=_ollama_client(False)
        )
    )
    for key in ("OLLAMA_MAX_LOADED_MODELS", "OLLAMA_NUM_PARALLEL", "OLLAMA_KEEP_ALIVE", "ollama.server"):
        assert checks[key].status == "warn"  # type: ignore[attr-defined]


def test_doctor_sandbox_states(isolated_settings: Settings, tmp_path: Path) -> None:
    """Part E (D-07 (b)): the doctor checks the WSL engine over mutual TLS, not the Docker Desktop CLI."""

    def down(_settings: Settings) -> doctor.SandboxProbe:
        return doctor.SandboxProbe(False, "the Docker Engine in WSL is not reachable")

    def up(_settings: Settings) -> doctor.SandboxProbe:
        return doctor.SandboxProbe(True, "ok", "29.1.3", "Ubuntu 24.04.4 LTS", "Nov 22 16:06:00 2028 GMT")

    profile = tmp_path / "profile"
    profile.mkdir()
    env = {**GOOD_ENV, "USERPROFILE": str(profile)}
    checks = _by_name(
        doctor.run_checks(
            isolated_settings,
            runner=_fake_runner(GOOD_TOOLS),
            environ=env,
            client=_ollama_client(True),
            sandbox_prober=down,
        )
    )
    assert "docker.cli" not in checks  # the Windows Docker CLI (Docker Desktop) is not used
    assert checks["sandbox.engine"].status == "warn"  # type: ignore[attr-defined]
    assert "Blocked" in checks["sandbox.engine"].detail  # type: ignore[attr-defined]
    assert checks["sandbox.wsl_memory"].status == "warn"  # type: ignore[attr-defined]

    (profile / ".wslconfig").write_text("[wsl2]\nmemory=4GB\nswap=2GB\n", encoding="utf-8")
    checks = _by_name(
        doctor.run_checks(
            isolated_settings,
            runner=_fake_runner(GOOD_TOOLS),
            environ=env,
            client=_ollama_client(True),
            sandbox_prober=up,
        )
    )
    assert checks["sandbox.engine"].status == "ok"  # type: ignore[attr-defined]
    assert checks["sandbox.certificates"].status == "ok"  # type: ignore[attr-defined]
    assert checks["sandbox.wsl_memory"].status == "ok"  # type: ignore[attr-defined]

    (profile / ".wslconfig").write_text("[wsl2]\nmemory=8GB\n", encoding="utf-8")
    checks = _by_name(
        doctor.run_checks(
            isolated_settings,
            runner=_fake_runner(GOOD_TOOLS),
            environ=env,
            client=_ollama_client(True),
            sandbox_prober=up,
        )
    )
    assert checks["sandbox.wsl_memory"].status == "warn"  # type: ignore[attr-defined]


@pytest.mark.parametrize(("text", "expected"), [("v22.20.0", (22, 20, 0)), ("uv 0.11", (0, 11)), ("x", None)])
def test_version_parsing(text: str, expected: tuple[int, ...] | None) -> None:
    assert doctor._version(text) == expected


def test_doctor_api_token_states_never_print_the_key(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "K" * 43
    monkeypatch.setenv("DTTEST_SECRET__API_TOKEN", token)
    check = doctor.check_api_token(isolated_settings)
    assert check.status == "ok" and token not in check.detail and "memory" in check.detail
    monkeypatch.delenv("DTTEST_SECRET__API_TOKEN")
    assert doctor.check_api_token(isolated_settings).status == "warn"  # not created yet
    monkeypatch.setenv("DTTEST_SECRET__API_TOKEN", "short-token")
    assert doctor.check_api_token(isolated_settings).status == "fail"
    isolated_settings.api_token_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_settings.api_token_path.write_text(token, encoding="utf-8")
    legacy = doctor.check_api_token(isolated_settings)
    assert legacy.status == "warn" and "migrate_api_token" in legacy.detail and token not in legacy.detail


def test_signin_link_puts_the_key_only_in_the_fragment(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from devteam_api import signin_link  # noqa: PLC0415

    monkeypatch.setenv("DEVTEAM_ROOT", str(isolated_settings.root))
    monkeypatch.setenv("DEVTEAM_PROFILE", "test")
    token = "L" * 43
    monkeypatch.setenv("DTTEST_SECRET__API_TOKEN", token)  # test-profile memory keyring seed
    assert signin_link.main() == 0
    link = capsys.readouterr().out.strip()
    assert not isolated_settings.api_token_path.exists()
    assert link == f"http://127.0.0.1:5173/signin#token={token}"
    assert token not in link.split("#", 1)[0]  # never in the part a browser sends to a server

"""Connection to the WSL Docker Engine (HC-14; D-07 (b), D-50, D-51; Part E plan §3.10-§3.11).

This is the **only** module that imports the Docker SDK (import-linter contract). It builds a client
for ``https://127.0.0.1:<port>`` whose every connection uses the pinned mutual-TLS context from
:mod:`devteam_harness.sandbox.tls`:

- the SDK's own ``TLSConfig`` is not used (it cannot load an encrypted key); a small ``requests`` adapter
  hands our ``SSLContext`` to urllib3 instead, and refuses ``verify=False``, client-certificate overrides
  and proxies;
- requests' default CA bundle is never added to the context (``cert_verify`` is overridden), so the
  private CA stays the only trust anchor;
- proxy settings from the environment or ``~/.docker/config.json`` are ignored and never injected into
  containers.

An engine that is unreachable, reports itself as Docker Desktop, is not Linux, or cannot speak the
pinned API version is refused (:class:`SandboxBlockedError`). There is no fallback.
"""

from __future__ import annotations

import contextlib
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import docker
import requests
from docker.utils.proxy import ProxyConfig
from requests.adapters import HTTPAdapter

from devteam_core.config import Settings
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.spec import LABEL, ContainerSpec, create_arguments, host_config_arguments
from devteam_harness.sandbox.tls import SandboxTLSError, assert_verifying, build_client_context
from devteam_harness.secrets import SecretsManager

ENGINE_API_VERSION = "1.44"  # pinned: no unauthenticated version probe is ever made
_CONNECT_TIMEOUT_S = 10


class PinnedTLSAdapter(HTTPAdapter):
    """Use exactly one pre-built, verifying ``SSLContext`` for every HTTPS connection."""

    def __init__(self, context: ssl.SSLContext) -> None:
        assert_verifying(context)
        self._context = context
        super().__init__(pool_connections=2, pool_maxsize=4, max_retries=0)

    @property
    def context(self) -> ssl.SSLContext:
        return self._context

    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any
    ) -> None:
        pool_kwargs["ssl_context"] = self._context
        pool_kwargs["cert_reqs"] = "CERT_REQUIRED"
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        raise SandboxTLSError("proxies are never used for the Docker Engine connection")

    def get_connection_with_tls_context(
        self, request: requests.PreparedRequest, verify: Any, proxies: Any = None, cert: Any = None
    ) -> Any:
        if verify is False or cert:
            raise SandboxTLSError("TLS verification cannot be disabled or overridden for the engine")
        assert_verifying(self._context)
        url = urlsplit(request.url or "")
        if url.scheme != "https" or url.hostname != "127.0.0.1" or url.port is None:
            raise SandboxTLSError("the engine connection is only allowed to https://127.0.0.1:<port>")
        return self.poolmanager.connection_from_host(url.hostname, url.port, scheme="https")

    def cert_verify(self, conn: Any, url: str, verify: Any, cert: Any) -> None:
        # Never let requests attach its CA bundle: the pinned context already holds the only trust anchor.
        if verify is False:
            raise SandboxTLSError("TLS verification cannot be disabled for the engine")
        conn.cert_reqs = "CERT_REQUIRED"
        conn.ca_certs = None
        conn.ca_cert_dir = None


@dataclass(frozen=True, slots=True)
class EngineInfo:
    version: str
    api_version: str
    min_api_version: str
    os: str
    operating_system: str
    platform_name: str


def https_url(docker_host: str) -> str:
    parts = urlsplit(docker_host)
    if parts.scheme != "tcp" or parts.hostname != "127.0.0.1" or parts.port is None:
        raise SandboxTLSError("sandbox.docker_host must be tcp://127.0.0.1:<port>")
    return f"https://127.0.0.1:{parts.port}"


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(piece) for piece in value.split("."))


class Engine:
    """A verified connection to the WSL engine. Use :func:`connect` to create one."""

    def __init__(self, client: docker.DockerClient, adapter: PinnedTLSAdapter, info: EngineInfo) -> None:
        self.client = client
        self.adapter = adapter
        self.info = info

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- narrow operations used by the runner (the SDK and its errors stay in this module) ------------

    @property
    def api(self) -> Any:
        return self.client.api

    def image_id(self, tag: str) -> str | None:
        try:
            return str(self.api.inspect_image(tag)["Id"])
        except docker.errors.ImageNotFound:
            return None
        except _ENGINE_ERRORS as exc:
            raise _unavailable(exc) from None

    def create(self, spec: ContainerSpec) -> str:
        """Create the per-run volume and the container (not started). Nothing runs yet."""
        try:
            self.api.create_volume(name=spec.volume_name, driver="local", labels=spec.all_labels)
            host_config = self.api.create_host_config(**host_config_arguments(spec))
            created = self.api.create_container(host_config=host_config, **create_arguments(spec))
        except _ENGINE_ERRORS as exc:
            self.remove(None, spec.volume_name)
            raise _unavailable(exc) from None
        return str(created["Id"])

    def inspect(self, container_id: str) -> dict[str, Any]:
        return dict(self.api.inspect_container(container_id))

    def put_tar(self, container_id: str, path: str, data: bytes) -> None:
        if not self.api.put_archive(container_id, path, data):
            raise SandboxBlockedError(
                "sandbox.copy_in_failed", "files could not be copied into the container"
            )

    def start(self, container_id: str) -> None:
        self.api.start(container_id)

    def wait(self, container_id: str, timeout_s: float) -> int | None:
        """Exit code, or ``None`` if the container is still running after ``timeout_s``."""
        try:
            result = self.api.wait(container_id, timeout=timeout_s)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            return None
        return int(result.get("StatusCode", -1))

    def kill(self, container_id: str) -> None:
        with contextlib.suppress(docker.errors.APIError):  # already stopped
            self.api.kill(container_id)

    def state(self, container_id: str) -> dict[str, Any]:
        return dict(self.inspect(container_id).get("State") or {})

    def stats_stream(self, container_id: str) -> Iterator[dict[str, Any]]:
        try:
            yield from self.api.stats(container_id, decode=True, stream=True)
        except (*_ENGINE_ERRORS, ValueError):
            return

    def log_chunks(self, container_id: str, *, stdout: bool) -> Iterator[bytes]:
        yield from self.api.logs(container_id, stdout=stdout, stderr=not stdout, stream=True, follow=False)

    def get_tar(self, container_id: str, path: str) -> Iterator[bytes] | None:
        try:
            stream, _stat = self.api.get_archive(container_id, path)
        except docker.errors.NotFound:
            return None
        chunks: Iterator[bytes] = iter(stream)
        return chunks

    def diff(self, container_id: str) -> list[dict[str, Any]]:
        return list(self.api.diff(container_id) or [])

    def remove(self, container_id: str | None, volume_name: str | None) -> None:
        """Force-remove the container and its volume. Missing objects are fine; never raises."""
        if container_id is not None:
            try:
                self.api.remove_container(container_id, v=True, force=True)
            except docker.errors.NotFound:
                pass
            except _ENGINE_ERRORS:
                pass
        if volume_name is not None:
            try:
                self.api.remove_volume(volume_name, force=True)
            except docker.errors.NotFound:
                pass
            except _ENGINE_ERRORS:
                pass

    def labelled(self) -> tuple[list[str], list[str]]:
        """IDs of every sandbox container and volume still present (label ``devteam.sandbox=1``)."""
        containers = [c["Id"] for c in self.api.containers(all=True, filters={"label": f"{LABEL}=1"})]
        volumes = [
            v["Name"] for v in (self.api.volumes(filters={"label": f"{LABEL}=1"}).get("Volumes") or [])
        ]
        return containers, volumes

    def sweep(self) -> tuple[int, int]:
        """Remove leftovers of crashed runs (startup sweep, plan §3.3 step 6)."""
        containers, volumes = self.labelled()
        for container_id in containers:
            self.remove(container_id, None)
        for name in volumes:
            self.remove(None, name)
        return len(containers), len(volumes)


_ENGINE_ERRORS = (docker.errors.DockerException, requests.RequestException, OSError)


def _unavailable(exc: BaseException) -> SandboxBlockedError:
    if isinstance(exc, docker.errors.APIError):
        return SandboxBlockedError(
            "sandbox.engine_error", f"the engine refused the request ({exc.status_code})"
        )
    return SandboxBlockedError("sandbox.docker_unavailable", "the Docker Engine in WSL is not reachable")


def build_client(
    context: ssl.SSLContext, docker_host: str, *, timeout: int = 120
) -> tuple[docker.DockerClient, PinnedTLSAdapter]:
    """Create the SDK client without contacting the engine (the API version is pinned)."""
    adapter = PinnedTLSAdapter(context)
    client = docker.DockerClient(
        base_url=https_url(docker_host), version=ENGINE_API_VERSION, timeout=timeout, tls=True
    )
    api = client.api
    api.trust_env = False  # ignore HTTP(S)_PROXY / NO_PROXY and .netrc
    api.proxies = {}
    api._proxy_configs = ProxyConfig()
    api.mount("https://", adapter)
    api._custom_adapter = adapter
    return client, adapter


def probe(client: docker.DockerClient) -> EngineInfo:
    """Ask the engine who it is and refuse anything other than a Linux Docker Engine (not Desktop)."""
    try:
        version = client.api.version(api_version=False)
        system = client.api.info()
    except (docker.errors.DockerException, requests.RequestException, OSError) as exc:
        if isinstance(getattr(exc, "__context__", None), ssl.SSLError) or isinstance(
            exc, requests.exceptions.SSLError
        ):
            raise SandboxTLSError("TLS handshake with the Docker Engine failed") from None
        raise SandboxBlockedError(
            "sandbox.docker_unavailable", "the Docker Engine in WSL is not reachable"
        ) from None
    info = EngineInfo(
        version=str(version.get("Version", "")),
        api_version=str(version.get("ApiVersion", "")),
        min_api_version=str(version.get("MinAPIVersion", "")),
        os=str(version.get("Os", "")),
        operating_system=str(system.get("OperatingSystem", "")),
        platform_name=str((version.get("Platform") or {}).get("Name", "")),
    )
    if "desktop" in info.operating_system.lower() or "desktop" in info.platform_name.lower():
        raise SandboxBlockedError("sandbox.engine_not_allowed", "Docker Desktop is not used (D-07 (b))")
    if info.os != "linux":
        raise SandboxBlockedError("sandbox.engine_not_allowed", "the engine must run Linux containers")
    try:
        supported = (
            _version_tuple(info.min_api_version)
            <= _version_tuple(ENGINE_API_VERSION)
            <= _version_tuple(info.api_version)
        )
    except ValueError:
        supported = False
    if not supported:
        raise SandboxBlockedError(
            "sandbox.engine_not_allowed", f"the engine does not support Docker API {ENGINE_API_VERSION}"
        )
    return info


def connect(settings: Settings, secrets: SecretsManager, *, timeout: int = 120) -> Engine:
    """Build the pinned TLS context, the client, and verify the engine. Fails closed."""
    if not settings.sandbox.enabled:
        raise SandboxBlockedError("sandbox.disabled", "the sandbox is disabled in configuration")
    context = build_client_context(settings.sandbox_tls_dir, secrets)
    client, adapter = build_client(context, settings.sandbox.docker_host, timeout=timeout)
    try:
        info = probe(client)
    except BaseException:
        client.close()
        raise
    return Engine(client, adapter, info)


__all__ = [
    "ENGINE_API_VERSION",
    "Engine",
    "EngineInfo",
    "PinnedTLSAdapter",
    "build_client",
    "connect",
    "https_url",
    "probe",
]

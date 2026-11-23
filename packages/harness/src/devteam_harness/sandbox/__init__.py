"""HC-14 isolated execution sandbox (Part E).

Generated code runs only in disposable Docker containers on the WSL2 engine: no network, read-only root,
non-root user, hard resource limits, no host mounts, removed after every run. There is no host fallback:
if the engine or its TLS connection is unavailable, the work is Blocked.
"""

# ADR-0007: Docker distribution for the sandbox

- **Status:** Proposed. **Owner ruling required (D-07).** Blocks Part E; does not block Parts A–D.
- **Date:** 2026-11-19
- **Related:** roadmap §9, §13 E; HC-14; risk R4

## Context

- The generated target application must run only inside disposable, network-isolated containers. There is no host fallback; without Docker, the work is Blocked.
- Observed on the development laptop (2026-11-19): **Docker Desktop is already installed** (CLI 29.6.2, context `desktop-linux`). The engine was not running at the time.

## Options

| Option | Notes |
|---|---|
| (a) Docker Desktop | Already installed. Free only within Docker's subscription terms (personal use, education, small businesses). Commercial use in larger organisations requires a paid subscription |
| (b) Docker Engine inside WSL2 | Free and open source; more set-up work |
| (c) Podman | Free; Docker-API compatibility to be verified with the Docker SDK |

## Recommendation

- If the owner's use of Docker Desktop is covered by its free terms, use **(a)**.
- Otherwise use **(b)**, per roadmap D-07.

The roadmap prohibits paid dependencies (NFR-013), so the licensing context must be confirmed by the owner. It cannot be assumed.

## Consequences

The sandbox code (Part E) talks to the Docker Engine API and works with options (a) and (b) unchanged.

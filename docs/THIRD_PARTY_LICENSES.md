# Third-Party Licence Inventory

Scope: the platform's **runtime** Python dependencies (`uv export --no-dev --all-packages`), as pinned in `uv.lock`. Licences are read from each installed package's own metadata (`License-Expression`, otherwise its licence classifiers). Last checked: 2026-11-23, Part D.2.

The generated target applications are not covered here; their dependencies come from the Capability Catalog (Part G).

**Rules (CLAUDE.md §10, NFR-013):**
- no AGPL or GPL dependency;
- no paid service;
- any copyleft licence needs an owner ruling before it is used.

`tests/contract/test_third_party_licences.py` checks this file against the installed packages. It fails when a version or licence here no longer matches, when any installed package (runtime or development) carries a GPL-family licence, or when an MPL-2.0 package appears that §1 does not list.

## 1. Weak-copyleft (MPL-2.0) packages

| Package | Version | Licence expression | Pulled in by | Ruling |
|---|---|---|---|---|
| `orjson` | 3.13.0 | `MPL-2.0 AND (Apache-2.0 OR MIT)` | `langgraph-sdk` (LangGraph) and `langsmith` (via `langchain-core`); D.1 | **Accepted conditionally** by the owner on 2026-11-23 (KI-023): obligations below; source never modified |
| `certifi` | 2026.10.23 | MPL-2.0 (classifier) | `httpx` / `httpcore` and `requests`; in the lockfile since Part A (v0.1.0) | **Not yet ruled.** Found during the D.2 licence review; reported to the owner (KI-026) |

Development-only tools, which are never part of a runtime install or a distribution:
- `hypothesis` 6.170.15 (MPL-2.0), the test framework;
- `pathspec` 1.1.1 (MPL-2.0), used by mypy.

### 1.1 What MPL-2.0 requires of this project

MPL-2.0 is a file-level copyleft licence. Its conditions attach to the MPL-licensed files themselves, not to the code that uses them (MPL-2.0 §3.3, "Larger Work"). The platform's own code keeps its own licence.

| Obligation (MPL-2.0 section) | How this project meets it |
|---|---|
| Do not remove or alter the licence notices (§3.4) | The packages are installed unchanged from PyPI with hash-pinned wheels (`uv.lock`). Their notices stay in their `dist-info/licenses/` folders: for orjson, `LICENSE-MPL-2.0`, `LICENSE-APACHE` and `LICENSE-MIT`; for certifi, `LICENSE`. The test above checks that orjson's three files are present |
| Modified MPL files must stay under MPL-2.0 and their source must be published (§3.1-§3.2) | **No MPL file is modified.** The packages are used as published: no fork, no patch, no vendored copy in this repository |
| If the software is distributed in executable form, recipients must be told how to get the MPL source (§3.2(a)) | Today the platform is **not distributed**: it runs locally from source, installed by `uv sync`, which downloads the packages from PyPI. **If an installer, container image or other bundle of the platform is ever distributed**, it must include these notices and a pointer to the unmodified upstream source: the orjson source distribution on PyPI (`orjson-3.13.0.tar.gz`, hash in `uv.lock`) and https://github.com/ijl/orjson, and likewise for certifi |
| The executable form may be distributed under other terms only if those terms do not limit the recipient's rights to the MPL source (§3.2(b)) | Nothing in this project restricts them |

**What triggers a new review:**
- upgrading orjson or certifi to a version whose licence expression changes (the test fails);
- copying any of their source files into this repository;
- distributing the platform in packaged form.

## 2. All runtime packages

76 packages. Three are installed only on other platforms (environment markers) and are not on this Windows install.

| Package | Version | Licence (from package metadata) |
|---|---|---|
| aiosqlite | 0.22.1 | MIT License |
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.7 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| distro | 1.9.0 | Apache Software License |
| fastapi | 0.165.0 | MIT |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.16.2 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.16.2 | BSD-3-Clause |
| httpx2-jsfetch | 1.0 | not installed on Windows (platform-specific); licence not checked |
| idna | 3.20 | BSD-3-Clause |
| jaraco.classes | 3.4.0 | MIT License |
| jaraco.context | 6.1.2 | MIT |
| jaraco.functools | 4.6.0 | MIT |
| jeepney | 0.9.0 | not installed on Windows (platform-specific); licence not checked |
| jsonpatch | 1.48 | BSD-3-Clause |
| jsonpointer | 3.12 | BSD-3-Clause |
| keyring | 25.7.0 | MIT |
| langchain-core | 1.8.4 | MIT License |
| langchain-protocol | 0.0.22 | MIT License |
| langgraph | 1.2.24 | MIT |
| langgraph-checkpoint | 4.3.2 | MIT |
| langgraph-checkpoint-sqlite | 3.1.3 | MIT |
| langgraph-prebuilt | 1.1.3 | MIT |
| langgraph-sdk | 0.4.14 | MIT |
| langsmith | 0.16.0 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| more-itertools | 11.2.1 | MIT |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| opentelemetry-sdk | 1.45.2 | Apache-2.0 |
| opentelemetry-semantic-conventions | 0.66b2 | Apache-2.0 |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| ormsgpack | 1.13.1 | Apache-2.0 OR MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pdfminer.six | 20260107 | MIT |
| pdfplumber | 0.11.10 | MIT License |
| pillow | 13.0.0 | MIT-CMU |
| psutil | 7.2.2 | BSD-3-Clause |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| pypdf | 6.21.4 | BSD-3-Clause |
| pypdfium2 | 5.15.2 | BSD-3-Clause, Apache-2.0, dependency licenses |
| python-docx | 1.2.0 | MIT License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.34 | Apache-2.0 |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT License |
| requests | 2.34.2 | Apache Software License |
| requests-toolbelt | 1.0.0 | Apache Software License |
| secretstorage | 3.5.0 | not installed on Windows (platform-specific); licence not checked |
| sniffio | 1.3.1 | MIT License / Apache Software License |
| SQLAlchemy | 2.1.9 | MIT |
| sqlite-vec | 0.1.9 | MIT License, Apache License, Version 2.0 |
| starlette | 1.10.0 | BSD-3-Clause |
| structlog | 26.1.0 | MIT OR Apache-2.0 |
| tenacity | 9.2.1 | Apache-2.0 |
| truststore | 0.10.5 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| uuid_utils | 0.19.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| websockets | 17.4 | BSD-3-Clause |
| xxhash | 4.1.0 | BSD-2-Clause |
| zstandard | 0.25.0 | BSD-3-Clause |

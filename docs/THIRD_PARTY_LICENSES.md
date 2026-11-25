# Third-Party Licence Inventory

Scope: the platform's **runtime** Python dependencies (`uv export --no-dev --all-packages`), as pinned in `uv.lock`. Licences are read from each installed package's own metadata (`License-Expression`, otherwise its licence classifiers). Last checked: 2026-11-23, Part D.

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
| `certifi` | 2026.10.23 | `MPL-2.0` (metadata `License` field and classifier) | `httpx` / `httpcore` and `requests`; in the lockfile since Part A (v0.1.0) | **Accepted conditionally** by the owner on 2026-11-23 (KI-026), on the same terms as orjson: obligations below; source never modified |
| `tqdm` | 4.70.1 | `MPL-2.0 AND MIT` | `chromadb` (Part F.5, D-54) | **Accepted** by the owner on 2026-11-25 with ChromaDB (AP-024; audit TR-0141): progress bars only, used unmodified as a separate package; obligations below |

Development-only tools, which are never part of a runtime install or a distribution:
- `hypothesis` 6.170.15 (MPL-2.0), the test framework;
- `pathspec` 1.1.1 (MPL-2.0), used by mypy.

### 1.1 What MPL-2.0 requires of this project

MPL-2.0 is a file-level copyleft licence. Its conditions attach to the MPL-licensed files themselves, not to the code that uses them (MPL-2.0 §3.3, "Larger Work"). The platform's own code keeps its own licence.

| Obligation (MPL-2.0 section) | How this project meets it |
|---|---|
| Do not remove or alter the licence notices (§3.4) | The packages are installed unchanged from PyPI with hash-pinned wheels (`uv.lock`). Their notices stay in their `dist-info/licenses/` folders: for orjson, `LICENSE-MPL-2.0`, `LICENSE-APACHE` and `LICENSE-MIT`; for certifi, `LICENSE`. The test above checks that these files are present |
| Modified MPL files must stay under MPL-2.0 and their source must be published (§3.1-§3.2) | **No MPL file is modified.** The packages are used as published: no fork, no patch, no vendored copy in this repository |
| If the software is distributed in executable form, recipients must be told how to get the MPL source (§3.2(a)) | Today the platform is **not distributed**: it runs locally from source, installed by `uv sync`, which downloads the packages from PyPI. **If an installer, container image or other bundle of the platform is ever distributed**, it must include these notices and a pointer to the unmodified upstream source: the orjson source distribution on PyPI (`orjson-3.13.0.tar.gz`, hash in `uv.lock`) and https://github.com/ijl/orjson, and likewise for certifi |
| The executable form may be distributed under other terms only if those terms do not limit the recipient's rights to the MPL source (§3.2(b)) | Nothing in this project restricts them |

**What triggers a new review:**
- upgrading orjson, certifi or tqdm to a version whose licence expression changes (the test fails);
- copying any of their source files into this repository;
- distributing the platform in packaged form.

## 2. All runtime packages

134 packages. 4 are installed only on other platforms (environment markers) and are not on this Windows install.

| Package | Version | Licence (from package metadata) |
|---|---|---|
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| aiohttp-retry | 2.9.1 | MIT License |
| aiosignal | 1.4.0 | Apache Software License |
| aiosqlite | 0.22.1 | MIT License |
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.7 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| attrs | 26.1.0 | MIT |
| bcrypt | 5.0.0 | Apache Software License |
| build | 1.6.1 | MIT |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| chromadb | 1.5.9 | Apache Software License |
| click | 8.5.1 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| distro | 1.9.0 | Apache Software License |
| docker | 7.2.0 | Apache-2.0 |
| durationpy | 0.12 | MIT |
| fastapi | 0.165.0 | MIT |
| filelock | 4.2.12 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| googleapis-common-protos | 1.76.0 | Apache-2.0 |
| grpcio | 1.85.4 | Apache-2.0 |
| h11 | 0.16.0 | MIT License |
| hf-xet | 1.8.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.16.2 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.16.2 | BSD-3-Clause |
| httpx2-jsfetch | 1.0 | not installed on Windows (sys_platform == 'emscripten') |
| huggingface_hub | 2.4.0 | Apache Software License |
| idna | 3.20 | BSD-3-Clause |
| importlib_resources | 7.1.0 | Apache-2.0 |
| jaraco.classes | 3.4.0 | MIT License |
| jaraco.context | 6.1.2 | MIT |
| jaraco.functools | 4.6.0 | MIT |
| jeepney | 0.9.0 | not installed on Windows (sys_platform == 'linux') |
| jsonpatch | 1.48 | BSD-3-Clause |
| jsonpointer | 3.12 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| keyring | 25.7.0 | MIT |
| kubernetes | 37.0.3 | Apache-2.0 |
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
| markdown-it-py | 4.3.0 | MIT License |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT License |
| mmh3 | 5.3.1 | MIT License |
| more-itertools | 11.2.1 | MIT |
| multidict | 7.2.1 | Apache-2.0 |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| oauthlib | 4.0.1 | BSD-3-Clause |
| onnxruntime | 1.32.0 | MIT License |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| opentelemetry-exporter-otlp-common | 0.66b2 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-common | 1.45.2 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-grpc | 1.45.2 | Apache-2.0 |
| opentelemetry-proto | 1.45.2 | Apache-2.0 |
| opentelemetry-sdk | 1.45.2 | Apache-2.0 |
| opentelemetry-semantic-conventions | 0.66b2 | Apache-2.0 |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| ormsgpack | 1.13.1 | Apache-2.0 OR MIT |
| overrides | 7.7.0 | Apache License, Version 2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pdfminer.six | 20260107 | MIT |
| pdfplumber | 0.11.10 | MIT License |
| pillow | 13.0.0 | MIT-CMU |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 7.36.2 | 3-Clause BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| pybase64 | 1.5.2 | BSD-2-Clause |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic-settings | 2.16.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pypdf | 6.21.4 | BSD-3-Clause |
| pypdfium2 | 5.15.2 | BSD-3-Clause, Apache-2.0, dependency licenses |
| PyPika | 0.51.1 | Apache Software License |
| pyproject_hooks | 1.3.3 | MIT |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| python-docx | 1.2.0 | MIT License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.34 | Apache-2.0 |
| pywin32 | 312 | Python Software Foundation License |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT License |
| referencing | 0.37.0 | MIT |
| requests | 2.34.2 | Apache Software License |
| requests-oauthlib | 2.0.0 | BSD License |
| requests-toolbelt | 1.0.0 | Apache Software License |
| rich | 15.0.0 | MIT License |
| rpds-py | 2026.9.1 | MIT |
| secretstorage | 3.5.0 | not installed on Windows (sys_platform == 'linux') |
| shellingham | 1.5.4 | ISC License (ISCL) |
| sniffio | 1.3.1 | MIT License / Apache Software License |
| SQLAlchemy | 2.1.9 | MIT |
| sqlite-vec | 0.1.9 | MIT License, Apache License, Version 2.0 |
| starlette | 1.10.0 | BSD-3-Clause |
| structlog | 26.1.0 | MIT OR Apache-2.0 |
| tenacity | 9.2.1 | Apache-2.0 |
| tokenizers | 0.23.3 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| truststore | 0.10.5 | MIT |
| typer | 0.29.2 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uuid_utils | 0.19.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | not installed on Windows (platform_python_implementation != 'PyPy' and sys_platform != 'cygwin' and sys_platform != 'win32') |
| watchfiles | 1.3.0 | MIT License |
| websocket-client | 1.9.2 | Apache-2.0 |
| websockets | 17.4 | BSD-3-Clause |
| xxhash | 4.1.0 | BSD-2-Clause |
| yarl | 1.25.1 | Apache-2.0 |
| zstandard | 0.25.0 | BSD-3-Clause |

## 3. Sandbox images (Part E)

The two sandbox images are **built and used only on this machine** (`scripts/build_sandbox_images.py`); they are never pushed to a registry or distributed. Licences are handled in tiers (`sandbox/images/licence_policy.yaml`, Part E plan §12.3, approved AP-021). The full per-package list is `sandbox/images/licence_report.json`; `tests/contract/test_sandbox_images.py` re-checks it against the policy on every test run.

| Tier | What | Rule | `python_api` | `node_web` |
|---|---|---|---|---|
| 1 | Debian packages of the pinned base image | Any free licence, **including GPL/LGPL** (e.g. `bash`, `coreutils`, `glibc`); unmodified; copyright files kept in `/usr/share/doc` | 87 | 88 |
| 2 | Runtimes and the base image's own packages (CPython PSF-2.0, `pip` MIT; Node.js MIT, npm Artistic-2.0) | Permissive | 1 | Node.js and npm (not listed separately) |
| 3 | Build/test/check tools installed for recipes | Permissive; MPL-2.0 or LGPL only as unmodified separate packages listed for review | 18 | 80 |
| 4 | Runtime dependencies that travel with every generated app | Permissive only | 17 | 3 |

**Tier-3 MPL-2.0 packages listed for the owner's review** (unmodified, not copied into generated apps):

| Package | Image | Why it is there |
|---|---|---|
| `certifi` | `python_api` | CA bundle used by `httpx` in generated tests only (same package and terms as the platform, KI-026) |
| `lightningcss` (+ `-linux-x64-gnu`, `-linux-x64-musl`) | `node_web` | CSS transformer/minifier that Vite 8 runs at build time (KI-037) |

**Obligations.** The images are not distributed, so neither the GPL/LGPL source-offer obligations of the tier-1 packages nor the MPL-2.0 executable-form obligations are triggered. Distributing an image would need a separate decision and would then require these notices and pointers to the corresponding source. Nothing is modified. Vulnerability audits (`pip-audit`, `npm audit`) run on the locked dependency lists at every image build; the result is stored in `sandbox/images/images.lock.json` (both **clean** at the 2026-11-23 build).

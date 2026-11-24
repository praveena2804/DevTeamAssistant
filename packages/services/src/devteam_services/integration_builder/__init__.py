"""Integration/Build Service base (deterministic service, not an agent; D-24; Part E plan §3.6).

- :func:`load_manifest` / :func:`template_files`: a starter kit = ``templates/target_apps/_common`` plus the
  kit's own folder, with ``{{APP_TITLE}}``-style placeholders rendered;
- :func:`template_checksum`: SHA-256 over every file path and content (changes make old verifications stale);
- :func:`materialise`: write a kit into ``workspaces/<project>/generated/`` (only if it is empty), render
  ``generated/.env`` from the manifest's **non-secret** placeholders, and record the protected files'
  checksums in ``runs/template_materialisation.json`` (a platform-owned area agents cannot write);
- :func:`protected_changes`: which protected files no longer match (reported as violations from Part K).

In Part E it never builds anything inside a run (D-47); building happens through the sandbox recipes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from devteam_core.contracts.sandbox import TemplateManifest
from devteam_core.paths import find_repo_root

COMMON = "_common"
RECORD = "runs/template_materialisation.json"
_TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".json", ".html", ".css", ".md", ".tmpl", ".yml", ".yaml", ".toml", ".ini",
    ".mako", ".txt", ".example", "",
}  # fmt: skip


class TemplateError(ValueError):
    pass


def templates_root() -> Path:
    return find_repo_root() / "templates" / "target_apps"


def template_ids(root: Path | None = None) -> list[str]:
    base = root or templates_root()
    if not base.is_dir():  # an installation without starter kits simply has none (never a crash)
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "template.yaml").is_file())


def load_manifest(template_id: str, root: Path | None = None) -> TemplateManifest:
    base = root or templates_root()
    if template_id not in template_ids(base):
        raise TemplateError(f"unknown starter kit {template_id!r}")
    data = yaml.safe_load((base / template_id / "template.yaml").read_text(encoding="utf-8"))
    manifest = TemplateManifest.model_validate(data)
    if manifest.template_id != template_id:
        raise TemplateError("template.yaml names a different template")
    sensitive = [key for key, value in manifest.env_placeholders.items() if _secret_like(key, value)]
    if sensitive:
        raise TemplateError(f"env placeholders must not be secrets: {sensitive}")
    return manifest


_SECRET_WORDS = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "API_KEY", "PRIVATE", "CREDENTIAL")


def _secret_like(key: str, value: str) -> bool:
    """A placeholder must be a plain non-secret default: no secret-like name, no credentials in a URL."""
    return any(word in key.upper() for word in _SECRET_WORDS) or bool(re.search(r"://[^/@\s]+@", value))


def _is_text(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[1] if "." in name else ""
    return suffix in _TEXT_SUFFIXES or name in {"Dockerfile", ".gitignore"}


def template_files(
    template_id: str, *, app_title: str = "Starter Kit", root: Path | None = None
) -> dict[str, bytes]:
    """The kit's files, placeholders rendered, keyed by their path inside ``generated/``."""
    base = root or templates_root()
    manifest = load_manifest(template_id, base)
    values = {
        "{{APP_TITLE}}": app_title,
        "{{TEMPLATE_ID}}": manifest.template_id,
        "{{TEMPLATE_VERSION}}": manifest.version,
    }
    files: dict[str, bytes] = {}
    for folder in (base / COMMON, base / template_id):
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder).as_posix()
            if not path.is_file() or path.is_symlink() or relative == "template.yaml":
                continue
            if any(part in {"node_modules", "__pycache__", "dist"} for part in path.parts):
                continue
            data = path.read_bytes()
            if _is_text(relative):
                text = data.decode("utf-8")
                for placeholder, value in values.items():
                    text = text.replace(placeholder, value)
                data = text.encode("utf-8")
            if relative.endswith(".tmpl"):
                relative = relative[: -len(".tmpl")]
            files[relative] = data
    missing = [p for p in manifest.protected_files if p not in files]
    if missing:
        raise TemplateError(f"protected files missing from the kit: {missing}")
    return files


def template_checksum(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode() + b"\0" + hashlib.sha256(files[path]).digest())
    return digest.hexdigest()


def render_env(manifest: TemplateManifest, app_title: str) -> bytes:
    lines = ["# Rendered by DevTeam AI from the starter kit's non-secret defaults. Never put secrets here."]
    for key, value in manifest.env_placeholders.items():
        lines.append(f"{key}={value.replace('{{APP_TITLE}}', app_title)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class Materialised:
    template_id: str
    version: str
    checksum: str
    files: int
    protected: dict[str, str]  # path -> sha256


def materialise(
    workspace: Path, template_id: str, *, app_title: str, now: datetime, root: Path | None = None
) -> Materialised:
    generated = workspace / "generated"
    if generated.exists() and any(generated.iterdir()):
        raise TemplateError("generated/ is not empty; a starter kit is only copied into an empty folder")
    manifest = load_manifest(template_id, root)
    files = template_files(template_id, app_title=app_title, root=root)
    files[".env"] = render_env(manifest, app_title)
    for relative, data in files.items():
        target = generated.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    protected = {p: hashlib.sha256(files[p]).hexdigest() for p in manifest.protected_files}
    record = {
        "template_id": manifest.template_id,
        "version": manifest.version,
        "checksum": template_checksum(files),
        "materialised_at": now.isoformat(),
        "protected": protected,
        "writable_slots": list(manifest.writable_slots),
        "tester_slots": list(manifest.tester_slots),
    }
    target = workspace.joinpath(*RECORD.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return Materialised(manifest.template_id, manifest.version, record["checksum"], len(files), protected)  # type: ignore[arg-type]


def protected_changes(workspace: Path) -> list[str]:
    """Protected starter-kit files that were changed or deleted since materialisation."""
    record = json.loads(workspace.joinpath(*RECORD.split("/")).read_text(encoding="utf-8"))
    changed: list[str] = []
    for relative, expected in record["protected"].items():
        path = workspace / "generated" / Path(*relative.split("/"))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed.append(relative)
    return changed


def in_slots(path: str, slots: list[str] | tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in slots)


__all__ = [
    "Materialised",
    "TemplateError",
    "in_slots",
    "load_manifest",
    "materialise",
    "protected_changes",
    "render_env",
    "template_checksum",
    "template_files",
    "template_ids",
    "templates_root",
]

"""``python -m devteam_static_check <folder>``: offline checks of a plain HTML/CSS/JS frontend (Part E §3.4).

Checks every ``.html`` file in the folder:

- it parses, and has ``<html>``, ``<head>``, ``<title>`` and ``<body>``;
- every local ``href`` / ``src`` points to a file that exists inside the folder (no ``..`` escape);
- nothing loads from another site (``http:``, ``https:`` or ``//`` in ``src``, stylesheet ``href`` or
  ``import``), because the generated app must work offline and must not depend on third-party hosts;
- every ``.js`` and ``.css`` file is referenced by some page (unused files are reported).

Prints one JSON object (``{"files": n, "findings": [...]}``) and exits 1 if there are findings.
Part of the DevTeam AI platform's sandbox image; not third-party code.
"""

from __future__ import annotations

import json
import sys
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

_EXTERNAL = ("http:", "https:", "//", "data:text/javascript")
_REQUIRED_TAGS = ("html", "head", "title", "body")


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.references: list[tuple[str, str, str]] = []  # (tag, attribute, value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        values = {name: value or "" for name, value in attrs}
        if tag in {"script", "img", "iframe", "source", "audio", "video"} and values.get("src"):
            self.references.append((tag, "src", values["src"]))
        if tag == "link" and values.get("href"):
            self.references.append((tag, "href", values["href"]))
        if tag == "a" and values.get("href"):
            self.references.append((tag, "href", values["href"]))


def _local_target(root: Path, page: Path, value: str) -> Path | None:
    clean = value.split("#", 1)[0].split("?", 1)[0]
    if not clean or clean.startswith(("mailto:", "tel:")):
        return None
    relative = PurePosixPath(clean.lstrip("/")) if clean.startswith("/") else PurePosixPath(clean)
    base = root if clean.startswith("/") else page.parent
    return (base / Path(*relative.parts)).resolve()


def check(root: Path) -> dict[str, object]:
    root = root.resolve()
    findings: list[str] = []
    pages = sorted(root.rglob("*.html"))
    referenced: set[Path] = set()
    if not pages:
        findings.append("no .html page found")
    for page in pages:
        name = page.relative_to(root).as_posix()
        parser = _Page()
        try:
            parser.feed(page.read_text(encoding="utf-8"))
            parser.close()
        except (UnicodeDecodeError, ValueError) as exc:
            findings.append(f"{name}: cannot be read ({type(exc).__name__})")
            continue
        for tag in _REQUIRED_TAGS:
            if tag not in parser.tags:
                findings.append(f"{name}: missing <{tag}>")
        for tag, attribute, value in parser.references:
            lowered = value.strip().lower()
            if lowered.startswith(_EXTERNAL):
                if tag != "a":
                    findings.append(f"{name}: <{tag} {attribute}> loads from another site: {value}")
                continue
            target = _local_target(root, page, value)
            if target is None:
                continue
            if root not in target.parents and target != root:
                findings.append(f"{name}: <{tag} {attribute}> points outside the frontend: {value}")
            elif not target.is_file():
                findings.append(f"{name}: <{tag} {attribute}> points to a missing file: {value}")
            else:
                referenced.add(target)
    for asset in sorted([*root.rglob("*.js"), *root.rglob("*.css")]):
        text = asset.read_text(encoding="utf-8", errors="replace")
        if asset.suffix == ".css" and any(
            f"@import url({p}" in text or f'@import "{p}' in text for p in ("http", "//")
        ):
            findings.append(f"{asset.relative_to(root).as_posix()}: imports a stylesheet from another site")
        if asset.resolve() not in referenced:
            findings.append(f"{asset.relative_to(root).as_posix()}: not referenced by any page")
    return {"files": len(pages), "findings": findings}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(json.dumps({"error": "usage: python -m devteam_static_check <folder>"}))
        return 2
    result = check(Path(args[0]))
    print(json.dumps(result, indent=2))
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

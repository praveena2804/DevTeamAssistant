"""The digest-locked sandbox images (Part E plan §3.5; D-08).

``sandbox/images/images.lock.json`` is written only by ``scripts/build_sandbox_images.py`` after a build
whose licence report and audits passed. Before every container run the engine's image is compared with
the lock: a missing image or a different image ID means the work is **Blocked**.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from devteam_harness.sandbox.errors import SandboxBlockedError

ImageName = Literal["python_api", "node_web"]
IMAGE_NAMES: tuple[ImageName, ...] = ("python_api", "node_web")
LOCK_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class LockedImage:
    name: ImageName
    tag: str
    image_id: str  # sha256:<64 hex>


@dataclass(frozen=True, slots=True)
class ImageLock:
    images: dict[str, LockedImage]

    @classmethod
    def load(cls, path: Path) -> ImageLock:
        if not path.is_file():
            raise SandboxBlockedError(
                "sandbox.image_missing", "sandbox images are not built yet (scripts/build_sandbox_images.py)"
            )
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != LOCK_SCHEMA:
            raise SandboxBlockedError("sandbox.image_missing", "unknown image lock format")
        images = {
            name: LockedImage(name=name, tag=str(entry["tag"]), image_id=str(entry["image_id"]))
            for name, entry in data.get("images", {}).items()
            if name in IMAGE_NAMES
        }
        return cls(images)

    def get(self, name: str) -> LockedImage:
        try:
            return self.images[name]
        except KeyError:
            raise SandboxBlockedError(
                "sandbox.image_missing", f"image {name} is not in the image lock"
            ) from None


def verify_image(image_id_of: Callable[[str], str | None], locked: LockedImage) -> str:
    """Return the image ID if the engine holds exactly the locked image; otherwise Blocked.

    ``image_id_of`` is :meth:`devteam_harness.sandbox.engine.Engine.image_id` (``None`` = not present).
    """
    found = image_id_of(locked.tag)
    if found is None:
        raise SandboxBlockedError("sandbox.image_missing", f"image {locked.tag} is not built")
    if found != locked.image_id:
        raise SandboxBlockedError(
            "sandbox.image_digest_mismatch", f"image {locked.tag} does not match the image lock"
        )
    return locked.image_id


__all__ = ["IMAGE_NAMES", "ImageLock", "LockedImage", "verify_image"]

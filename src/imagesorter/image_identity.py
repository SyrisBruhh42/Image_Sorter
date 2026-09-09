"""Immutable cache identity: source stat, decoder version, frame and preview."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def source_identity(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Cache inputs must be regular non-symlink files")
    return stat_identity(info)


@lru_cache(maxsize=4)
def _identity_manager(root, catalogue, catalogue_identity):
    """Reuse immutable catalogue validation; active_path still reads live state.

    This is a cache label, never executable authorization. Capability workers
    independently validate and lease the exact installed component version.
    """
    from .component_manager import ComponentManager
    return ComponentManager(root=Path(root), catalog_path=Path(catalogue))


def decoder_identity(path):
    from .components import component_for_extension
    from .paths import get_components_dir
    extension = Path(path).suffix.lower().lstrip(".")
    descriptor = component_for_extension(extension)
    component_id = descriptor.component_id if descriptor else None
    if extension in {"gif", "png", "apng", "webp", "tif", "tiff"}:
        component_id = "viewer.animation-multipage"
    active = None
    if component_id:
        catalogue = Path(__file__).parent / "resources" / "component_catalog.json"
        manager = _identity_manager(str(get_components_dir()), str(catalogue), source_identity(catalogue))
        active = manager.active_path(component_id)
    return (component_id, active.name) if active else ("core.qt", "base")


@dataclass(frozen=True)
class ImageCacheKey:
    path: str
    source: tuple
    decoder: tuple
    frame: int = 0
    preview: tuple | None = None


def cache_key(path, *, frame=0, target_size=None):
    path = os.path.abspath(path)
    return ImageCacheKey(path, source_identity(path), decoder_identity(path), frame,
                         tuple(target_size) if target_size else None)


def matches_metadata(key, metadata):
    return (tuple(metadata.get("source_identity", ())) == key.source and
            tuple(metadata.get("decoder_identity", ())) == key.decoder and
            metadata.get("frame", 0) == key.frame and
            (tuple(metadata["target_size"]) if metadata.get("target_size") else None) == key.preview)

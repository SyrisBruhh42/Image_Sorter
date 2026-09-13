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
    if os.name != "nt":
        return stat_identity(info)

    # CPython Windows pathname stat can expose creation time as ctime while
    # fstat exposes change time. Match the reader's complete handle identity;
    # never discard or tolerate drift in either API's five-field observation.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        final = os.fstat(descriptor)
        if not all(stat.S_ISREG(item.st_mode) for item in (opened, current, final)):
            raise ValueError("Cache inputs must be regular non-symlink files")
        path_identity, handle_identity = stat_identity(info), stat_identity(opened)
        if (path_identity != stat_identity(current) or handle_identity != stat_identity(final)
                or path_identity[:4] != handle_identity[:4]):
            raise ValueError("Source changed during cache identity lookup")
        return handle_identity
    finally:
        os.close(descriptor)


@lru_cache(maxsize=4)
def _identity_manager(root, catalogue, catalogue_identity):
    """Reuse immutable catalogue validation; active_path still reads live state.

    This is a cache label, never executable authorization. Capability workers
    independently validate and lease the exact installed component version.
    """
    from .component_manager import ComponentManager
    # Match the reader's default-profile contract. The root argument partitions
    # this cache by profile; it is not an explicit component-store override,
    # whose stricter POSIX permission check is unsuitable for Windows mode bits.
    manager = ComponentManager(catalog_path=Path(catalogue))
    if manager.root != Path(root).resolve():
        raise ValueError("Component profile changed during cache identity lookup")
    return manager


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

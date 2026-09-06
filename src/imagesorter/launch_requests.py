"""Launch request parsing and shared launch contract v1 integration for Image Sorter."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .settings_manager import SettingsManager

SUPPORTED_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tiff",
    ".tif",
)


def is_supported_image(filepath: str) -> bool:
    """Checks whether a filepath has a supported image extension."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def parse_launch_paths(args: Sequence[str]) -> list[str]:
    """Parses CLI arguments into a validated, ordered list of absolute image file paths.

    Filters out CLI flags, expands directories deterministically (sorted alphabetically),
    validates file existence and extensions, and deduplicates while preserving order.

    Args:
        args: Command-line arguments excluding executable name (e.g. sys.argv[1:]).

    Returns:
        List of absolute canonical image file paths.
    """
    validated_paths: list[str] = []
    seen: set[str] = set()

    for arg in args:
        if not arg or arg.startswith("-"):
            continue

        abs_path = os.path.realpath(os.path.abspath(arg))
        if not os.path.exists(abs_path):
            continue

        if os.path.isdir(abs_path):
            try:
                entries = sorted(os.listdir(abs_path))
            except OSError:
                continue

            for entry in entries:
                child_path = os.path.realpath(os.path.abspath(os.path.join(abs_path, entry)))
                if os.path.isfile(child_path) and is_supported_image(child_path):
                    if child_path not in seen:
                        seen.add(child_path)
                        validated_paths.append(child_path)
        elif os.path.isfile(abs_path) and is_supported_image(abs_path):
            if abs_path not in seen:
                seen.add(abs_path)
                validated_paths.append(abs_path)

    return validated_paths


def open_paths_contract(viewer_obj: Any, paths: Sequence[str]) -> None:
    """Implementation of open_paths(paths: list[str]) -> None for MainViewer.

    Treats passed paths as a transient input view without mutating persisted settings.
    """
    valid_paths = [
        os.path.realpath(os.path.abspath(p))
        for p in paths
        if os.path.exists(p) and os.path.isfile(p) and is_supported_image(p)
    ]

    if hasattr(viewer_obj, "loader") and viewer_obj.loader is not None:
        viewer_obj.loader.clear_tasks()

    if hasattr(viewer_obj, "clear_pixmap_cache"):
        viewer_obj.clear_pixmap_cache()

    viewer_obj.load_generation = getattr(viewer_obj, "load_generation", 0) + 1
    if hasattr(viewer_obj, "pending_ops") and isinstance(viewer_obj.pending_ops, dict):
        viewer_obj.pending_ops.clear()

    viewer_obj._transient_view = True
    viewer_obj.images = valid_paths

    if valid_paths:
        viewer_obj.current_index = 0
        if hasattr(viewer_obj, "show_image"):
            viewer_obj.show_image()
    else:
        viewer_obj.current_index = -1
        if hasattr(viewer_obj, "viewer"):
            viewer_obj.viewer.hide()
        if hasattr(viewer_obj, "empty_label"):
            viewer_obj.empty_label.show()
            viewer_obj.empty_label.setText("No valid images provided in launch arguments.")


def patch_main_viewer_launch_contract() -> None:
    """Extends MainViewer with initial_paths support and open_paths contract if not already present."""
    try:
        from .ui_main import MainViewer
    except ImportError:
        from imagesorter.ui_main import MainViewer  # type: ignore

    if hasattr(MainViewer, "_launch_contract_patched") and MainViewer._launch_contract_patched:
        return

    orig_init: Callable[..., None] = MainViewer.__init__

    def patched_init(
        self: Any,
        settings_manager: SettingsManager,
        initial_paths: Sequence[str] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        orig_init(self, settings_manager, *args, **kwargs)
        if initial_paths is not None and len(initial_paths) > 0:
            self.open_paths(initial_paths)

    def open_paths_method(self: Any, paths: Sequence[str]) -> None:
        open_paths_contract(self, paths)

    MainViewer.__init__ = patched_init  # type: ignore[method-assign]
    MainViewer.open_paths = open_paths_method  # type: ignore[attr-defined]
    MainViewer._launch_contract_patched = True  # type: ignore[attr-defined]


# Automatically apply launch contract patch on module import
patch_main_viewer_launch_contract()

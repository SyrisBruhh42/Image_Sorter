"""Pure helpers for parsing image paths supplied on the command line."""

from __future__ import annotations

import os
from collections.abc import Sequence

CORE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tiff",
    ".tif",
)

OPTIONAL_EXTENSIONS = (
    ".heic",
    ".heif",
    ".avif",
    ".cr2",
    ".nef",
    ".arw",
    ".dng",
    ".orf",
    ".rw2",
    ".pef",
    ".raf",
    ".srw",
)

SUPPORTED_EXTENSIONS = CORE_EXTENSIONS + OPTIONAL_EXTENSIONS


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

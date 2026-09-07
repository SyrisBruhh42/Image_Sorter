"""Canonical capability registry for future opt-in component packs.

This module intentionally performs no downloads or package installation. It gives the
core sorter stable IDs, locations, and honest unsupported-format diagnostics while the
transactional component manager is developed independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PyQt6.QtGui import QImageReader

from .paths import get_component_cache_dir, get_components_dir


class ComponentType(str, Enum):
    FORMAT_CODEC = "format_codec"
    VIEWER_CAPABILITY = "viewer_capability"
    AI_MODEL = "ai_model"
    HARDWARE_PROVIDER = "hardware_provider"


@dataclass(frozen=True)
class ComponentDescriptor:
    component_id: str
    component_type: ComponentType
    display_name: str
    capabilities: tuple[str, ...]
    optional: bool = True


CATALOG: tuple[ComponentDescriptor, ...] = (
    ComponentDescriptor(
        "codec.heif-avif",
        ComponentType.FORMAT_CODEC,
        "HEIF / HEIC / AVIF codec pack",
        ("decode:heic", "decode:heif", "decode:avif"),
    ),
    ComponentDescriptor(
        "codec.camera-raw",
        ComponentType.FORMAT_CODEC,
        "Camera RAW codec pack",
        tuple(
            f"decode:{extension}"
            for extension in (
                "cr2",
                "nef",
                "arw",
                "dng",
                "orf",
                "rw2",
                "pef",
                "raf",
                "srw",
            )
        ),
    ),
    ComponentDescriptor(
        "viewer.animation-multipage",
        ComponentType.VIEWER_CAPABILITY,
        "Animation and multipage viewer",
        ("animate:gif", "animate:apng", "animate:webp", "pages:tiff"),
    ),
    ComponentDescriptor(
        "ai.mobilenet-v2",
        ComponentType.AI_MODEL,
        "MobileNetV2 model and labels",
        ("tag:imagenet",),
    ),
    ComponentDescriptor(
        "provider.onnx-nvidia",
        ComponentType.HARDWARE_PROVIDER,
        "NVIDIA ONNX Runtime provider",
        ("inference:cuda",),
    ),
)

_BY_CAPABILITY = {
    capability: descriptor
    for descriptor in CATALOG
    for capability in descriptor.capabilities
}


def component_for_extension(extension: str) -> ComponentDescriptor | None:
    """Return the curated optional component associated with an extension."""
    return _BY_CAPABILITY.get(f"decode:{extension.lower().lstrip('.')}")


def component_install_dir(component_id: str, version: str) -> Path:
    """Resolve an inactive/active version location without creating it."""
    return get_components_dir() / component_id / version


def component_staging_dir(component_id: str) -> Path:
    """Resolve download staging separately from activated component versions."""
    return get_component_cache_dir() / component_id


def qt_decode_capabilities() -> frozenset[str]:
    """Report actual decoder plugins available to this Qt runtime."""
    return frozenset(
        bytes(item).decode("ascii", errors="ignore").lower()
        for item in QImageReader.supportedImageFormats()
    )


def unsupported_format_message(extension: str) -> str:
    """Return an actionable message without claiming a component is installable."""
    normalized = extension.lower().lstrip(".")
    component = component_for_extension(normalized)
    if component:
        return (
            f"{normalized.upper()} requires the optional {component.display_name}, "
            "which is not installed in this build. Common-format sorting remains available."
        )
    return f"No installed decoder can read {normalized.upper()} images."

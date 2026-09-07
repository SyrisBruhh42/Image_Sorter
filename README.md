# Image Sorter

Keyboard-first desktop image review and sorting for Linux, built with Python and
PyQt6. Ubuntu 24.04 LTS with KDE Plasma/X11 is the primary target. Windows and
macOS currently receive experimental smoke coverage.

[![CI](https://github.com/SyrisBruhh42/Image_Sorter/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SyrisBruhh42/Image_Sorter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

## Current capabilities

- Open one or more images or scan a selected directory.
- Review with keyboard navigation, zoom, pan, a compact HUD, clipping warnings,
  zen mode, accessible labels, themes, and configurable destination hotkeys.
- Move, copy, or send an image to a custom staging trash or the system trash.
- Preserve matching `.txt` sidecars through file operations.
- Undo application-managed move, copy, and custom-trash operations with strict
  identity/provenance checks. System-trash operations are intentionally not
  advertised as application-undoable.
- Recover conservatively from interrupted application-managed operations using a
  bounded SQLite journal. Ambiguous state is preserved for manual recovery.
- Optionally write merged tags to JPEG EXIF XPKeywords and/or `.txt` sidecars.
- Optionally download and run a pinned MobileNetV2 ONNX model. Model weights and
  labels are not bundled and no first-run download occurs.

The file-operation safeguards reduce accidental loss, but this is beta software.
Use test copies until it has been qualified with your own filesystem and backup
workflow.

## Format and component status

The core scanner recognizes JPEG, PNG, WebP, BMP, GIF, and TIFF. Actual decoding
depends on the Qt image plugins available in the installed build. GIF/APNG/WebP
animation and multipage TIFF presentation are not implemented yet; currently the
viewer shows a decoded still frame.

HEIF/HEIC/AVIF, camera RAW, animation/multipage support, optional AI packs, and
hardware-provider packs have stable capability identifiers and separate data/cache
locations. A unified Download & Component Manager is planned but is not yet an
installer. Unsupported optional formats produce an actionable diagnostic instead
of being silently omitted.

## Install for development

Prerequisites are Python 3.10 or newer and the Qt runtime libraries required by
your display backend. On Ubuntu 24.04:

```bash
sudo apt update
sudo apt install -y python3-venv libegl1 libgl1 libxcb-cursor0 libdbus-1-3
git clone https://github.com/SyrisBruhh42/Image_Sorter.git
cd Image_Sorter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Launch it with a configured source directory, an image, or a directory:

```bash
imagesorter
imagesorter /path/to/photo.jpg
imagesorter /path/to/folder
```

The application does not force Wayland or X11. Qt uses the active desktop session.

## Keyboard controls

| Action | Default key |
| --- | --- |
| Next / previous | `D`, `Right`, or `Space` / `A`, `Left`, or `Backspace` |
| Custom move or copy | Configured single-character hotkey |
| Custom/system trash | `X` or `Delete` |
| Undo application-managed operation | `Ctrl+Z` |
| Settings | `S` or `Ctrl+S` |
| Reload source | `R` |
| Clipping warning overlay | `C` |
| Lock zoom | `L` |
| Zen mode | `Z` |
| Copy current path | `Ctrl+C` |
| Exit / leave fullscreen or zen mode | `Esc` |

A configured custom single-character hotkey takes precedence over the fallback
letter action. Modifier shortcuts, arrows, `Space`, `Backspace`, and `Delete` keep
their fixed behavior.

## Optional local AI

Open Settings → AI & Metadata and choose **Download Model**. The downloader uses
pinned HTTPS URLs, writes to a temporary file, verifies SHA-256, and atomically
activates the model and label files under the application data directory. AI is
off by default and ordinary image review remains available without those files.

The standard Python dependency set currently includes the CPU ONNX Runtime. GPU
provider packs are future optional components; the application only selects an
accelerated provider already available in its runtime and falls back to CPU.

## Build and test

```bash
./scripts/test_headless.sh -m "not packaging" -q tests
source .venv/bin/activate
ruff check src tests scripts build_desktop.py
python build_desktop.py
```

`python build_desktop.py` creates a PyInstaller onedir build under
`dist/ImageSorter/`. On Linux x86_64, `python build_desktop.py --appimage`
additionally requires a verified
AppImage. The build script downloads only the pinned `appimagetool` 1.9.1 binary,
verifies its digest, and fails if the requested artifact is absent.

Ubuntu 24.04 may require `libfuse2t64` to mount an AppImage. Extraction is the
FUSE-free fallback:

```bash
./dist/ImageSorter-x86_64.AppImage --appimage-extract
./squashfs-root/AppRun
```

See [Architecture](docs/architecture.md),
[Contributing](CONTRIBUTING.md), and the
[KDE acceptance matrix](docs/engineering/platform-acceptance.md) for scope and
verification details.

## License

MIT. See [LICENSE](LICENSE).

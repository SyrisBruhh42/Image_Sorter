# Image Sorter

Keyboard-first desktop image review and sorting for Linux, built with Python and
PyQt6. Ubuntu 24.04 LTS with KDE Plasma/X11 is the primary target. Windows and
macOS currently receive experimental smoke coverage.

[![CI](https://github.com/SyrisBruhh42/Image_Sorter/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SyrisBruhh42/Image_Sorter/actions/workflows/ci.yml)
[![Source: MIT](https://img.shields.io/badge/Source-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

## Current capabilities

This beta source integrates the local safety, recovery, viewer and optional-component
improvements. Source integration and downloadable releases have separate gates:
this update publishes source only. Full native desktop and binary distribution
qualification remain incomplete. See [source integration](docs/engineering/source-integration.md)
and the [qualification history](docs/engineering/integration-status.md).

- Open one or more images or scan a selected directory.
- Review with keyboard navigation, zoom, pan, a compact HUD, clipping warnings,
  zen mode, accessible labels, themes, and configurable destination hotkeys.
- Move, copy, or send an image to a custom staging trash or the system trash.
- Preserve image-qualified sidecars (`image.jpg.txt`, not `image.txt`) through
  file operations, without adopting unrelated destination sidecars.
- Undo application-managed move, copy, and custom-trash operations with strict
  identity/provenance checks. System-trash operations are intentionally not
  advertised as application-undoable.
- Recover conservatively from interrupted operations using a durable SQLite
  journal and versioned file-set manifests. Unresolved material is retained;
  journal replay is paginated rather than deleting safety history to bound it.
- Optionally write merged tags to JPEG EXIF XPKeywords and/or `.txt` sidecars.
- Optionally import and run a pinned MobileNetV2 ONNX model. Model weights and
  labels are not bundled; online component downloads are unavailable in this
  source-only delivery. No first-run download occurs.
- Keep a moved image visible when Auto Advance is off. Its held preview is read-only;
  Next/Previous resumes the source queue, and Undo restores normal interaction.
- Keep the current folder independent of operations that finish after a folder change.

The file-operation safeguards reduce accidental loss, but this is beta software.
Use test copies until it has been qualified with your own filesystem and backup
workflow.

## Format and component status

The base scanner recognizes JPEG, PNG/APNG, WebP, BMP, GIF and TIFF. Base decoding
depends on installed Qt plugins. Settings → Components manages these isolated,
opt-in packs; only catalogued, platform-compatible versions can be activated:

| Component | Behavior |
| --- | --- |
| `ai.mobilenet-v2` | Hash-verified model and labels through pack or verified legacy import; isolated CPU inference. |
| `provider.onnx-nvidia` | Separate CUDA/cuDNN runtime, actual compute probe and one explicit CPU fallback; never replaces the driver. |
| `codec.heif-avif` | Decoder-only HEIF/HEIC and Pillow AVIF, with orientation, transparency, color conversion and resource bounds. |
| `codec.camera-raw` | CR2, NEF, ARW, DNG, ORF, RW2, PEF, RAF and SRW previews through LibRaw; camera-specific unsupported variants receive errors. |
| `viewer.animation-multipage` | GIF/APNG/WebP animation and TIFF pages; initially paused, with playback, stepping, seeking and loop controls. |

Install, cancel, enable/disable, verify, update, rollback, remove and interrupted
installation recovery are explicit actions. Opening the application or Components
does not access the network. Failed updates preserve the working version; active
readers hold removal leases. All five catalogue descriptors remain available, with
unchanged archive identities, but online Install/Update is disabled until reviewed
release assets exist. Use **Import pack…** or **Import existing model…** for verified
local inputs. Installed compatible packs remain usable. A fresh source clone does
not include these optional binaries or depend on another machine's profile.

## Clean local installation

Prerequisites are Python 3.10 or newer and the Qt runtime libraries required by
your display backend. On Ubuntu 24.04:

```bash
sudo apt update
sudo apt install -y python3-venv libegl1 libgl1 libxcb-cursor0 libdbus-1-3
git clone https://github.com/SyrisBruhh42/Image_Sorter.git
cd Image_Sorter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

For development and tests, use `python -m pip install -e ".[dev]"` instead.
Neither installation includes optional component packs or model weights.

Launch it with a configured source directory, an image, or a directory:

```bash
imagesorter
imagesorter /path/to/photo.jpg
imagesorter /path/to/folder
imagesorter --profile-root /absolute/private/test-profile /path/to/photo.jpg
```

The application does not force Wayland or X11. Qt uses the active desktop session.
An explicit profile is processed before application logging or settings imports;
its configuration, data, cache, state, components, journal and mutation runtime
must remain inside that profile. Invalid isolation fails instead of falling back.

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

Open Settings → Components and import a checksum-verified model pack or existing
pinned model/label directory. Enable that component, then enable AI in AI & Metadata.
AI is off by default; ordinary image review works without a model. The confidence
control ranges from 0 to 1 (default 0.5); the active component pipeline returns up
to ten labels with scores greater than or equal to the threshold. Classification
runs after move/copy, with the primary transfer committed before enrichment.

The retained legacy `model_path` setting is deprecated and does not select arbitrary
models. Model selection uses the verified component store. The image-details AI
option displays activity status, not a persistent tag editor or tag browser.
Catalogue versions, prerequisites and the historical proposed download locations
are documented in [source integration](docs/engineering/source-integration.md).

The base Python dependency set includes CPU ONNX Runtime. GPU libraries live only
in the separate provider pack. Actual provider, fallback and component/model
identities accompany inference results; provider enumeration alone is not a
successful CUDA test. Primary sorting is committed before optional enrichment;
enrichment is a separate journalled child operation.

Redistribution of the original pinned model and labels is not yet established.
The current local qualification packs must not be published until that gate is
resolved. See [licensing and exact source materials](docs/licensing.md).

## Recovery and filesystem boundaries

One OS-locked mutation service owns a profile. GUI closure cancels readers but
does not kill a writer to meet a timer: a writer that must settle remains
`drain_pending` in its pinned independent runtime. Recovery uses journalled
ownership, never a matching name. System trash submits a recoverable directory
containing the whole image/sidecar set; native trash review remains necessary and
application Undo is not advertised for it.

Original claims and rollback material are retained. Unresolved records are never
aged out automatically. Resolved material becomes eligible for explicit,
identity-checked cleanup after 30 days; cleaning the last proven pre-sort copy
can intentionally make later Undo unavailable. Recovery storage above 10 GiB
triggers a warning; low space pauses work without reclaiming recovery bytes.

File hashes do not exclude every race with external programs holding open file
descriptors. Do not edit the same collection concurrently; use a local filesystem
with working no-clobber publication, locking and directory/file sync semantics.
Unsupported durability or metadata preservation produces an actionable failure
with source/recovery bytes retained. VM power-interruption evidence is distinct
from ordinary process-crash tests and does not certify physical drive/controller
power-loss behavior.

## Build and test

```bash
./scripts/test_headless.sh -m "not packaging" -q tests
source .venv/bin/activate
ruff check src tests scripts build_desktop.py
python build_desktop.py
```

`python build_desktop.py` creates a PyInstaller onedir build under
`dist/ImageSorter/`. On Linux x86_64, `python build_desktop.py --appimage`
also builds and verifies an AppImage. The build script downloads the pinned
`appimagetool` 1.9.1 and AppImage runtime inputs, verifies their digests, and fails
if the requested artifact is absent. These commands build locally; they publish
no artifacts.

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

The project's own source is MIT; see [LICENSE](LICENSE). Frozen binaries include
GPL-licensed PyQt and other dependencies and are not MIT-only distributions.
Their applicable texts and corresponding source must accompany the exact
artifacts. See [distribution and relinking requirements](docs/licensing.md).

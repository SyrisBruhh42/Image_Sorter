# KDE Plasma Acceptance Matrix — Ubuntu 24.04 LTS

This matrix is the native-desktop gate for the current Linux-first development
baseline. Automated offscreen and Xvfb tests support it, but do not replace it.

## Target profile

| Item | Target |
| --- | --- |
| Distribution | Ubuntu 24.04.4 LTS (Noble), x86_64 |
| Desktop | KDE Plasma 5.27.12 |
| Window manager/session | KWin 5.27.11, X11 |
| Display manager | SDDM 0.20.0 |
| GPU | NVIDIA GeForce RTX 4070 SUPER |
| Application version | `0.1.0.dev0` |
| Native status | **NOT RUN** for the unified candidate |

Record the source commit, package type, Qt/PyQt versions, NVIDIA driver, screen
layout, scale factor, and result when executing this matrix. Never convert a
`NOT RUN` cell to `PASS` based only on CI or code inspection.

## Disposable fixture setup

Run acceptance tests only against disposable files:

```bash
fixture_root="$(mktemp -d)"
mkdir -p "$fixture_root/source" "$fixture_root/destination" "$fixture_root/trash"
python - "$fixture_root/source" <<'PY'
from pathlib import Path
import sys
from PIL import Image

root = Path(sys.argv[1])
for index, color in enumerate(("red", "green", "blue"), start=1):
    image = root / f"sample-{index}.jpg"
    Image.new("RGB", (1600, 1000), color).save(image, quality=90)
    image.with_suffix(".txt").write_text(f"human note {index}\n", encoding="utf-8")
PY
```

Create a separate application profile for the run:

```bash
export XDG_CONFIG_HOME="$fixture_root/xdg/config"
export XDG_DATA_HOME="$fixture_root/xdg/data"
export XDG_CACHE_HOME="$fixture_root/xdg/cache"
export XDG_STATE_HOME="$fixture_root/xdg/state"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME"
```

Remove `fixture_root` only after reviewing results and copying any diagnostic log
you want to retain.

## Acceptance cases

| ID | Area | Procedure | Required result | Status |
| --- | --- | --- | --- | --- |
| KDE-01 | Startup | Launch the wheel command, onedir executable, and extracted AppImage from a directory outside the repository. | Each opens without importing source-tree files; supplied file/folder opens. | NOT RUN |
| KDE-02 | Desktop | Launch the AppImage normally and via Dolphin “Open With.” | Icon/desktop identity is correct; `%F` paths arrive in order; no forced Wayland backend. | NOT RUN |
| KDE-03 | Navigation | Navigate rapidly with keyboard, zoom/pan, lock zoom, toggle clipping and zen mode. | UI remains responsive; only the current generation is displayed; no stale image flash. | NOT RUN |
| KDE-04 | Custom hotkeys | Configure move and copy keys to the disposable destination and exercise auto-advance. | Exactly one operation occurs per key; fixed/modifier shortcut precedence matches README. | NOT RUN |
| KDE-05 | File set | Move/copy/trash an image that has a `.txt` sidecar, including a destination-name collision. | Image and sidecar remain paired; unique destination chosen; no overwrite or orphan. | NOT RUN |
| KDE-06 | Undo | Undo move, copy, and custom-trash; then alter a destination and try Undo again. | Valid operations reverse; altered target is refused without deleting either copy. | NOT RUN |
| KDE-07 | Failure | Make a destination unwritable or disconnect a disposable mounted destination during an operation. | Failure is visible; source set remains intact; no false success. | NOT RUN |
| KDE-08 | Recovery | Terminate the app during a disposable operation and relaunch twice. | Proven temporary artifacts reconcile once; ambiguous files remain with `RECOVERY_REQUIRED`; second launch is idempotent. | NOT RUN |
| KDE-09 | Settings | Save settings, corrupt the isolated settings JSON, then relaunch. | Corrupt file is uniquely backed up; validated defaults load; actual user profile is untouched. | NOT RUN |
| KDE-10 | Optional AI | Confirm no model files/network activity on first launch; explicitly download; tag a fixture with CPU and any installed NVIDIA provider. | Base app works without model; verified files activate only after request; provider and fallback are reported honestly. | NOT RUN |
| KDE-11 | Formats | Open JPEG/PNG/WebP/BMP/GIF/TIFF plus HEIC and one camera RAW sample if available. | Core stills decode where Qt supports them; animation/multipage stays still; unavailable optional formats show the component diagnostic. | NOT RUN |
| KDE-12 | Display | Exercise normal/maximized/fullscreen at 100% and any routinely used fractional scale; move across monitors if applicable. | Controls remain visible and keyboard focus is clear; no blocking dialog appears behind the main window. | NOT RUN |
| KDE-13 | Accessibility | Navigate menus/settings with keyboard, inspect focus, tooltips, names, dark/high-contrast themes, and large font. | Every interactive control remains operable and labeled; focus is visible. | NOT RUN |
| KDE-14 | Shutdown | Close during decode, model validation/download, and queued file work. | Close is bounded and responsive; committed results are reported/recovered; no indefinite wait. | NOT RUN |

## Evidence record

For each failure, record exact reproduction steps and whether the source image and
sidecar hashes changed. Attach `scripts/diagnose_linux_p07.py --json` output after
reviewing it for privacy, plus application logs from the isolated state directory.

Acceptance is complete only when KDE-01 through KDE-09 and KDE-12 through KDE-14
pass. KDE-10 and optional-format portions of KDE-11 may remain explicitly skipped
when the corresponding optional components are not installed; the base behavior
must still pass.

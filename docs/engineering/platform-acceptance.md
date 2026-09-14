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
    Path(str(image) + ".txt").write_text(f"human note {index}\n", encoding="utf-8")
PY
```

Create a separate application profile for the run. Every entry point must carry
`--profile-root "$fixture_root/profile"`; shell exports alone do **not** isolate a
launch originating in an already running Dolphin process. Explicit profiles are
resolved before settings, logs, components, journal recovery, or GUI imports and
fail closed if a path escapes or cannot be written. They override portable mode.

```bash
export XDG_CONFIG_HOME="$fixture_root/profile/config"
export XDG_DATA_HOME="$fixture_root/profile/data"
export XDG_CACHE_HOME="$fixture_root/profile/cache"
export XDG_STATE_HOME="$fixture_root/profile/state"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME"
```

Do not remove `fixture_root` until the independently draining mutation owner has
actually stopped, all operation IDs have terminal/recoverable receipts, and
evidence and preserved file claims have been reviewed. A GUI close does not imply
that a kernel-stalled file writer has stopped. Never force-kill that writer merely
to meet a shutdown timer.

## Installed-artifact readiness and first launch

`scripts/native_acceptance.py` accepts the installed wheel command, onedir binary,
actual AppImage, or extracted `squashfs-root/AppRun`. Run from the build tooling
environment (Pillow is required for disposable fixtures); it removes source-tree
Python and Qt search-path overrides from the launched application.

```bash
python scripts/native_acceptance.py --backend xcb --kind onedir \
  --artifact /absolute/path/dist/ImageSorter/ImageSorter \
  --source-sha FULL_COMMIT_SHA --source-tree FULL_TREE_SHA \
  --output /absolute/path/new-evidence-directory
```

Repeat with `--kind wheel` and an independently installed non-editable wheel entry
point, and `--kind appimage` for both normal and extracted routes. CI uses the same
command with `--backend offscreen`; this is readiness evidence, never KDE/X11
acceptance. An exit code of 124 from a generic timeout is never evidence of a
usable application. Source identity arguments must agree with the build record.

The runner requires `strace`, follows descendants (including independently owned
mutation processes), records the real painted image/backend/module origin, and
observes 30 seconds after first paint by default. It counts process-attributed
IPv4/IPv6 socket/connect/send attempts, including failed attempts, separately from
local Unix sockets, and reports model-file open attempts and profile model files.
Missing trace/readiness, source-tree imports, changed fixtures, nonzero egress,
pending processes or absent shutdown receipts fail the smoke. Observation windows
shortened for tests are explicitly recorded and do not satisfy the 30-second gate.
Strace observes process syscalls; it is not an enforceable network sandbox or a
claim that unrelated desktop daemons did not use the network.

Each output contains `smoke.json`, hashed traces/logs, a diagnostic receipt, correct
`sample-1.jpg.txt` sidecar, and a disposable `.desktop` launcher whose Exec carries
the explicit profile and `%F`. For Dolphin acceptance, create a separate launcher
from that template with a fresh explicit profile and receipt path, then record
its exact bytes before use. Reusing a completed smoke launcher would overwrite
its already-hashed readiness receipt. Do not register either launcher as a
real-user default application. If that GUI route is not traced, its no-network
status remains unverified rather than inherited from CLI.

## Acceptance cases

### Enumerated diagnostic scenarios

The shipped application accepts `--diagnostic-scenario NAME` only with an explicit
private `--profile-root`. `core` generates its own image/sidecar fixtures and runs
navigation, paired file operations/Undo/failure checks, and Settings/Components
focus checks using Qt events. Additional fixed names are `hotkeys`, `recovery`,
`settings-corrupt`, `settings-relaunch`, `frames`, `optional-ai-cpu`, and
`optional-ai-gpu`. No arbitrary command or Python expression is accepted.

```bash
/absolute/path/ImageSorter --profile-root /absolute/path/new-private-profile \
  --diagnostic-receipt /absolute/path/new-private-profile/state/diagnostics.json \
  --diagnostic-scenario core
```

The diagnostic refuses an unmarked existing settings/journal profile. Every file
operation is confined to fixtures generated inside that run. `recovery` requires
a fresh journal and starts a fixed fixture process that exits at the first durable
publication boundary; the GUI then exercises record selection, explicit Yes
confirmation, service-owned rollback, paired hash verification and inventory
refresh. This process-crash check does **not** replace VM power-loss qualification.
Legacy and system-trash ambiguous records are read-only/manual review only.

Run `settings-corrupt` followed by `settings-relaunch` in the same marked profile
to observe actual backup/relaunch behavior. Optional scenarios require previously
explicitly installed packs; missing packs report NOT RUN, never install implicitly.
Reports and widget screenshots are under `PROFILE/native-diagnostics/RUN_ID/` and
always set `complete:false`. Offscreen output is not native evidence. Actual
Dolphin selection/order, physical scale/monitor changes, assistive technology and
visual readability still require attributed observations under the strict schema.

`scripts/native_drain_probe.py --artifact /absolute/path/ImageSorter.AppImage
--output /absolute/path/new-evidence --backend xcb` holds a second authenticated
service client, closes the actual GUI, observes AppImage mount disappearance,
performs a paired copy through the independently pinned journal owner **after**
GUI exit, then disconnects and observes owner exit. This proves the mount/runtime
lifetime boundary without killing the writer; it is partial evidence only.

Unprivileged `strace` can suppress the setuid privileges needed by `fusermount3`.
If tracing prevents normal AppImage mount, retain that measurement failure and
use appropriately authorized privileged process tracing for the normal route.
Do not silently substitute extracted AppRun evidence for the normal mount route.

Where native authorization has explicitly been approved, the normal AppImage
runner accepts `--privileged-trace`. The desktop authorization agent elevates
only the system observer; `strace -u` launches the application as the caller,
whose real/effective/saved/filesystem UIDs are measured. The observer writes a
multiplexed stream only to an already-open descriptor; the unprivileged runner
retains that raw stream and derives per-PID records. It does not grant the
observer root pathname writes in a caller-writable profile, modify global
tracing policy, or make application features require elevation. Cancelled or
denied authorization is explicitly not qualified. Normal untraced launch and
the separate unprivileged extracted-AppImage smoke remain available, but do not
fill the normal-mount egress evidence gap.

Cache entries bind source device/inode/size/mtime/ctime, active decoder version,
frame/page and preview size. Late decoded replies must match both the request and
current identity. A preview larger than the cache budget may be displayed as the
single current image but is never admitted to the cache. Local identity metadata
lookups are not a guarantee against a kernel-stalled remote filesystem lookup.
Playback prefetches at most eight frames per helper call and 64 MiB per batch,
within the ordinary cache byte/item cap. Cold buffering is explicit and timed
separately; ready-frame clocks use a precise timer without a new helper per
frame. Insufficient cache space pauses playback visibly rather than repeatedly
decoding an oversized page. Manual step/seek remains available. Finite loop and
current-frame duration metadata survive ordinary cache eviction without an
unbounded metadata collection. Catalogue validation is reused by profile and
catalogue identity, while live activation and source identity are still checked.

The reader retains raw `loop_count` for inspection and supplies normalized
`total_plays` to playback: zero means infinite and a positive value is the total
number of presentations. A positive GIF loop count means repeats after the first
presentation; APNG/WebP counts already mean total plays. Absent animation loop
metadata and TIFF pages default to one presentation. The native generated
raw-count-one fixtures independently require one GIF wrap and zero APNG/WebP/TIFF
wraps with continuous looping off. Do not derive expected wraps from the same
reader field being tested. These distinctions follow the
[APNG play-count definition](https://www.w3.org/TR/png-3/#acTL-chunk) and
[WebP playback procedure](https://developers.google.com/speed/webp/docs/riff_container#animation).

Optional enrichment has a bounded 195-second outer budget for two independently
bounded CPU/GPU attempts plus snapshot/transport overhead. It never delays the
already committed primary operation or the GUI's close deadline. Cancelling the
read-only process tree retains the unchanged one/three-second cancellation and
reaping targets; file mutation owners are never killed to meet those deadlines.

| ID | Area | Procedure | Required result | Status |
| --- | --- | --- | --- | --- |
| KDE-01 | Startup | Launch the wheel command, onedir executable, and extracted AppImage from a directory outside the repository. | Each opens without importing source-tree files; supplied file/folder opens. | NOT RUN |
| KDE-02 | Desktop | Launch the AppImage normally and via Dolphin “Open With.” | Icon/desktop identity is correct; `%F` paths arrive in order; no forced Wayland backend. | NOT RUN |
| KDE-03 | Navigation | Navigate rapidly with keyboard, zoom/pan, lock zoom, toggle clipping and zen mode. | UI remains responsive; only the current generation is displayed; no stale image flash. | NOT RUN |
| KDE-04 | Custom hotkeys | Configure move and copy keys to the disposable destination and exercise auto-advance. | Exactly one operation occurs per key; fixed/modifier shortcut precedence matches README. | NOT RUN |
| KDE-05 | File set | Move/copy/trash an image that has a `.txt` sidecar, including a destination-name collision. | Image and sidecar remain paired; unique destination chosen; no overwrite or orphan. | NOT RUN |
| KDE-06 | Undo | Undo move, copy, and custom-trash; then alter a destination and try Undo again. | Valid operations reverse; altered target is refused without deleting either copy. | NOT RUN |
| KDE-07 | Failure | Make a destination unwritable or disconnect a disposable mounted destination during an operation. | Failure is visible; source set remains intact; no false success. | NOT RUN |
| KDE-08 | Recovery | Close GUI during a disposable operation; separately simulate a mutation-owner crash on disposable files, then relaunch twice. | GUI disconnect allows safe drain; kernel ownership prevents competing recovery; ambiguous claims remain with `RECOVERY_REQUIRED`; records are visible and second launch is idempotent. | NOT RUN |
| KDE-09 | Settings | Save settings, corrupt the isolated settings JSON, then relaunch. | Corrupt file is uniquely backed up; validated defaults load; actual user profile is untouched. | NOT RUN |
| KDE-10 | Optional AI | Confirm no model files/network activity on first launch; explicitly download; tag a fixture with CPU and any installed NVIDIA provider. | Base app works without model; verified files activate only after request; provider and fallback are reported honestly. | NOT RUN |
| KDE-11 | Formats | Open core stills without packs; then explicitly install and qualify HEIC/HEIF/AVIF, every declared RAW family, GIF/APNG/WebP animation and multipage TIFF. | Missing packs give diagnostics; installed formats decode; animation starts paused and step/seek/play/pause/loop preserve viewport and discard stale frames. | NOT RUN |
| KDE-12 | Display | Exercise normal/maximized/fullscreen at 100% and any routinely used fractional scale; move across monitors if applicable. | Controls remain visible and keyboard focus is clear; no blocking dialog appears behind the main window. | NOT RUN |
| KDE-13 | Accessibility | Navigate menus/settings with keyboard, inspect focus, tooltips, names, dark/high-contrast themes, and large font. | Every interactive control remains operable and labeled; focus is visible. | NOT RUN |
| KDE-14 | Shutdown | Close during stalled read/provider/model validation/download and queued file work; inspect all recorded PIDs. | GUI close target ≤5s, read-only subprocess reaping ≤3s; mutator drains at safe boundaries, never killed; pending state is honest. Full PASS requires its eventual exit plus durable replay. | NOT RUN |

## Evidence record

For each failure, record exact reproduction steps and whether the source image and
sidecar hashes changed. Attach `scripts/diagnose_linux_p07.py --json` output after
reviewing it for privacy, plus application logs from the isolated state directory.

## Full optional profile and sealed qualification

The approved cutover requires **all** KDE-01 through KDE-14 for each wheel,
onedir, and AppImage, plus these component case IDs for each artifact:

- `COMPONENT:codec.heif-avif`: HEIC/HEIF/AVIF fixtures, orientation/color/alpha,
  malformed input, uninstall diagnostic, reinstall and rollback.
- `COMPONENT:codec.camera-raw`: every advertised camera family with provenance and
  expected dimensions/color, large/malformed input, failure confinement.
- `COMPONENT:viewer.animation-multipage`: GIF/APNG/WebP/TIFF exact frame/page,
  delays, finite/infinite looping, paused launch, seek, viewport, rapid switching.
- `COMPONENT:ai.mobilenet-v2`: explicit installation only, CPU output from pinned
  tensors/model/labels, isolated inference, separate metadata-child failure/Undo,
  cancellation, uninstall and verified rollback.
- `COMPONENT:provider.onnx-nvidia`: actual RTX target CUDA compute events (provider
  enumeration alone is insufficient), matching input tensor hash and declared
  CPU/GPU tolerance, GPU memory/time, forced unavailable-provider CPU fallback
  and visible reason, cancellation, uninstall/reinstall/rollback in all packages.

Optional means never required for base launch and never silently downloaded.
It does not mean optional evidence may be skipped in the approved **full** profile.
Each case records fixture hashes, component manifest hashes, actual result,
procedure, observed environment, performance measurements where relevant, and
hashed evidence. Missing/prohibited hardware, formats or desktop routes remain
NOT RUN/BLOCKED; no silent skip can produce cutover success.

The smoke always emits `complete:false`. Build an operator-authored schema-1
matrix using the [strict evidence schema](qualification-evidence.md) enforced by
`scripts/cutover.py:verify_qualification` and `scripts/qualification_schema.py`:
`profile_id:linux-x86_64-full`, source SHA/tree, all three artifact file hashes,
`native:true`, `display_backend:xcb`, `session_type:x11`, `desktop:KDE`, installed
component manifests, and unique `{id,artifact_kind,status,evidence}` cases.
Only after every case passes, all processes stop, and the measured base egress
attempt count is zero may the matrix set `complete:true`.

`native_acceptance.py aggregate --matrix MATRIX.json --source-sha SHA
--source-tree TREE --component ID` (repeat for all five IDs) `--output FINAL.json`
validates all required IDs/statuses, attributed operator observations, clean build
inventories, raw painted-image/process/egress telemetry, and every referenced
file hash, then seals a
new copy with absolute evidence paths. It never fills in missing results or
relabels offscreen evidence. The cutover controller independently validates it.

The batch regression budget is explicitly 60 seconds for each 200-operation
move/Undo batch. This replaces the previous eight-thread throughput assumption
with serialized durable transactions and retained recovery claims. The test logs
elapsed time, checks event-loop responsiveness, and retains every operation-count,
content and Undo assertion. It does not change the ≤5-second GUI close or
≤3-second read-only process reaping targets.

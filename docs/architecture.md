# Architecture

Image Sorter keeps UI state, decoding, file mutation, settings, and optional AI
behind separate contracts so slow or optional work does not block the Qt GUI
thread.

## Runtime boundaries

| Area | Canonical module | Contract |
| --- | --- | --- |
| Startup | `main.py`, `launch_requests.py` | Parse paths without import-time environment mutation; construct one `QApplication` and `MainViewer`. |
| UI state | `ui_main.py` | Own view generation, pending operation IDs, Undo history, and display updates. |
| Decode | `image_loader.py`, `image_decoding.py` | Bounded asynchronous requests; exactly one terminal image/error outcome; stale generations are ignored. |
| File operations | `queue_worker.py`, `operation_contracts.py` | Bounded background tasks; one versioned terminal result and strict Undo token validation. |
| Recovery | `operation_journal.py` | WAL-backed intent/state journal; live-owner leases; conservative, idempotent restart reconciliation. |
| Metadata | `metadata_io.py` | Preserve/merge sidecar and EXIF content; stage and replace atomically; fail closed on malformed data. |
| Settings and paths | `settings_manager.py`, `paths.py` | Atomic validated settings; XDG paths on Linux; platform-native locations elsewhere; isolated fallback when unwritable. |
| Optional AI | `ai_tagger.py`, `hardware_scan.py` | Download verified model data only on request; initialize off the UI thread; use an installed provider or CPU. |
| Future components | `components.py` | Stable optional capability IDs and segregated staging/activation paths; no installation implementation yet. |

## File-operation invariants

- An image and its matching `.txt` sidecar are one operation set.
- Destination names are reserved before mutation and never overwrite an existing
  image or sidecar.
- Cross-filesystem moves copy and verify the entire set before unlinking sources.
- Destructive Undo requires a complete versioned token and verifies recorded
  provenance before removing or restoring anything.
- A journal write that fails after the filesystem commit produces a warning, not a
  false claim that the operation did not happen.
- Restart recovery removes only artifacts whose ownership can be proven. Ambiguous
  entries remain `RECOVERY_REQUIRED`.

## Optional-component direction

The intended manager will treat codecs, viewer capabilities, AI models/labels, and
hardware providers as independently selectable components. Downloads must be
explicit, versioned, digest-verified, staged outside the active directory, and
atomically activated with rollback. The base application must continue to launch
and handle common formats when every optional component is absent.

The current `components.py` registry establishes that boundary; it does not yet
claim download, dependency resolution, activation, removal, or update support.

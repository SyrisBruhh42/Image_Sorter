# Architecture

Image Sorter keeps UI state, decoding, file mutation, settings, and optional AI
behind separate contracts so slow or optional work does not block the Qt GUI
thread.

## Runtime boundaries

| Area | Canonical module | Contract |
| --- | --- | --- |
| Startup | `bootstrap.py`, `main.py`, `launch_requests.py` | Process explicit profile before settings/logging imports; validate profile containment; construct one GUI. |
| UI state | `ui_main.py` | Own view generation, pending operation IDs, Undo history, and display updates. |
| Readers | `image_loader.py`, `reader_process.py`, `reader_job.py` | Preserve viewer request/cancel facade; independently terminate slow readers; suppress stale display generations. |
| Mutation client | `queue_worker.py`, `mutation_client.py`, `worker_protocol.py` | Versioned bounded JSON, session/request identity, durable acceptance and canonical `operation_result` delivery. |
| Sole mutation owner | `mutation_service.py`, `operation_engine.py`, `profile_lock.py` | OS-backed profile lock, pinned runtime, idempotent operation IDs; serialize file changes, metadata, migration and recovery. |
| Recovery | `operation_journal.py`, `file_safety.py` | Durable versioned manifests; retained originals and quarantine claims; bounded cursor replay, not destructive history expiry. |
| Metadata | `metadata_io.py` | Preserve/merge sidecar and EXIF content; stage and replace atomically; fail closed on malformed data. |
| Settings and paths | `settings_manager.py`, `paths.py`, `bootstrap.py` | Atomic settings; platform defaults or strict explicit profile; an invalid explicit profile never falls back. |
| Optional AI | `ai_tagger.py`, `ai_preprocessing.py`, `component_runtime.py` | Shared preprocessing, immutable snapshots, isolated inference; separately journalled enrichment follows the primary commit. |
| Optional components | `component_manager.py`, `component_jobs.py`, `component_worker.py` | Shipped catalog trust root; private staging, complete inventories, self-contained helpers, active-version leases and rollback. |
| Animation | `apng_frames.py`, `ui_main.py` | Bounded frame metadata/cache; independently tested APNG composition; paused initial playback preserves zoom and pan. |

## File-operation invariants

- An image and its qualified sidecar (`image.jpg.txt`) are one operation set.
- Destination names are reserved before mutation and never overwrite an existing
  image or sidecar.
- Cross-filesystem moves retain private source claims and stage/sync the entire
  set before publication. Durable intent precedes destructive transitions.
- Destructive Undo requires a complete versioned token and verifies recorded
  provenance before removing or restoring anything.
- A failed receipt after filesystem commit cannot prove failure: preserve intent
  and bytes and reconcile the actual state before retrying.
- Restart recovery removes only artifacts whose ownership can be proven. Ambiguous
  entries remain `RECOVERY_REQUIRED`.

## Reader and writer lifetime

Reader cancellation begins immediately; termination follows at one second and
reaping is checked by three seconds. Decode and inference have 30-second bounds;
cold model validation/loading gets 60 seconds. These are independently measured
from GUI shutdown (five-second target, six-second acceptance ceiling).

Writers are not killed to satisfy UI timers. A pending writer reports
`drain_pending`; its verified runtime copy is pinned outside an AppImage mount.
Profile ownership is determined by an OS lock, not an expired heartbeat. Recovery
cannot take over a live owner. Undo settles/cancels child enrichment and uses
authoritative manifest provenance to restore the pre-sort originals.

## Optional-component trust and distribution

The source-controlled catalogue pins every archive, file, size, mode, platform,
ABI and helper version. It is never refreshed over the network at startup.
Executable components have their own runtime and do not install into the running
base application. Download/import jobs and active-version pointers have separate
durable records. Hostile or incomplete archives cannot activate, failed updates
retain the working version, and removal waits for active reader leases.

Reader inputs are private, regular-file snapshots; returned identities, dimensions,
frame metadata, lengths and hashes are validated before presentation. CPU ORT
stays in the base; CUDA/cuDNN and additional codecs remain in their packs.

The catalog may remain empty until pack qualification and distribution rights
are established. Source/binary licensing, corresponding source and exact artifact
identity are release gates; see [licensing](licensing.md) and the
[qualification evidence contract](engineering/qualification-evidence.md).

## Recovery retention and support boundary

Unresolved records/claims remain indefinitely. Explicit cleanup of resolved
material requires 30 days and verified identities/surviving bytes; retained
recovery storage above 10 GiB warns, and insufficient space pauses new work.
Cleaning the final pre-sort copy can deliberately disable later destructive Undo.

External open-descriptor writers remain a documented concurrency boundary.
Filesystem sync/locking/no-clobber and attribute support must be real; an
unsupported guarantee preserves originals and yields an actionable result.
Process crash, whole-VM power interruption and physical controller failure are
different evidence classes. Passing one does not imply another.

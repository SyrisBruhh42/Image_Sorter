# Changelog

This project follows semantic versioning once stable releases begin. The package is
currently `0.1.0.dev0`.

## Unreleased

### Source integration, 2026-09-13

- Repair hosted qualification defects: self-contained streaming hashes support
  Python 3.10; platform-path tests cover Linux, Windows and macOS with private
  profiles; asynchronous UI tests prove thread/event behavior without fixed
  machine-speed assertions.
- Refuse unsupported Windows durable sorting, Undo and metadata work before
  admission or file changes. Keep experimental image review/settings usable;
  verify real image display and refusal behavior in the required native smoke.
  Preserve POSIX directory sync and reader containment without compatibility shims.

- Preserve all original GitHub branches through a separately gated source-only
  integration into a new protected `main`; retain binary/native release gates.
- Isolate late file-operation results by view generation, keep rejected Undo
  requests in history, and ignore duplicate or older metadata token revisions.
- Honor move Auto Advance with a held read-only preview and explicit navigation;
  route Escape through one fullscreen/Zen/exit handler.
- Expose the existing AI confidence threshold and persist tooltip/AI-status controls.
- Retain five verified optional component descriptors and local imports while
  disabling unavailable online downloads. No model, runtime pack or binary is
  published by the source integration route.


### Added

- A single OS-locked mutation service with durable operation acceptance,
  versioned file-set ownership, retained rollback claims and explicit recovery.
- Independently cancellable reader processes and a pinned mutation runtime that
  can drain safely after the GUI and normal AppImage mount close.
- Strict `--profile-root` isolation before settings or logging initialization.
- An opt-in component installer with complete hash inventories, private staging,
  leases, interrupted-job reconciliation, update/rollback and offline import.
- Isolated HEIF/AVIF, LibRaw, animation/TIFF and CUDA helpers, independently
  verified APNG blending, real CUDA compute evidence and one CPU fallback.
- Exact source and build-input attestation, hostile-input regressions, VM
  interruption harnesses, and an immutable-plan/append-only GitHub controller.

Binary delivery of these additions remains behind native, licensing and exact
artifact gates. Source integration uses its separate source/CI/remote-state gate;
source acceptance never certifies a native or redistributable release.

- Versioned file-operation results, strict Undo tokens, an operation journal, and
  conservative restart recovery.
- Sidecar-aware move/copy/trash/Undo behavior and collision reservation.
- Bounded asynchronous decoding with stale-generation cancellation.
- Off-thread AI initialization and model integrity checks.
- Stable registry and storage boundaries for future optional component packs.
- Linux artifact qualification, release manifests, and experimental native
  Windows/macOS smoke jobs.

### Changed

- CUDA activation and inference explicitly disable TF32 and internal provider
  retries; the host validates effective precision before accepting GPU results.
  Legacy GPU helpers without the precision contract use one explicit CPU
  fallback. Full precision may reduce throughput; it does not promise bitwise
  CPU/GPU equality. The original numerical acceptance tolerance is unchanged.
- Rebuilt all five local helper packs as `1.0.0+20260909.r10` from the clean
  precision checkpoint. The catalogue remains local-qualification-only; older
  archives and their failed or incomplete evidence remain preserved.
- Consolidated the eight 2026 unification task branches while retaining merge
  ancestry.
- Corrected Python support to 3.10+ and made package versioning single-source.
- Corrected frozen resource paths and made requested AppImage failure fatal.
- Removed checked-in runtime settings and the duplicate dependency list.

### Security and data safety

- File mutations now validate provenance and fail closed for incomplete Undo,
  malformed metadata, sidecar conflicts, and ambiguous recovery state.
- Model and AppImage build-tool downloads are pinned and SHA-256 verified before
  activation or execution.

### Source integration continuation: native startup and image identity

- Repaired macOS pathname sockets for long isolated profile paths using a short, private, user-owned endpoint directory. Linux abstract sockets are unchanged. Unrelated endpoint files are preserved, and connection failures retain operation IDs with visible diagnostics.
- Worker request bookkeeping and enrichment cancellation now follow successful admission.
- The viewer and decoder use the same default component-store contract for cache identity, preserving Windows base-image display without relaxing component verification.
- Preserved the second candidate's hosted failures. Its Python 3.10–3.12, X11, pinned CPU inference and artifact jobs passed; Windows/macOS smoke failures still required these repairs and a new exact-candidate run.

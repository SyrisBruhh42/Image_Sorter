# Changelog

This project follows semantic versioning once stable releases begin. The package is
currently `0.1.0.dev0`.

## Unreleased

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

These cutover additions remain unpublished candidates until all required native,
licensing, recovery, CI and remote-state gates pass.

- Versioned file-operation results, strict Undo tokens, an operation journal, and
  conservative restart recovery.
- Sidecar-aware move/copy/trash/Undo behavior and collision reservation.
- Bounded asynchronous decoding with stale-generation cancellation.
- Off-thread AI initialization and model integrity checks.
- Stable registry and storage boundaries for future optional component packs.
- Linux artifact qualification, release manifests, and experimental native
  Windows/macOS smoke jobs.

### Changed

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

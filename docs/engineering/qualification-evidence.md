# Native qualification evidence contract

`scripts/qualification_schema.py` is the shared, application-independent semantic
verifier used by the cutover controller and native aggregate command. A matrix
containing `passed` and arbitrary hashed files is insufficient. The verifier
checks consistency of recorded observations; it does **not** cryptographically
authenticate an operator, prove screenshots were honestly captured, or turn a
hand-written observation into an automated measurement. Preserve raw records,
name the observer, and report unavailable cases as unavailable.

## References and identities

A file reference is `{ "path": "/absolute/path", "sha256": "64 lowercase hex" }`.
Referenced files must still exist and match. Prefer absolute paths throughout
nested documents so sealing the top-level matrix does not relocate their meaning.
Structured evidence is bounded to 20 MiB per file. A `source_sha` and `source_tree`
always mean the exact current candidate or actual final merge being qualified.

Each top-level artifact adds `build` and `smoke` file references; each case adds
an `observation` reference. Existing top-level completeness, native KDE/X11,
all-processes-stopped, all-three-artifacts and all-required-cases checks still
apply. `components[].manifest` must identify an actual component descriptor
containing matching `id`, `version`, archive `sha256` and complete `files`, not an
unrelated log or a catalogue-wide placeholder shared across components.
Every descriptor must also exactly match its version in the source-controlled
`imagesorter/resources/component_catalog.json` inside each delivery's verified
runtime inventory. A descriptor cannot grant trust to itself.

## Build record

The external build record has:

- `schema_version: 1`, exact `source_sha`, `source_tree`, and `dirty: false`;
- `launch`: reference to the exact executable in the artifact record;
- `delivery`: reference to the delivered wheel or AppImage (mandatory for those
  kinds; the onedir delivery is its complete runtime inventory);
- `runtime_root`: absolute installed or extracted runtime directory;
- `runtime_files`: a nonempty complete array of `{relative_path, sha256}`;
- `build_log`: reference to the preserved actual build/install process log.

The inventory rejects duplicate/escaping paths, changed content, omitted files
and extra runtime files. Preserve a quiet qualified runtime: creating cache files
inside it after recording the inventory invalidates that inventory. Wheel build
records identify the non-editable installation, not the checkout. For a mounted
AppImage, retain the corresponding extracted runtime inventory and original
AppImage digest. A receipt writer may serialize these observations after a real
build, but must not infer a build's source from a caller-supplied SHA alone.

The application also emits its packaged `resources/build_identity.json` as the
raw diagnostic's `build_identity`: schema, exact source SHA/tree, `dirty:false`,
and a nonempty source-file hash map. This second identity must agree with the
external build and requested qualification. The native runner checks it before
reporting readiness success. Optional pack provenance can predate the final
catalogue commit; its helper source-file hashes, not a cyclic catalogue SHA,
must match the corresponding qualified sources.

## Native smoke record

The actual native runner writes `smoke.json` with `complete:false`. Each delivery
kind needs its own xcb run; offscreen/Xvfb evidence cannot be substituted.
Required named references are `diagnostic`, `telemetry`, `traces` (an array),
`fixtures_before`, and `fixtures_after`. The latter two point to JSON arrays of
file references, not to an assertion that files were unchanged.

Required identity/timing fields include `run_id`, `effective_profile_root`,
`observation_started_unix`, `observation_ended_unix`, and
`first_launch_observation_seconds`. The actual first-paint-to-GUI-shutdown
monotonic interval and wall observation interval must each cover at least 30
seconds. The raw diagnostic must contain exactly one `image_presented` for the
160×100 supplied fixture and one `gui_shutdown` with elapsed time ≤5,000 ms.
Actual module origin must be installed/extracted/mounted, never checkout source.

The verifier reparses every process-attributed trace: the hashed launch must
appear in `execve`, the GUI PID must be covered, every traced PID must have a
zero exit, and IPv4/IPv6 socket/connect/send attempts and model-file open attempts
must both be zero. Recomputed telemetry must equal the recorded telemetry. An
unsuccessful network attempt is still an attempt. Fixture lists must match and
their retained files must still match their hashes. This smoke is a bounded
observation, not proof against future network activity or unobserved launch paths.

Python startup `.pth` files are not automatically model weights, but their
extension or basename never grants an exemption. Pass `--build-record` to the
native observer for an installed wheel. It verifies the complete installed build
inventory and accepts only a bounded valid site-startup file directly under
site-packages/dist-packages, with exact SHA-256/size, unique distribution RECORD
ownership and inventory-bound RECORD/METADATA bytes. No startup text is executed
by the classifier. The smoke retains that full `python_startup_files` attestation
and its `installed_build` reference; the independent verifier reconstructs and
compares it. Changed bytes, binary checkpoints, unowned/ambiguous files and the
same basename outside that exact installed location remain model attempts. Raw
traces and failed prior observations are preserved, never rewritten into PASS.

## Interactive case observation

Each required `{id, artifact_kind, status, evidence}` case additionally references
one JSON observation with:

- `schema_version:1`, exact `source_sha`, `source_tree`, `id`, and
  `artifact_sha256` matching that delivery's launch;
- `kind:"operator-observation"`, nonblank named `operator`, unique `run_id`,
  timezone-bearing ISO `observed_at`, actual nonempty `steps`, `expected`,
  and `observed` descriptions;
- nonempty `supporting_evidence` file references to actual screenshots/logs;
- `criteria`, with every key required for this case explicitly true only after
  observation. The authoritative key map is `CASE_CRITERIA` in the verifier;
- `fixtures_before` and `fixtures_after`, each a nonempty JSON array of
  `{relative_path,sha256,size}` describing the disposable fixture inventory.
  These may differ for intentional file operations; record and explain changes.

KDE-04/05/06/07/08/14 additionally require `mutation_receipts`, references to
actual persisted schema-1 results with operation ID and a terminal or explicitly
recoverable state. Preserve the journal export backing them. Failure and
recovery-required outcomes can be the **expected** result of a failure-injection
case; they are not relabeled successful file operations.

KDE-14 also requires measured `gui_shutdown_ms` ≤5,000,
`readonly_reap_ms` ≤3,000, `mutation_drain_completed:true`, and
`all_processes_stopped:true`. GUI exit alone cannot establish these fields. Do
not kill the mutation owner to manufacture a timing pass.

The normal-AppImage drain probe records its actual `.mount_` path and observes
both disappearance from kernel mount information and removal of the temporary
directory. Runtime cleanup is asynchronous after GUI exit: allow a separate
maximum 5,000 ms observation window, recording every sample and elapsed time in
`mount_teardown`. This does not extend the GUI deadline, force an unmount, or
terminate the surviving writer. Missing or unreadable mount evidence fails the
check. Wheel and extracted routes explicitly record this check as not required;
they cannot establish normal FUSE-mount cleanup. The paired mutation is submitted
only after this bounded observation, and its exact source/sidecar bytes and final
owner exit must still be verified.

## Component observations

Every component case requires the exact `component_manifest_sha256` and criteria
for explicit install, malformed input, cancellation, disable, uninstall,
reinstall, and rollback. Format components add a `formats` mapping; each declared
format has `{status:"passed", fixture:FILE_REFERENCE}`. Required coverage is:

| Component | Required observed formats |
| --- | --- |
| HEIF/AVIF | HEIC, HEIF, AVIF |
| RAW | CR2, NEF, ARW, DNG, ORF, RW2, PEF, RAF, SRW |
| Animation/multipage | GIF, APNG, WebP, TIFF |

These are the formats advertised by the current source registry. A fixture must
actually use its named format; renaming a JPEG is not format evidence. Preserve
fixture provenance, expected decode results, color/orientation/alpha checks and
frame/timing observations in supporting records. Missing fixtures remain an
unpassed acceptance gate, not a reason to silently reduce advertised coverage.

NVIDIA observations additionally reference `gpu_result`, which must record
an explicitly successful reply (`ok:true`, no error or fallback reason), actual
`CUDAExecutionProvider` use, and the helper's canonical `actual_providers` and
`compute_nodes` fields. The actual session must contain CUDA and may contain CPU;
each compute node has a unique nonempty `name` and a provider in that session.
The positive integer `cuda_compute_events` must equal the number of named CUDA
nodes. Exact lowercase SHA-256 `tensor_sha256` and `profile_sha256` values are
required. Legacy `node_providers`/`session_providers` aliases are rejected, not
guessed or synthesized. These are consistency checks on the preserved reply;
hashes alone are not independent proof of an operator's observations.
Record computed `cpu_gpu_max_abs_error`, declared `cpu_gpu_tolerance` (≤0.01),
and an actually observed `forced_fallback_reason`. Preserve both CPU/GPU outputs,
profiling records and timing/memory measurements in supporting evidence. Provider
enumeration alone cannot satisfy execution evidence.

The new GPU precision policy also requires the additive `cuda_precision` object:

```json
{
  "policy_version": 1,
  "requested_use_tf32": "0",
  "observed_use_tf32_before": "0",
  "observed_use_tf32_after": "0",
  "internal_fallback_disabled": true
}
```

Every field has the exact shown type and value, with no missing or extra keys.
The worker observes the CUDA session's effective `use_tf32` setting before and
after inference, and disables ONNX Runtime's internal fallback. GPU failure or
unverifiable precision returns through the application's separately observed CPU
helper, not a hidden retry inside the GPU process. The host rejects legacy GPU
replies missing this policy, including retained r9 rollback versions; their
archives remain recoverable and CPU operation remains available.

This is not a promise of bitwise CPU/GPU equality. Preserve a predeclared
numerical tolerance and real outputs. The 2026-09-09 test declared `1e-5` before
execution: the preserved E/r9 result failed at `0.0008931681513786316`. Do not
raise that tolerance or relabel the old result. A separate exact-runtime
countertest with TF32 disabled reduced the observed full-vector error to
`7.37607479095459e-7`; it establishes the repair rationale, not qualification of
newly built packs. The packaged IPC exposes only ten ranked scores; distinguish
that comparison from a diagnostic that captures all 1,000 outputs.

[ONNX Runtime documents the default TF32 precision/performance tradeoff and
per-session control](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#use_tf32).
Pinning full precision can reduce GPU throughput. Benchmark actual new artifacts;
do not invent a speed guarantee or silently enable reduced precision to pass a
performance target.

## Failure and maintenance

Changing a source, binary, installed dependency, component descriptor, fixture,
or evidence file invalidates the corresponding sealed record. Create a new
record; do not rewrite old accepted evidence. All hash-consistent contradiction
tests use explicitly labeled synthetic records in temporary directories; those
unit fixtures are never native acceptance results.

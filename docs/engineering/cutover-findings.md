# Cutover audit finding register

This register covers cutover tooling and governance. It does not claim that the
application, native matrix, or GitHub cutover has passed final acceptance.
Implementation status and operational acceptance are different facts.

| ID | Confirmed finding | Control / repair status | Evidence / outstanding acceptance |
| --- | --- | --- | --- |
| CUT-01 | Separate read and unconditional deletion can remove an advanced, unarchived tip. | Full-ref/full-expected-SHA deletion lease; absent-only recovery; exact allowlist. | `test_real_git_lease_rejects_update_between_preflight_and_push` exercises a real disposable Git remote. |
| CUT-02 | Repairs change the candidate while later gates only check old `f6d7e8b` ancestry. | Immutable candidate/tree identity, exact PR-head merge condition, current-base checks, merge parents/tree validation, new actual-merge receipt. | Evidence rejection and drift tests; live GitHub/native gates remain pending. |
| CUT-03 | Quoted glob passed to `git show-ref` cannot list the archive namespace; annotated object and peeled commit identities differ. | `for-each-ref` with object type and peeled target; exact mapping and independent local/remote restores. | Controller validates each tag and refuses changed identities. |
| CUT-04 | Frozen CSV does not contain the new integration branch or later repairs. | Plan registers the integration head separately and includes a new immutable candidate archive. | New candidate requires a new plan; original snapshot remains unchanged. |
| CUT-05 | Phase-only restart instructions cannot reconcile an interrupted mutation. | Immutable plan; durable intent/result/reconciled journal; state-derived retries; no inferred deletion authorship. | Crash/unknown response, torn-tail, hash corruption and changed-state tests. |
| CUT-06 | Branch freeze cannot stop a new fork PR dependency racing deletion. | Before/after dependency checks and absent-only compensating restoration, then stop. | Real local ref deletion/restoration test with injected PR arrival. GitHub metadata cannot share the ref transaction. |
| CUT-07 | Automatic GitHub branch deletion can bypass the archive/prune phases. | Snapshot and disable automatic deletion during cutover. | Controller checks the setting with effective policies. |
| CI-01 | Candidate CI filters only target `main`, but the integration PR initially targets the old default. | All PR targets plus exact old-default/integration/main push coverage. | Workflow source and aggregate test; hosted runs required after publication. |
| CI-02 | A skipped job can appear successful to GitHub; optional CPU inference was skipped for PRs. | Stable aggregate explicitly requires actual success of every mandatory job; CPU inference runs for PRs. | Test executes the actual aggregate program with success/skipped/failure/cancelled results. |
| CI-03 | A process surviving a timeout is not proof it painted an image or shut down cleanly. | Packaged smoke calls `native_acceptance.py` for actual readiness and clean shutdown. | Native runner and complete artifact qualification are separate acceptance gates. |
| DOC-01 | In-repo evidence describes an older 35-tag bundle while the handoff describes the newer 36-tag bundle. | Historical bundles explicitly labeled; current state derived from receipts. | No historical result relabeled as current. |
| COMP-01 | Global thread lock persisted throughout a component lease, defeating nonblocking removal and unrelated concurrency. | Repair in progress: OS per-file leases and cross-thread race tests. | Independent source review; final repair verification pending. |
| COMP-02 | Post-activation receipt failure could mark an active installation failed and prevent recovery reconciliation. | Repair in progress: explicit activation commit point and ambiguous-state reconciliation. | Fault injection after registry replacement required. |
| COMP-03 | Model and labels were snapshotted without a model-component lease; receipt read the later active version. | Repair in progress: capture exact leased model descriptor and retain ownership across snapshots. | Concurrent removal/update test required. |
| COMP-04 | File-to-FIFO replacement could block snapshot open before timeout enforcement. | Repair in progress: nonblocking no-follow open and descriptor regular-file validation. | Special-file replacement test required. |
| COMP-05 | Helper response validation allowed malformed objects, empty decode success, and unbounded animation metadata. | Repair in progress: action-specific strict IPC schema. | Invalid object/frame/payload/duration/loop tests required. |
| COMP-06 | Build provenance was recorded before freezing mutable source without a final source identity check. | Repair in progress: captured source build inputs and embedded source identity. | Build-time source drift rejection required. |
| COMP-07 | Generated catalogs omitted prior trusted descriptors, invalidating retained rollback versions on adoption. | Explicit `--previous-catalog` retains trusted descriptor history and rejects a version reused with different bytes. | `test_catalog_adoption.py` exercises actual catalogue generation, update, reopen and verified rollback; conflicting identity produces no catalogue. |
| COMP-08 | CUDA enumeration was weaker than real execution, and per-node provider evidence was discarded. | Repair in progress: retain actual session/node-provider observations and real CUDA validation. | All installed artifact GPU cases remain acceptance gates. |
| EVD-01 | Passing flags plus arbitrary hashed evidence files did not establish recorded native facts. | Shared pure semantic verifier recomputes raw smoke facts and requires attributed per-case observations, exact builds, full runtime inventory, component/operation evidence. | Synthetic contradiction tests; actual native qualification remains pending. |
| TXN-01 | Public-path verify followed by unlink could delete a replacement inserted between those operations. | Journaled private quarantine before ownership verification/deletion; mismatched bytes preserved and restored only if absent. | Independent code review confirms durable path intent precedes rename; deterministic replacement regression preserves external bytes and reports recovery required. |

The supplied bundle hash and refs were verified during intake; those checks
establish artifact/reference consistency only. The historical 152-test claim,
desktop acceptance, per-feature retention claims, and live remote state require
their own current evidence. Missing evidence blocks the relevant phase.

## Implementation checkpoint — 2026-09-08

These observations supersede the implementation-progress wording above, not the
final qualification gates. Root reports 39 component tests passed covering strict
IPC, noisy probes, offline exact-pack reconstruction, FIFO-safe staging,
ambiguous activation reconciliation and cross-thread leases. The r4 private
dirty-source builds passed HEIF/HEIC/AVIF, all nine RAW extensions, animation/APNG/
TIFF, actual CUDA execution across 100 nodes, and injected CUDA failure with one
CPU fallback. Lifecycle update/rollback/remove acceptance was still in progress
at this checkpoint. These are preliminary implementation results, not a final
clean-source/artifact publication receipt.

| ID | Finding / repair | Evidence and remaining boundary |
| --- | --- | --- |
| COMP-09 | APNG source-over alpha composition was incorrect; root repaired it. | r4 APNG probes reported passed; final native artifact matrix still required. |
| LIC-01 | Upstream pillow-heif binary wheel includes unnecessary GPL x265 encoder. | New decoder-only wheel independently built and probed; exact libheif/libde265 source, LGPL notices and dynamic/relink evidence retained. |
| LIC-02 | Exact converted MobileNet weights and PyTorch hub labels lack an established affirmative redistribution grant. | Publication decision remains unresolved; runtime inference success is not licence evidence. |
| LIC-03 | Combined frozen PyQt application cannot accurately be labelled MIT-only. | Project source remains MIT; GPL-compliant binary notice/source collection implemented, exact final closure still gated. See `docs/licensing.md`. |
| LIC-04 | Supplier/package SBOM labels can overstate or omit actual native contents. | Frozen preflight inventories 221 ELF files; optional Pillow libimagequant is absent despite generic SBOM entry; unused Qt PDF plugin identified for subtraction. |
| EVD-02 | Packaged build provenance can accidentally reflect a live/editable checkout. | Root added spec source-origin guard and all-file source-export attestation; final clean build identity remains required. |

The decoder build recipe has nine isolated regression tests. Runtime licence
collection has fourteen tests, including obligation-sensitive source gates,
hash/path validation and refusal to treat version matching as verified GPL/LGPL
build correspondence. Neither test suite creates legal clearance.

The source-acquisition selector adds four tests; the combined licensing/decoder
suite has 27 passing tests. The final whole-VM power-interruption report was
independently checked: 12 exact cases, 24 stable cold-restart reports and 25 raw
Linux boot records. All payload hashes, ten application module hashes, the guest
probe and host harness match current source at review. The attribute-preparation
crash gap was repaired and its explicit rollback passed in the VM. Report SHA-256:
`22602328c15489231e7cff7d199f102c462c28f872edbc6ee5f1e31c5a24a1bd`.
This proves the recorded whole-guest/kernel-RAM interruption scenarios with
virtual block flush semantics, not a physical host/storage-controller power cut.

## Decoder/source checkpoint — 2026-09-09

The private `raw-decoder-build-r2` independently rebuilt rawpy 0.27.1 against
LibRaw 0.22.2 from pinned source, without host package changes. The repaired
wheel SHA-256 is
`0f426938f7eacaaa45ef99a5250ed4a9cd403d126c29e9ee73764d123d89102a`;
its receipt SHA-256 is
`93c1cae895a51af34024719a7235f815ced07fddfcf66a5865b2f87bd56dde13`.
Runtime and compiled library versions both report 0.22.2, prior configured flags
match, and both captured recipe hashes match the source reviewed at this
checkpoint. This is an isolated build/capability result. Root must integrate the
wheel and repeat all nine actual format cases and final native qualification.
This r2 build is historical and superseded by r4 below because it retained
rawpy's inherited LCMS 2.11 build dependency.

| ID | Finding / repair | Evidence and remaining boundary |
| --- | --- | --- |
| RAW-01 | The original wheel used LibRaw 0.22.1, preceding the upstream 0.22.2 defensive bug fixes. | Controlled 0.22.2 source rebuild complete; exact source/relink configuration preserved. Do not infer that every upstream bug was exploitable in this application. |
| RAW-02 | `REDCINECODEC=true` did not prove actual RedCine support: LibRaw 0.22 removed the old video-camera source while CMake retained the configuration flag. | Verified no JasPer references/linkage in fixed runtime. Documented as legacy metadata; nine advertised still-camera fixtures are the support gate. |
| EVD-03 | A declared delivery hash alone does not establish that a licence inventory describes the contents of that archive. | Publication agent is implementing direct archive/payload-to-inventory comparisons; completion requires those independent checks, not a self-declared association. |

RAW recipe validation has 19 tests (version mismatch, every feature drift and
safe bounded source extraction). Exact distributor-source checks now have seven
tests, including identity/hash/size, duplicate-member and symlink rejection.
All 38 acquired distributor source sets were independently verified against the
preserved descriptors and receipt hashes. The original acquisition captured its
script at completion rather than startup; this limitation is preserved explicitly
in its independent audit, while future runs capture executable recipe bytes
before work starts. No acquisition or unit test constitutes legal clearance.

The accepted decoder build is `raw-decoder-build-r4`: LibRaw 0.22.2 and LCMS
2.19.1 exact source, wheel SHA-256
`189c7cb2ebd2d0566b80811c9983d8736e8b5719263761b5f0161e6056a6bafd`,
receipt SHA-256
`a919db59e10b44e25d71d5b8c753e5d9f1dfec6adae20f90a7ff5b006406e73b`.
All source, wheel and captured/live recipe hashes were independently checked.
The LCMS hotfix source retains API value 2190; r3 correctly stopped on an overly
strict assumed 2191 assertion, then r4 used the value verified in the exact source.
Twenty RAW recipe tests pass. Application format/native qualification remains
owned by the integration/native runners, not inferred from this build.

| ID | Finding / repair | Evidence and remaining boundary |
| --- | --- | --- |
| RAW-03 | Reusing rawpy's upstream dependency recipe retained obsolete LCMS 2.11. | Root review prompted a source-pinned upgrade to maintained LCMS 2.19.1; runtime API and source identity checked, real RAW fixtures still required. |
| LIC-05 | AppImage runtime's aggregate notice omits statically linked mimalloc and does not identify every exact Alpine package input. | New collector preserves full runtime/libfuse-patch/SquashFuse sources, static dependency notices including mimalloc, build metadata and labeled historical Alpine recipes. Exact release build logs are expired (HTTP410), not invented. Static LGPL relink execution remains unproven. |

`appimage-runtime-source-r1` has 69 independently hash-verified preserved files
and seven collector regression tests. Its report SHA-256 is
`10800786842a3c5c18a68596066fa31f4082d08ab8438467f03a11c2f75218a2`.
It is a complete collection of the stated evidence, not a distribution-clearance
or unsupported claim that all historical package inputs are known exactly.

## Independent r5 RAW pack qualification

The r5 RAW pack (SHA-256
`574345d605e4881c9ce46c761bfea8ab4684646f127f9ee70f19134cb707a1ec`)
passed 21 component cases: all nine pinned formats with deterministic repeated
previews, all nine full camera-white-balance/sRGB raw renders, clear unsupported
input rejection, initial install/verify/disable/reopen, and the full disposable
update/rollback/lease/removal/reinstall lifecycle. The largest full response was
the SRW fixture at 112,666,624 bytes; no preview-only success was promoted into
proof of raw pixel processing. Original fixture hashes/size/mode/mtime, archive
hash and loaded client-source hashes remained unchanged.

`raw-pack-r5-independent-r2/report.json` has SHA-256
`0a2b778770d81f028810830ab7a92ac9ab92836b0beb6cee7375e861b515bf22`
and preserves its exact executed harness/client snapshots. The initial r1 report
retains two corrected harness mistakes: too-short invalid input produced I/O
rather than unsupported-format wording; reinstall mistakenly used the temporary
update catalogue. Neither was reported as an application defect. The reusable
runner now also refuses optimized Python, which would disable assertions.

This is preliminary r5 component qualification, not final KDE/native application
acceptance, OS reader write containment, or publication clearance. The pending
reader-containment change alters the frozen helper and requires new final packs
and requalification; these r5 results do not certify those future bytes.

## Reader-authority and collection checkpoint

| ID | Verified finding / repair | Evidence and remaining boundary |
| --- | --- | --- |
| ISO-01 | CUDA's trusted tiny MatMul creates AF_UNIX/SOCK_SEQPACKET sockets and an abstract bind/listen; it passes without a successful MPS connection or socket message exchange. | `test-evidence/cuda-ipc-baseline-FHdBwm` and `cuda-ipc-denied-fR7I2N` both passed exact matrix output plus one real CUDA event. The second installed a narrow seccomp filter before ONNX Runtime import and denied every connect/accept/send/receive call. Bind/listen necessity was not separately isolated. Host Landlock ABI is 8; filesystem UNIX lookup control requires ABI 9. This tiny probe is not full model/native qualification. |
| ISO-02 | The original r6 reader could deliver SIGTERM to another same-UID process using F_SETOWN/F_SETSIG/F_SETFL(O_ASYNC), despite blocked kill calls. | Exact preserved policy `cbd43df6eb9aa6a36b705831fc1c1ce72ceea8631a482533743bc2796b66ad6b` reproduced the bypass against only an owned disposable target: `test-evidence/reader-fcntl-original-H9RKSt`. Current policy `db67d7e1585b2d9498ebb513475d46893d913acb5fd10c43160ae4333332b618` denied all three controls and the target survived: `reader-fcntl-signal-pt46iF`. Receipt SHA-256 `394152918b851f557ec3404aad60f3c392a9eaa1c6ec13c7d1126bab48dffcf8`. Final packs must include the repaired policy; r6 is not accepted for publication. |
| LIC-06 | The base-runtime collector incorrectly demanded build_identity.json from packs that correctly carry provenance.json; optional runtime extras were also not expanded. | Explicit component-descriptor mode now checks the complete pack inventory, delivery hash, actual provenance and exact clean pack-build source. GPU CUDA/cuDNN extras expand to the actual runtime dependencies. Core embedded-identity gates remain unchanged. |
| LIC-07 | Generic library/source reviews could omit the separately distributed model and label assets. | Exact model-weight and model-label path/hash/role mappings now require distinct source IDs. Publication separately binds each attributable rights review to its exact asset hash and role; technical mappings do not create missing grants. |

The collector's base and component suites have 38 passing tests; the combined
collector/publication suite had 139 passing tests at this checkpoint. The actual
preserved r4 GPU environment resolved 13 runtime packages, including seven NVIDIA
CUDA/cuDNN packages, under explicit extras. None of these controls supplies legal
clearance or certifies an unbuilt final artifact. A bounded final reader-policy
review found no additional confirmed safety escape after ISO-02's repair; it is
not an exhaustive kernel/driver or availability-isolation certification.

## Verifier-only checkpoint — preserved E application evidence

| ID | Confirmed finding | Control / repair status | Evidence / outstanding acceptance |
| --- | --- | --- | --- |
| EVD-04 | The helper preserved actual GPU session/node facts as `actual_providers` and `compute_nodes`, but the final verifier expected stale aliases and accepted arbitrary truthy node data. This was a verification-contract defect, not missing GPU execution in the preserved E run. | Canonical successful reply only; exact CPU/CUDA session types, unique named nodes, consistent positive CUDA count, tensor/profile digests, and no failed/fallback status. Legacy aliases are rejected. | `tests/test_gpu_evidence.py` rejects type confusion, enumeration-only claims, duplicate/invented nodes, miscounts, malformed digests and contradictory status. Actual E reply retained 100 named CUDA events and exact session/model/component identities. Independent adversarial review and corrected raw-reply checks do not satisfy still-unperformed full native, equivalence/fallback or publication gates. |
| EVD-05 | A fixture envelope could substitute unrelated bytes after omitting the optional `input_sha256`; the shared reader gate also failed to require an explicitly successful reply. | Every fixture-bearing reader reply must have `ok:true`, no error, and an exact lowercase 64-character input digest matching the preserved fixture. Probe-only replies cannot qualify a fixture. | `test_native_format_evidence_binds_real_reply_policy_and_fixture` covers missing/wrong/malformed/type-confused digests, substituted fixtures and failed status. Independent frozen-E counterexample receipt `test-evidence/gpu-fixture-counterexample-E/receipt.json`, SHA256 `66060955bcb5329889d9bfd67db302619258b427d6f035ed584af74952116084`, demonstrated the omission bypass using copied actual GPU and GIF replies at the `verify_native_readers` stage only; no original receipt or application artifact was changed. |

The targeted verifier/observer/cutover/publication suite passed 283 tests in
2.58 seconds (`test-evidence/gpu-verifier-F/source-targeted-tests.xml` in the
external evidence root). This checkpoint changes verification code, tests and
documentation only; helper/application bytes and existing E artifact identities
remain unchanged. Preserved E client-rendering, optional-scenario and shutdown
results are not relabeled as a later-source full native qualification. The
locked desktop, missing acceptance coverage and unresolved distribution rights
remain explicit gates.

## Final-test precision finding — 2026-09-09

| ID | Confirmed finding | Control / repair | Evidence and remaining boundary |
| --- | --- | --- | --- |
| GPU-01 | E/r9 used ONNX Runtime's default TF32 math. All three packaged routes produced the same top-ten CPU/GPU score error `0.0008931681513786316`, exceeding the test's predeclared `1e-5` bound despite identical inputs, tensors, models, labels and ranked labels. | Explicit full-precision CUDA session options; effective option checks before and after inference; no internal provider fallback. The host and new qualification gate require typed precision attestations and reject legacy/malformed GPU replies through the existing explicit CPU fallback. | Independent exact-library countertest repeated default and TF32-disabled CUDA, using a CPU comparison across all 1,000 outputs: disabling TF32 reduced maximum error to `7.37607479095459e-7`; GPU-runtime CPU matched the base CPU vector exactly. New helper/core builds and their native numerical checks are still required. This is a declared-tolerance failure, not proof of image corruption or classification accuracy. |

The preserved packaged discrepancy audit is
`test-evidence/packaged-ai-equivalence-E-r2/independent-discrepancy-review/receipt.json`,
SHA-256 `988c495f55441e26b02e87055f2d4dfebaeb026acfa5d5c6de0f922da5b6efef`.
The isolated precision countertest is
`test-evidence/cuda-precision-diagnostic-r2/report.json`, SHA-256
`8b9e2d36028f85a831f251e44f955d0ec0026dd5040feb56f2e9dd6aa13fd6c9`.
The normal AppImage's helper-attempt count was not traced; wheel/onedir raw
process traces establish one failed GPU process followed by one CPU process,
with fallback scores equal to the baseline. Same-PID PyInstaller bootstrap
re-execs are retained, not mistaken for distinct inference processes.

The first external precision diagnostic lacked the leaf reader's isolation and
mapped a normal ONNX cache file. Its record has no pre-run cache baseline, so
creation or modification of that cache cannot be excluded; no cleanup of a
possibly pre-existing cache was attempted. This observer-isolation incident is
preserved separately in
`test-evidence/cuda-precision-diagnostic-r1/observer-incident.json`, SHA-256
`ec6d693ca74a7850d6fe038a37c1c866c24d4c9ecfd712cb63802a53d8abdcc0`.
It is not evidence that the shipped sandboxed reader mutated that path.

The original F checkout and E artifacts remain unchanged. Precision remediation
uses a separate local worktree so source paths already named by immutable
evidence remain recoverable. The original bundle, profiles, r9 packs, all failed
observer results and the unchanged `1e-5` numerical oracle are preserved.
Actual Dolphin/Open With passed for all three E routes and 36 additional E
headless format cases passed, but neither result certifies future repaired
artifacts. Remaining unlocked-desktop tests, normal-AppImage first-launch
tracing, complete native coverage and redistribution gates still block cutover.

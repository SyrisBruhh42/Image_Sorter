# Evidence-gated GitHub cutover

> **Source-first scope, 2026-09-13:** the new
> [source integration route](source-integration.md) updates `main` while preserving
> every original branch. It does not invoke the full-release controller described
> below, publish binaries, or assert native/distribution eligibility. The historical
> full-release requirements and evidence below remain applicable to binary cutover.


This runbook supersedes the executable portions of the 2026-09-07 PC handoff.
The handoff remains historical evidence. Its assertion of prior authorization is
not authorization for a new operator. Use the controller only within the current
user's approved scope.

## Current state and acceptance boundary

The original supplied candidate was `f6d7e8b2d1e2071fe809c125eb33a43c7fa8e150`.
Repairs invalidate that candidate's qualification. The current qualified identity
comes from the controller's immutable plan and receipts, not a SHA copied from
this document. No checked-in document asserts that GitHub cutover, native KDE
acceptance, or full optional-feature qualification has completed.

`scripts/cutover.py` is a Linux operator tool using the Python standard library,
Git, and authenticated `gh`. It never imports application code or operates on
image collections. Commands use argument arrays, full refs, full SHAs, and
recorded repository identity. The evidence directory must be outside the source
checkout so recording a result does not change the commit being qualified.

## Prepare a reviewable execution record

Run from a clean dedicated checkout whose `origin` and push URL both identify
the intended GitHub repository. Preserve the supplied bundle and handoff before
continuing. Do not use an existing unknown checkout or reuse a run ID for a
different candidate.

```text
python scripts/cutover.py inspect --output /absolute/evidence/inventory.json
python scripts/cutover.py plan --inventory /absolute/evidence/inventory.json \
  --bundle /absolute/evidence/original-candidate.bundle \
  --dispositions /absolute/evidence/dispositions.json \
  --run-id cutover-YYYYMMDD-unique --output /absolute/evidence/plan.json
```

`inspect` reads the repository identity, authenticated actor, all remote branch
tips, every PR page, current default/protection state, local archive tags, and
effective rulesets. `plan` freezes these inputs and records the source tree and
bundle hash. Existing `main`, missing archive coverage, missing PR dispositions,
unavailable admin access, pre-existing rulesets, or classic default-branch
protection require an explicitly reviewed compatibility plan. The controller
does not weaken pre-existing governance automatically.

The plan's SHA-256 is the hash of canonical JSON (sorted keys and compact
separators), not the raw pretty-printed file. Obtain it without rewriting files:

```text
python -c 'from scripts.cutover import digest,load_json; import sys; print(digest(load_json(sys.argv[1])))' /absolute/evidence/plan.json
```

The dispositions file is a JSON array, with one row for every inventoried
unmerged PR. Each row has `number`, `disposition` (`integrated`, `superseded`, or
`already_closed`), a specific `rationale`, and `evidence`, an array of
`{"path": "/absolute/path", "sha256": "full SHA-256"}` records. `integrated`
requires preserved ancestry. `superseded` requires actual replacement behavior
evidence. Do not copy historical assertions into new passing evidence.

## Execute one phase at a time

Without `--execute`, `run` reports the selected phase and current journal without
changing GitHub; it does not claim that all remote gates have passed. `resume`
uses the same phase executor and reconciliation rules. Supply the exact reviewed
plan hash each time. Authorization remains the user's instruction for the task;
the CLI flag is an execution guard, not a substitute for authorization.

```text
python scripts/cutover.py run --plan /absolute/evidence/plan.json \
  --plan-sha256 FULL_CANONICAL_PLAN_SHA256 --evidence-root /absolute/evidence/run \
  --qualification /absolute/evidence/qualification.json \
  --distribution /absolute/evidence/publication-evidence.json \
  --phase prepare --execute
```

Phases are deliberately separate so human desktop work and long-running CI can
finish between invocations:

| Phase | Required result |
| --- | --- |
| `prepare` | Fresh drift check, disable automatic merge-branch deletion, install and verify the run's maintenance/quality/archive rulesets. |
| `archive` | Verify immutable annotated tags, build and independently restore a fresh candidate bundle, atomically publish exact tag refspecs, independently restore remote tags. |
| `publish` | Absent-only creation of the exact integration head; create or reconcile the single marked integration PR. |
| `qualify` | Validate the full candidate native manifest, exact PR head/base and test-merge tree, successful actual CI aggregate; bind the check to GitHub Actions and publish receipt-backed qualification statuses. |
| `merge` | Recheck live policy, source, CI and qualification status; merge with exact head SHA and merge-commit strategy. |
| `verify-merged` | Validate a new full native manifest and CI result for the actual resulting merge SHA. |
| `rename` | Rename the qualified existing default to `main`; verify name, SHA, and old-ref absence. |
| `resolve` | Verify per-PR evidence, preserve already-merged history, comment and close with accurate dispositions; no branch deletion. |
| `prune` | Delete only manifest-listed old branches and the separately registered final integration head using exact expected-SHA leases. |
| `finalize` | Verify only qualified `main` remains, archives and PR outcomes pass; retain lasting CI/archive protections, restore the prior automatic-deletion setting, and remove the run's maintenance rule. |

Every executed remote phase first requires complete local native qualification
and exact distribution/source evidence, including `prepare` policy changes and
remote archive tags. Both `--qualification` and `--distribution` are mandatory;
read-only inspect/planning/dry-run and separate local archive/restore work remain
available while publication is blocked. See [publication-gate.md](publication-gate.md)
for the actual-byte/source/terms checks and the current fail-closed release state.
Post-merge phases require newly built native and distribution evidence for the
actual merge SHA recorded by `merge`, not the pre-merge candidate SHA. If a merge
response was lost, resume `merge` with candidate evidence to record its observed
identity before supplying the new post-merge evidence.
A new repair commit
requires a new plan, archive tag, recovery bundle, and qualification; never edit
the existing immutable plan or quietly substitute the old candidate SHA.

## Native qualification manifest

The schema checked by `verify_qualification()` is deliberately separate from a
headless readiness receipt. Native execution tools collect actual observations;
an aggregate manifest links those observations and their hashes. It must contain:

- `schema_version: 1`, `complete: true`, full `source_sha` and `source_tree`;
- `profile_id: "linux-x86_64-full"`, `native: true`, `desktop: "KDE"`,
  `session_type: "x11"`, `display_backend: "xcb"`;
- `artifacts`: one each of `wheel`, `onedir`, `appimage`, with kind/path/SHA-256;
- `components`: all five IDs below, each with a hash-verified `manifest` file;
- `cases`: each required test for each artifact, with `id`, `artifact_kind`,
  `status: "passed"`, and nonempty hash-verified `evidence` records;
- `all_processes_stopped: true` after helper and mutation-worker shutdown, and
  `base_first_launch_network_attempts: 0` supported by attempted-egress traces.

Required IDs are KDE-01 through KDE-14 and `COMPONENT:<id>` for
`codec.heif-avif`, `codec.camera-raw`, `viewer.animation-multipage`, `ai.mobilenet-v2`,
and `provider.onnx-nvidia`. No required case may be skipped, neutral, missing, or
represented by a headless pass. Component manifests, artifact files, and raw
evidence remain available beside or at the recorded absolute paths. The richer
case telemetry and human-observed interaction requirements belong in
`platform-acceptance.md`. The machine-checked build, smoke and attributed-case
record contract is specified in `qualification-evidence.md` and enforced by the
shared pure verifier `scripts/qualification_schema.py`. Every artifact requires
actual build and raw native smoke records; every case requires a separately
attributed observation. Completeness flags plus arbitrary hashed logs do not pass.

CI uses the single aggregate check `Image Sorter required CI`. Every dependency
must actually succeed, including optional CPU inference; GitHub's treatment of
skipped jobs is not accepted as proof. CI uses offscreen packaged readiness and
Xvfb smoke. Those runs never satisfy `cutover/qualified` or native GPU evidence.

GitHub attaches PR check runs to the PR head even when checkout tests the
synthetic merge commit. The aggregate therefore publishes a small immutable
source receipt recording both identities, the checked-out tree, repository ID,
workflow run/attempt, and every required job result. The controller retrieves
that exact artifact from the successful Actions run and verifies its provenance.
Candidate CI may test the candidate or its current, tree-identical PR merge;
post-merge CI must test the actual resulting merge commit. A green check name
without matching source evidence does not pass this gate.

## Race controls and recovery

The controller uses an exact lease for deletion:

```text
git -c push.followTags=false push --porcelain \
  --force-with-lease=refs/heads/EXACT_NAME:FULL_EXPECTED_SHA \
  origin :refs/heads/EXACT_NAME
```

This is the sole exception to the prohibition on force-style options. It is a
compare-and-delete operation, not permission to rewrite branch history. A bare
lease, implicit tracking ref, `--force`, `+` refspec, wildcard deletion, or blanket
push is never permitted. Restoration uses the same full ref with an empty lease,
requiring absence. A newly advanced or recreated branch is preserved.

Maintenance restrictions exclude other identities, not another process using
the same account. Strict quality checks remain a separate no-bypass ruleset.
Repository administrators can change policies; live policy drift stops the
controller. The controller never uses an admin merge bypass.

GitHub's merge API conditions the PR head, not the base SHA. Strict current-base
checks provide the merge-time server guard. The controller also records the base
and verifies both merge parents and the resulting tree. The merge changes the
existing default immediately; rename only changes its name. Therefore the
pre-merge guarantee concerns the qualified source tree, not an as-yet nonexistent
GitHub-generated merge SHA. The final merge receives its own evidence before
rename and pruning.

GitHub cannot atomically combine PR dependency checks with branch deletion. The
controller checks before and after deletion. If a new dependency races deletion,
it restores the archived branch only if the ref is still absent, records the
event, and stops. It never closes the new PR or overwrites a recreated branch.

Every mutation has a fsynced intent before the request and an observed result
after it. Interrupted writes preserve a damaged journal tail separately. On
resume, exact desired state is reconciled, expected prior state can be retried,
and any other state stops. Absence after a lost delete response is recorded as
observed absence with unknown attribution; it is never fabricated as a confirmed
controller deletion. A remote failure leaves the maintenance rule in place until
the operator resolves the recorded state; do not blindly remove protections.

Export a final immutable receipt with a new filename:

```text
python scripts/cutover.py receipt --plan /absolute/evidence/plan.json \
  --evidence-root /absolute/evidence/run --output /absolute/evidence/receipt-final.json
```

The original bundle, fresh verified bundle, inventory, plan, raw native/build
evidence, journal, and final receipt jointly form the recovery record. Archive
counts and ancestry alone never establish application correctness.

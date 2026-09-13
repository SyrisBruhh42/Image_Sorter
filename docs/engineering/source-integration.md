# Source integration, 2026-09-13

## Scope and current evidence

This route integrates the complete local application source and makes a new
`main` the default while preserving all original branch names and tips. It does
not publish binaries, component packs, model weights or a release tag. Source
acceptance is distinct from complete native desktop and redistribution acceptance.
The latter remain incomplete and retain their original verifier/workflow gates.

The starting GitHub baseline is `1dfcf47d7d2d7156bd30415bd618155eaa44d4b4`;
the integrated local starting source is `e6940669cabdaf64c6ba4dc739eae8e0b68295bb`.
The operator's immutable plan pins the final repaired candidate C and tree T.
New test logs and receipts belong to their actual source identities. The 911-test
September 9 checkpoint and the 81 focused planning tests are historical evidence,
not passing results for subsequently changed source.

## Application acceptance contract

- The image and its exact `image.ext.txt` companion form one owned file set.
  Unrelated `.xmp`, `.json` or `image.txt` files are outside that contract.
- GUI Undo forwards the complete token, with journal-owned identity checks.
  Replacements, missing authority, occupied restore paths and changed companions
  are preserved. Worker admission failure must retain the Undo entry.
- Primary sorting settles before optional metadata. Child errors do not undo a
  successful primary result or erase its Undo. Metadata revisions never move backward.
- Delayed results from an earlier source generation settle durable history but
  cannot repopulate the current view. Duplicate results are idempotent.
- Move Auto Advance off keeps a bounded read-only display snapshot until navigation;
  it never permits another mutation against a removed source. Undo restores normal
  interaction only when the original source view is still selected.
- Escape exits Zen/fullscreen before closing. Shortcut tests use real Qt dispatch.
- Confidence uses the active component path: default 0.5, range 0..1, at most ten
  labels, scores >= threshold. The deprecated `model_path` does not bypass component
  verification. HUD AI status is not a persistent tag display.

All acceptance uses private explicit profiles and disposable images. Existing
previews, components and original collections are not modified by qualification.

## Optional component availability

All five component inventories, archive hashes, versions and helper sources remain
present. Active remote download URLs are absent because their proposed release
assets were never published. The UI reports download unavailable and retains
verified import, enable/disable, verification, rollback, removal and recovery.
Existing compatible packs remain usable; no first-run download is performed.
Installed-manifest verification excludes only the retired transport URL from
identity comparison. Archive/payload hashes, versions, entry points, capabilities
and compatibility requirements must still match exactly; saved manifests are not rewritten.

For local use, import a pack matching the catalogue's exact archive hash, or use
Import existing model for the pinned model and labels. The CPU model is data-only.
Executable packs target Linux x86_64/glibc >=2.39, enforce reader isolation, and
require compatible Landlock support (ABI 3 for codecs/CPU; ABI 6 for NVIDIA).
NVIDIA additionally requires a compatible host driver; the pack never installs it.
Unsupported environments fail explicitly. See [reader containment](reader-containment.md).

Rebuilding packs is separate from importing published identities. The build tools
require their explicit model inputs and qualified HEIF/RAW decoder build directories;
use their `--help` interfaces. Rebuilt bytes receive new hashes/descriptors and need
fresh qualification. Historical decoder/build receipts may reference the original
machine's captured inputs; a new build must capture its own inputs and cannot simply
relabel those receipts. No binary delivery or distribution eligibility follows
from committing build recipes. See [publication gate](publication-gate.md).

The following URLs are retained only as historical proposed locations. They are
not operational downloads and must not be copied back into active catalogue fields
without completing the binary/component release gates.

| Component | Preserved version | Historical proposed URL (unpublished) |
| --- | --- | --- |
| `ai.mobilenet-v2` | `1.0.0+20260909.r10` | https://github.com/SyrisBruhh42/Image_Sorter/releases/download/components-2026-09-09-r10/ai.mobilenet-v2-1.0.0+20260909.r10-any.tar.gz |
| `codec.camera-raw` | `1.0.0+20260909.r10` | https://github.com/SyrisBruhh42/Image_Sorter/releases/download/components-2026-09-09-r10/codec.camera-raw-1.0.0+20260909.r10-linux-x86_64.tar.gz |
| `codec.heif-avif` | `1.0.0+20260909.r10` | https://github.com/SyrisBruhh42/Image_Sorter/releases/download/components-2026-09-09-r10/codec.heif-avif-1.0.0+20260909.r10-linux-x86_64.tar.gz |
| `provider.onnx-nvidia` | `1.0.0+20260909.r10` | https://github.com/SyrisBruhh42/Image_Sorter/releases/download/components-2026-09-09-r10/provider.onnx-nvidia-1.0.0+20260909.r10-linux-x86_64.tar.gz |
| `viewer.animation-multipage` | `1.0.0+20260909.r10` | https://github.com/SyrisBruhh42/Image_Sorter/releases/download/components-2026-09-09-r10/viewer.animation-multipage-1.0.0+20260909.r10-linux-x86_64.tar.gz |

## Guarded source-only procedure

Use `scripts/source_integration.py`; do not run the legacy full-release controller
for this narrower scope. The source-only plan and receipts are typed separately
and cannot satisfy native `cutover/qualified` or binary publication evidence.

1. Inspect live repository identity, actor, refs, PRs, rules and settings; retain
   an immutable before-state. Stop on unexpected drift. Independently restore a
   recovery bundle containing original branch/PR heads and the final candidate.
2. Pin C/T and successful local checks plus attributable independent review. Create
   a unique immutable plan; keep all evidence outside the checkout.
3. Publish and verify new annotated archive tags under a non-`v*` prefix. Create
   `main` at baseline B and stage the exact C on `integration/unified-main` using
   create-only conditions. The original default remains unchanged.
4. Open one integration PR C into main. Require actual matching Actions CI and its
   receipt, including PR head/base, tested tree, run/attempt and all required jobs.
   Main alone requires a PR, strict current-base CI bound to Actions, resolved
   conversations and no force updates/deletion. Zero formal approval reviews avoids
   sole-owner self-approval deadlock; independent review evidence is still required.
5. Merge only C with merge-commit strategy and no bypass. Verify merge M parents
   exactly [B,C], tree(M)=T, preserved ancestry and every original ref unchanged.
6. Require actual M CI and an independent fresh checkout before changing the
   repository default setting to main. No branch is renamed or deleted.
7. Record final refs, checks, archive/bundle identities, review, PR and default
   metadata. Preserve all historical PRs; record GitHub-generated status changes.

`inspect`, `plan`, `run`, `resume` and `receipt` expose their complete options via
`--help`. Each executed phase requires the exact canonical plan hash. Execution
uses a local lock and append-only intent/result journal. It changes no repository-wide
permissions. A lost response is reconciled against actual state before retrying.

Local evidence is a `source-integration-evidence` JSON record bound to C/T, with
`result: passed`, attributable reviewers, a substantive reviewed `pr_body`, and
hashed evidence for `source-tests`, `lint`, `dependencies`, `package-smoke` and
`independent-review`. The controller checks every referenced file and pins the
exact PR body as part of the plan.

`verify-merged` additionally takes `--merged-validation`: a
`source-merged-validation` record with `operation: source-integration`, M/T,
`fresh_checkout: true`, `checkout_path`, the same five check categories and passing
results. The clone must have its own Git object repository and remain clean at M/T.
The controller rechecks that clone and evidence before switching the default.

If hosted CI exposes a defect before any merge attempt, commit a repair descended
from C, run acceptance again, and inspect live state. A new immutable plan may use
`--continue-plan` and `--continue-evidence-root` to preserve the prior plan/journal
and require the exact owned main=B, integration=oldC and still-open PR. It permits
only a normal fast-forward of that integration branch and an evidence-backed PR
body update; new archives and exact repaired-head CI are required. Unexpected
external drift stops continuation. After a merge attempt, reconcile its outcome;
a merged source requires a separately qualified correction PR.

Before the default switch, a failure leaves the original default at B. After the
switch, rollback changes only the default setting back to that original branch,
provided its recorded identity still matches. Keep main, the candidate and evidence;
subsequent fixes use new qualified commits/PRs. Never force-reset or delete branches.

## Required validation and completion

Required automated checks include the full applicable source suite, lint,
dependencies, real Qt event regression cases, fresh-clone/non-editable wheel,
standalone/AppImage smoke, Linux Python 3.10–3.12, Xvfb, Windows/macOS smoke and
pinned CPU inference. Skipped or missing required jobs are failures. CI uploads
receipts only; source integration does not invoke release publication.

Controller tests must cover original-ref preservation, unexpected main, stale
head/base/CI, wrong merge parents/tree, interrupted requests, default-only rollback
and rejection of release/native-success operations or evidence substitution.

Completion means a normal clone opens verified M on protected main, original
branches are preserved, source checks pass and reproduced defects have regression
evidence. The final receipt explicitly distinguishes source complete, binary
publication not performed, and native release qualification incomplete. Until a
receipt records those outcomes, this document describes the contract, not a claim
that remote execution succeeded.


## Hosted qualification findings and continuation

The initial repaired candidate `e9a2bee201f1db093434725e84ef84ae604eba40`
passed 1,020 local tests, but its first hosted runs exposed additional defects.
Those failed runs remain historical evidence; their skipped dependent jobs do
not count as acceptance. A new immutable continuation is required for the repairs.

- Five standalone verification/build recipes used `hashlib.file_digest`, which
  does not exist in Python 3.10. They now use bounded streaming SHA-256 without
  adding uncaptured recipe dependencies or changing integrity expectations.
- A platform-path test asserted Linux XDG locations on Windows/macOS. Tests now
  exercise all native path contracts explicitly; native jobs use private profiles
  and verify production path resolution stays inside them.
- Fixed 0.5-second UI construction assumptions failed under hosted load. Real
  worker barriers, thread identity and Qt event delivery now prove asynchronous
  behavior. Deliberately synchronous implementations fail those regressions.
- Windows lacks this mutation service's qualified Unix-socket, locking and
  directory-durability implementation. Requests previously hung at socket startup;
  direct metadata writes reached unsupported directory sync after file work.
  Windows sorting, Undo and metadata writes now fail before admission/file changes.
  Direct service startup and retained-material cleanup also refuse unsupported
  execution; recovery admission errors leave records/history intact and reach the
  UI as a clear status rather than an uncaught callback exception.
  The experimental Windows smoke verifies actual image review, settings, preserved
  bytes/history, responsive refusal of 200 requests, and no stranded helper or queue.
  Linux/POSIX smoke retains all 200 actual move/Undo transactions and disk-full
  preservation tests. Directory durability and POSIX reader isolation are unchanged.

Windows read-only scratch attributes are omitted for private reader copies so
successful reads can be cleaned up; original permissions are unchanged. Native
reader containment on Windows/macOS remains unqualified. Full Windows file
mutation support requires a separately designed and qualified backend and is not
asserted by source integration. All mandatory hosted job groups still must pass.

## Second hosted checkpoint and native repairs

Candidate `3655b424bb1b4fd1edfe2cf4c7a8192526c3c418` passed 1,077 local tests with no skips. Hosted push run `34788260404` and PR run `34788262580` passed Python 3.10, 3.11 and 3.12 (1,076 tests each; packaging runs separately), X11 (12 tests), pinned CPU inference, and actual wheel/standalone/AppImage build and smoke checks. Both aggregate checks failed because each native matrix had one remaining failure; none of their jobs were skipped. Preserve those failed receipts as evidence.

The macOS isolated profile produced a 160-byte Unix-socket path, exceeding the native pathname limit and leaving 200 operation IDs waiting for a helper that could not bind. The repaired non-Linux POSIX address uses a canonical journal hash under a checked system temporary directory and a private 0700 directory owned by the current user. It rejects unsafe ownership, modes and symlinks. Journal/profile locations and the Linux abstract address remain unchanged. Under the held journal lock, startup removes only an existing socket owned by the same user, preserving unrelated files. Startup failure is visible and pending journal identities remain intact; no unsuccessful operation is manufactured as a success.

Windows decoded-image presentation was blocked by a mismatch between viewer cache identity and reader component-store construction. The viewer now uses the same default-store contract as the reader, retaining the profile root in its cache partition. Component payload, compatibility, permission and execution checks are unchanged. Windows durable mutation remains explicitly unavailable.

Native smoke retains its original test selection and adds real pathname-socket cases on macOS. Local tests also exercise endpoint preservation, real long-profile move/Undo, actual generated-image presentation and failed-admission bookkeeping. These repairs require a new immutable continuation, successful exact-candidate hosted checks, actual-merge checks and independent checkout validation before default switching. They do not qualify a full native release.

## Third hosted checkpoint: Windows timestamp semantics

Candidate `8c22967497e485d2e1dde9d9daa139327ba23797` passed 1,115 local tests with no skips. Hosted macOS smoke passed on both push `34789541285` and PR `34789543143` (82 tests each). Windows PR smoke passed (51 tests), but push smoke failed three presentation/identity cases while 48 passed. A passing duplicate run does not erase this reproduced failure or authorize merging the defective candidate.

The captured reader replies contained correct pixels and the same device, inode, size and modification time as the viewer. Their fifth timestamp differed: the Windows pathname API supplied creation time while the handle API used by the reader supplied change time. CPython 3.12's [pathname implementation](https://github.com/python/cpython/blob/v3.12.10/Modules/posixmodule.c) maps that field to birth time, while the [handle implementation](https://github.com/python/cpython/blob/v3.12.10/Python/fileutils.c) follows its handle-stat path. These are incompatible meanings, not a reason to add timing tolerance or accept changed files.

The Windows cache identity repair reads the same complete handle identity as the reader. It retains strict stable pathname checks before/after the read, strict five-field handle checks, and matching pathname/handle device, inode, size and modification time. It returns all five handle fields unchanged. POSIX identity handling is unchanged. Deterministic tests cover creation/change-time disagreement, modification, replacement and descriptor closure, alongside generated-image native checks. Every new exact-candidate and actual-merge gate still applies. No native release success or binary publication is asserted.

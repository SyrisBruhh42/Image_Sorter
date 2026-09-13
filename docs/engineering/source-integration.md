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

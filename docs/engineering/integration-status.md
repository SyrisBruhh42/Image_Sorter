# Integration Status

Last updated: 2026-09-07 UTC. This file records evidence, not a claim that native
desktop or GitHub administration gates have already completed.

## Candidate

- Local branch: `integration/unified-main`
- Original baseline: `1dfcf47d7d2d7156bd30415bd618155eaa44d4b4`
- Eight-head merge checkpoint: `d233f64994e32742dcde17ab4a45674e7d08d8f1`
- Repair checkpoints:
  - `190641a25fe0` — launch and decoder lifecycle
  - `76ca195747da` — file transactions, metadata, and recovery
  - `0145b32d88a0` — nonblocking optional AI and component boundaries

All eight Jules task heads are reachable from the candidate through explicit merge
commits. Packaging, CI, and repository-documentation cleanup are grouped in the
next candidate commit after the repair checkpoints above.

## Verified so far

| Gate | Evidence |
| --- | --- |
| Archive | 35 annotated tags; verified/restore-tested full bundle; SHA-256 recorded in `branch-disposition.md`. |
| Unified non-packaging suite | 152 passed, 1 packaging test deselected; one expected unwritable-settings fallback warning. |
| Focused packaging/platform/manifest tests | 18 passed, 1 packaging test deselected after workflow/build repairs. |
| Actual packaging-marked test | 1 passed, 7 deselected; produced and started the PyInstaller executable. |
| Ruff | `ruff check src tests scripts build_desktop.py` passed after packaging/CI changes. |
| Wheel outside checkout | Built `imagesorter-0.1.0.dev0`; installed in a fresh environment; CLI help and packaged icon resolved outside the source tree. |
| Native KDE Plasma 5.27/X11 | NOT RUN; requires execution on the target machine using `platform-acceptance.md`. |

## Remaining cutover gates

1. Commit the qualified documentation/packaging/CI cleanup.
2. Publish the integration candidate, qualify the published SHA, and merge it while
   retaining the eight task-head parents.
3. Rename the GitHub default to `main`, update the local remote tracking state, and
   rerun required checks on the actual default.
4. Resolve historical and task PRs with accurate retained/ported/superseded notes.
5. Revalidate every exact remote branch SHA and prune only after the archive gate in
   `branch-disposition.md` is satisfied. Produce a final deletion receipt.

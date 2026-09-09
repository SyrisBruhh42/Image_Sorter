# Branch and Pull-Request Disposition

This is the in-repository summary of the 2026-09-07 unification audit. It replaces
an earlier 26-branch report that did not include all surviving refs or the eight
parallel Jules projects.

This section is a **historical snapshot**, not current cutover authorization or
new qualification evidence. The 35-tag hash below identifies the earlier archive
bundle. The later supplied unified-candidate bundle has 36 tags and SHA-256
`3bb852cc3ae6cd9bf8eb383e150d83a469ca7a1b732c2976b03fa86b7c5b6281`.
Preserve both identities without substituting one for the other. Current execution
uses the immutable inventory, per-PR evidence, and receipts described in
[`cutover-runbook.md`](cutover-runbook.md).

## Frozen pre-unification state

- Baseline/default tip: `1dfcf47d7d2d7156bd30415bd618155eaa44d4b4`
- Surviving remote branches inventoried: **34**
- Pull requests inventoried: **33**
- Additional divergent identity: PR #3's recorded head differs from its surviving
  branch tip; both were preserved.
- Annotated local archive tags: **35**, covering every branch tip and the divergent
  PR head.
- Verified bundle SHA-256:
  `99e328134141cce7c863ac6ec402032ee12fba9a780ec8233accf6e89e321c01`
- Restore result: a separate clone from the bundle passed full `git fsck`; every
  archived object resolved to the recorded SHA.

The complete full-SHA ledger and PR discussion snapshot are retained in the audit
evidence outside the repository. No branch was deleted to obtain this report.

## Eight parallel project heads

| PR | Task | Original head | Integration merge |
| --- | --- | --- | --- |
| #28 | P00 branch control | `9f08b08fc8c8` | `55a4f1d130ed` |
| #30 | P03 settings | `e6ee3cde89b8` | `054e20074293` |
| #26 | P04 AI contract | `8a20b39f65df` | `e859c6a438e6` |
| #31 | P01 mutation engine | `94d45414ed9d` | `a82dfcc6ba90` |
| #33 | P05 decoding | `77d5a4b85c0e` | `19702de45bd0` |
| #29 | P02 UI/controller | `8c5cb9045c76` | `63e32c8b0089` |
| #32 | P06 Linux delivery | `6b1f396e2692` | `1b74d34fda05` |
| #27 | P07 CI/release | `36a42ec4c28a` | `d233f64994e3` |

Each original head is a parent/ancestor of the integration candidate; the project
history was not squashed.

## Historical PR outcomes

The 18 historical open PRs (#2, #5–#9, #12–#17, and #19–#24) were reviewed by
intent rather than merged wholesale. Their relevant behavior is covered by the
current canonical implementation and regression areas below:

The wording and table below record that earlier review's claims. Each relevant
claim must be reverified against the repaired candidate and linked to current
passing evidence before closing a PR as retained/ported. A code-area reference
alone is not a passing test or proof of behavioral equivalence.

| Historical intent | Current disposition/evidence area |
| --- | --- |
| QoL, HUD, keyboard, accessibility, visual review | Current `ui_main.py` / `ui_settings.py`; accessibility, controller, decoding, and launch tests. |
| Paths, settings, logging, portability | XDG/platform path helpers, atomic settings recovery, packaged resources, platform-startup tests. |
| Async workers, collision safety, Undo, UI recovery | Unified operation result, versioned Undo, source locks, sidecar-aware transactions, journal recovery, safety/concurrency tests. |
| AI verification, providers, preprocessing, metadata | Pinned digests/revisions, cancellation, provider fallback, strict preprocessing, consolidated metadata writer, optional-AI tests. |
| Source layout, frozen startup, AppImage, releases | PEP 621 `src` layout, unrelated-CWD wheel/frozen tests, verified appimagetool, artifact manifest/checksum workflows. |
| Test harness | Current isolated tests retained; generated coverage data and obsolete package layouts were not restored. |

Per-PR disposition for closure is explicit:

| PR | Outcome | Current evidence/replacement |
| --- | --- | --- |
| #2 | Superseded and retained | Navigation, preloading, zoom lock, zen mode, settings, worker retries, and AI behavior exist in the canonical `src` layout; temporary patch scripts are intentionally rejected. |
| #5 | Retained/ported | The HUD is integrated into `MainViewer`; shortcut precedence and accessibility are covered by P02/accessibility tests. The separate `EnterpriseHUD` module/name is superseded. |
| #6 | Retained/ported | XDG/platform paths, validated settings recovery, logger fallback, provider ranking, and hardware scan remain under current modules. |
| #7 | Superseded/ported | One current CI/release pipeline builds from PEP 621 metadata and hashes explicit artifacts. Old nested specs, duplicate license/readme, placeholder version file, and empty model directory are rejected. |
| #8 | Superseded/ported | Bounded worker/decoder lifecycles, cancellation, strict model verification, and provider fallback replace the earlier engine and example digest. |
| #9 | Retained/ported | Equivalent and expanded test areas exist under `tests/`; the checked-in coverage database, duplicate dependency file, and old package topology are rejected. |
| #12 | Retained/ported | XDG validation, packaged resources, and display-backend preservation have automated tests. Native Plasma acceptance remains openly NOT RUN. |
| #13 | Retained/ported | PEP 621 `src` layout, entry point, resources, and unrelated-working-directory wheel validation remain. Checked-in runtime settings and duplicate requirements are removed. |
| #14 | Retained/ported | Pinned SHA-256 model/labels, bounded image preprocessing, non-finite output handling, provider fallback, and safe metadata tests remain; metadata has one canonical writer. |
| #15 | Superseded/ported | Source serialization, cross-device preservation, bounded stop, view generations, versioned operation results, strict Undo, and persistence failures are covered by current contracts. |
| #16 | Superseded/ported | Current spec/icons, artifact builder, accessibility/concurrency/security tests, and fatal build failures replace the older scripts. |
| #17 | Retained/ported | Bounded LRU cache, clipping overlay, focus isolation, accessible announcements, modal settings, zoom, and zen behavior remain in the unified UI. |
| #19 | Retained/ported | `run_app.py`, frozen resource lookup, spec imports, and an actual PyInstaller startup test remain; the desktop builder was renamed to avoid shadowing the standard `build` frontend. |
| #20 | Retained/ported | Exact pinned model/labels, background index handling, revalidation, cleanup, UI status, cancellation, and optional network integration remain. |
| #21 | Retained/ported | Collision reservation, no-overwrite, provenance, cross-filesystem preservation, custom trash, strict Undo, and sidecar-set extensions remain. |
| #22 | Retained/ported | Type repair/clamping, unknown-field preservation, atomic save, unique corrupt backup, XDG fallback, and logger fallback remain. |
| #23 | Superseded/ported | Pending-operation IDs and queue/Undo recovery remain, now settled only by the canonical versioned `operation_result` instead of multiple order-sensitive legacy signals. |
| #24 | Superseded/ported | CLI launch failures, backend preservation, Linux tests, and real artifact CI remain. Native Plasma acceptance remains openly NOT RUN; old non-fatal packaging behavior is rejected. |

The seven already merged PRs (#1, #3, #4, #10, #11, #18, #25) retain their merged
history. Historic feature branches may be closed as superseded/ported; that status
must not be misrepresented as a direct GitHub merge.

## Final prune gate

The controller-backed procedure in [`cutover-runbook.md`](cutover-runbook.md)
supersedes the operational list below. It adds exact expected-SHA deletion,
independent archive restoration, current qualification, and interrupted-operation
reconciliation. The older list remains context for the original disposition.

An old branch may be removed only after all of these are true:

1. The remote tip still matches the frozen ledger.
2. Its intended behavior has an explicit disposition above and corresponding tests
   pass on the published candidate.
3. The integration has become the GitHub default branch named `main`.
4. Its PR is resolved and no open PR uses it as a base.
5. An archive ref is remotely available, or the repository owner explicitly
   accepts the independently restore-tested bundle as the durable recovery source.

The current default branch and the integration branch are never deletion targets
until the `main` cutover is verified. See
[`integration-status.md`](integration-status.md) for live qualification state.

# Publication evidence boundary

The current optional catalogue is **local qualification only**. It is not a
distribution approval. Neither successful runtime tests, an upstream download
URL, a permissive-looking model label, nor `legal_clearance: true` resolves
missing redistribution grants or corresponding-source duties.

`scripts/publication_guard.py` checks preserved evidence against actual bytes.
It is an engineering consistency gate, not an automated legal opinion or proof
that a reviewer's judgment is correct. A responsible reviewer must assess the
actual applicable terms and record unresolved questions honestly. Where a grant
or source/build relationship remains uncertain, publication stays blocked.

## What is required

The shipped catalogue must explicitly become `publication_status:
"evidence-gated-release"` only after its descriptors and obligations are ready
for review. This value alone never passes. Supply a preserved JSON manifest:

```json
{
  "schema_version": 1,
  "kind": "imagesorter-publication-evidence",
  "source_sha": "full final Git commit SHA",
  "source_tree": "full final Git tree SHA",
  "catalog_sha256": "SHA-256 of exact shipped catalogue bytes",
  "unresolved": [],
  "deliveries": [],
  "support_archive": {"path": "release-sources-and-terms.tar.gz", "sha256": "full SHA-256"}
}
```

This is a structural illustration, deliberately not passing evidence. Every
reference uses a regular preserved file and its full SHA-256. Relative reference
paths resolve beside the record containing them.

There must be exactly one delivery each for `wheel`, `onedir`, and `appimage`,
plus every shipped `component-id@version`. Each delivery contains:

- `artifact`, `closure`, and `review` file/hash references;
- optional `delivery_prefix`, ending in `/`, for an archive's top directory;
- for AppImage, an `appimage_runtime` reference to the preserved standalone
  runtime whose SHA-256 is pinned in the captured `build_desktop.py` source;
- for a pack, an explicit `source_adoption` record described below.

The closure is the collector's `runtime-license-source-inventory`. Run
`scripts/collect_runtime_licenses.py --help` for collection inputs. `--delivery`
adds the artifact hash, but that label is not proof of content correspondence.
The gate independently enumerates wheel/tar members, or parses the AppImage ELF
boundary and reads its SquashFS with `unsquashfs`. It checks complete file sets,
bytes, permissions and symlink targets against the collected final runtime.
Unsupported special files, escaping paths, unknown entries, stale embedded
build identities, different catalogues, and missing native source mappings fail.

The project's source archive must match both its complete SHA-256 inventory and
the declared Git tree reconstructed from regular-file bytes and executable
bits. A self-consistent archive falsely labeled as the candidate therefore
does not pass. Core artifacts must identify the exact final candidate commit
and tree. GPL/LGPL source requirements cannot be suppressed by a false override;
source correspondence and all unresolved obligations are checked again.

Each `review` is a `distribution-obligations-review` identifying the exact
`artifact_sha256`, `reviewer`, `reviewed_at`, and empty `unresolved`. Its
`obligations` must cover every source/package identity exactly once. Each row
has its `id`, `disposition: "reviewed-for-distribution"`, empty `unresolved`,
nonempty preserved `terms` references, and a `decision` JSON reference. The
decision identifies `component_id`, exact `artifact_sha256`, attributable
`reviewer`, `reviewed_at`, substantive `basis`, and unresolved questions.
An AppImage additionally requires the `appimage-runtime` obligation. These
records retain accountable judgments; they do not manufacture rights.

The `ai.mobilenet-v2` closure additionally requires exactly two `asset_mappings`:
`mobilenetv2.onnx` with role `model-weights`, and `labels.txt` with role
`model-labels`. Each row gives `path`, the exact catalogue file `sha256`, and a
distinct `source_id` present in the source inventory. Those two source obligations
must each have a review decision also naming the exact `asset_sha256` and
`asset_role`. A generic ONNX Runtime or application licence review cannot cover
unmapped model/label rights. An explicit component-descriptor collection mode is
required for packs' `provenance.json` rather than inventing an application
`build_identity.json`; do not manually clear unresolved collector findings.

The support archive must actually contain every required preserved project
archive, source archive, package notice and reviewed terms document by hash.
Keeping required material only on a developer workstation is insufficient.
Use safe unique release filenames. The downstream publisher receives only the
exact verified artifacts and support archive, never an unrestricted wildcard.
Ordinary CI still builds and tests the actual binaries, but uploads only build,
readiness and source receipts. Downloadable binary assets are publication too;
the normal CI artifact upload is not an alternate route around this gate.

## Avoiding the catalogue hash cycle

Build packs from clean source **P**, preserve its complete source closure, then
adopt the resulting immutable descriptors into clean final application **C**.
P and C need not have the same commit: embedding an archive's hash in its own
build source would create a circular requirement. The pack's provenance must
identify clean P and every required helper/build source file. Every captured
`src/` helper hash must agree with C; omitting a helper or supplying only a
documentation hash fails. The original P build recipe remains preserved even
when unrelated C build tooling has evolved.

Each pack delivery records `source_adoption` with `kind:
"pack-source-adoption"`, `pack_source_sha`, `pack_source_tree`,
`final_source_sha`, `final_source_tree`, and `unchanged_helper_files` equal to
the complete captured helper path/hash mapping. This explicitly records the
relationship; it does not excuse changed helper source. A helper change means
rebuild/requalify the pack and adopt its new descriptors.

## AppImage compatibility metadata

The supported ELF64 little-endian type2 boundary is derived from the pinned
runtime's actual ELF section table, not by searching for a filesystem magic
string. The delivered executable prefix must be byte-for-byte identical to the
source-pinned standalone runtime. The builder alone may restore the named
16-byte `.digest_md5` field to the trusted input's original zeros, refusing any
other changed prefix byte, then fsync and reverify the whole artifact.

This optional legacy MD5 metadata is intentionally unpopulated: the pinned
appimagetool's digest implementation has undefined buffer-gap/tail behavior.
SHA-256 is the artifact integrity authority. The pinned runtime's mount/launch
path does not consume that field. A local disposable-copy probe verified offset,
help, application help, read-only FUSE mount and teardown; it does not replace
full final packaged qualification. Format/source references are retained in
`scripts/appimage_format.py`.

## Enforcement and current stop condition

Normal CI tests are never skipped or relabeled as passing. The release workflow
runs its existing tests/builds, then requires `dist/publication-evidence.json`,
`dist/native-qualification.json`, and every corresponding preserved input before
uploading publishable artifacts. Both manifests must identify the same exact
core artifact and optional pack hashes. Headless CI does not satisfy the native
gate, and qualifying one build does not authorize publishing another same-source
build with different bytes.
The native build record for each format must include a hash-verified `delivery`
reference, including the onedir archive. Native `artifact.path` identifies its
installed launcher and is not confused with a wheel or archive's delivery hash.
Executable descriptors must specify `reader_policy_version: 1` and
`min_landlock_abi: 3` for codecs/viewers or `6` for NVIDIA; the data-only model
pack specifies both fields as null. Old pre-policy helper packs cannot remain
release rollback choices. The clean pack source inventory includes
`src/imagesorter/reader_sandbox.py`.

Each native `COMPONENT:` operator observation must preserve `reader_results`
references to `reader-result-observation` envelopes, each containing a `fixture`
file/hash reference and a `reply` file/hash reference to the full returned helper
metadata. Every fixture-bearing reply must explicitly succeed (`ok:true`, with
`error` absent or null) and contain its actual `input_sha256`:
an exact lowercase 64-character SHA-256 string matching the preserved fixture.
A missing digest, a substituted fixture, or a probe-only reply cannot qualify;
no helper-native input hash may be invented. Every recorded format fixture needs a
corresponding actual result, exact executed helper identity and enforced
`reader_isolation` policy. GPU records require ABI 6+, exact provider identity,
NVIDIA-only declared devices, and denied external metadata/network/mutation IPC;
CPU/codecs require ABI 3+ with no device-write exception. Model CPU results must
identify the exact model/labels. Fallback alone cannot qualify a GPU helper.
New-release GPU results additionally require the strict, additive
`cuda_precision` policy documented in `qualification-evidence.md`: explicit
TF32 disablement, effective session options observed before and after inference,
and disabled internal provider fallback. Legacy r9 receipts remain historical
evidence; they cannot satisfy this new precision gate. Their archives are not
deleted, but the new host uses its explicit CPU fallback when a retained GPU
helper cannot attest the policy. All changed helper/core bytes require new
catalogue, source-adoption, artifact and qualification identities. Never change
an old receipt or widen its predeclared numerical tolerance to make it pass.
It currently fails closed because the local catalogue and unresolved evidence
are not publication eligible. There is intentionally no automatic fabricated
review or success fallback. A reviewed evidence-staging workflow is still
required before a real release can proceed.

Every remote cutover execution phase, including maintenance-policy changes and
remote archive publication, requires both complete local native qualification
and this distribution gate first. Read-only inspect/planning/dry-run and separate
local archive/bundle/restore work remain available. Supply `--qualification` and
`--distribution` to each executed phase. Native observations cannot substitute
for distribution evidence; distribution records cannot substitute for native
qualification. No remote mutation is authorized merely by passing either gate.

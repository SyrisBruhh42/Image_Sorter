# Licensing and binary-source distribution

Status: engineering controls implemented; final artifact/source closure and the
model/GPU distribution decisions remain release gates. This document is not a
legal opinion or a claim that every dependency has been cleared.

## Application source and combined binaries are different deliverables

Image Sorter's original source remains MIT, with its existing copyright and
permission notice unchanged. The selected PyPI PyQt6 6.11.0 package declares
`GPL-3.0-only`; its Qt 6.11.2 runtime package declares LGPLv3. MIT source is
GPL-compatible, but a frozen application incorporating GPL PyQt is not an
MIT-only binary distribution. The selected distribution route is to meet GPLv3
conditions for the combined binary while retaining each component's own notices.
No commercial PyQt licence is assumed and no binding migration is authorized by
this document. [Riverbank's licensing explanation](https://www.riverbankcomputing.com/commercial/license-faq)

Publish the full applicable licence texts, copyright notices and corresponding
source beside each exact binary. Do not substitute a link to a moving repository
for the source used to produce that binary. Do not issue a future written source
offer: this workflow accompanies delivery with the materials themselves. Preserve
the installed Python/Qt/native library licence conditions as well as application
source, packaging scripts, patches, configurations and build instructions.

## What the current evidence establishes

| Runtime/input | Verified engineering observation | Distribution prerequisite or unresolved point |
| --- | --- | --- |
| PyQt6 6.11.0 / Qt 6.11.2 | Installed package texts are GPL-3.0-only / LGPLv3. PyQt sdist and official Qt source module archives have been preserved and hash checked. | Reconcile final shipped Qt modules, bundled third parties, packager patches/configuration and exact source correspondence; include GPL/LGPL texts and rebuild instructions. |
| PyQt6-sip 13.12.0 | BSD-2-Clause text and matching release sdist are available. | Preserve the required notice; record source/build provenance limits without inventing a permissive-licence source duty. |
| Pillow 12.3.0 | MIT-CMU wrapper plus a substantial bundled-library notice file and two upstream SBOMs. | Preserve all relevant native notices/source, not just the wrapper's licence. Upstream SBOM entries can describe optional build features, not installed code. |
| Pillow optional libimagequant | Upstream SBOM lists GPL libimagequant, but the inspected wheel reports `libimagequant=False` and has no such shared library. | Do not assert that this optional component is shipped merely from the generic upstream SBOM. Recheck each final artifact. |
| Pillow FriBiDi | The inspected host resolves FriBiDi 1.0.13 while the upstream component SBOM lists 1.0.16. | Record actual shipped or host-loaded bytes/version; an upstream SBOM is not proof of the runtime version or linkage. |
| ONNX Runtime 1.29.0 / GPU variant | MIT licence and extensive ThirdPartyNotices are present. | Preserve exact variant/native notices and record provenance; apply source duties to components where their actual licences require them. Runtime code licence does not license model weights. |
| FlatBuffers 25.12.19 | Its wheel lacks a package-local licence file; the actual ORT ThirdPartyNotices includes `google/flatbuffers` and Apache-2.0 text. | Explicitly map the aggregate notice to FlatBuffers; do not report a whole-pack omission when the notice is present elsewhere. |
| Original rawpy 0.27.1 / LibRaw 0.22.1 wheel | MIT wrapper and LGPL-2.1 LibRaw text are present; runtime and compiled version report 0.22.1. | Historical input, superseded by the controlled 0.22.2 rebuild; keep prior evidence but do not publish the old decoder as the repaired one. |
| Custom rawpy 0.27.1 / LibRaw 0.22.2 wheel | Exact source rebuild passed runtime/compiled version and prior configuration-flag checks; LCMS is rebuilt from current stable 2.19.1 source. Five actual ELF files are the wrapper, LibRaw, LCMS2, libjpeg-turbo and libgomp. GPL demosaic flags remain false. | Select LibRaw's LGPL-2.1 alternative, preserve full source/configuration/relink materials, and requalify the nine advertised still-camera formats. OpenMP's exact distributor runtime and GCC Runtime Library Exception require their own source/notice mapping. |
| AppImage type2 runtime 20251108 | Exact release binary/source/build-run identities, pinned libfuse 3.15.0 plus its local patch, and SquashFuse 0.5.2 sources are preserved. Static mimalloc linkage is present in the Makefile but omitted from the upstream aggregate notice. | Include mimalloc and all actual static-library terms, libfuse LGPL2.1 corresponding source and a usable static relink route. Historical Alpine source recipes are not an exact installed-package lock; expired build logs are an explicit provenance limitation. |
| Original pillow-heif 1.7.0 wheel | Its bundled notice explicitly identifies GPLv2 binary wheels containing x265 4.2, LGPL libheif 1.23.3 and libde265 1.1.2. | Superseded for runtime distribution by the independently built decoder wheel; keep the old build only as historical evidence. |
| Decoder-only HEIF replacement | Exact pinned sources, private build, dynamic closure, HEIF/HEIC decoding and Pillow AVIF probes verified. Runtime is extension + libheif + libde265; no x265. | Include exact corresponding source, LGPL texts, configuration, receipt and rebuild/relink materials. The built-in LGPL mask encoder remains; no HEVC/AV1 encoder is available. |
| NVIDIA CUDA/cuDNN libraries | Exact wheel EULAs distinguish proprietary redistribution rights and restrictions from ORT's MIT terms. | Map every shipped file to a permitted redistributable, preserve notices and binary bytes, and resolve compatibility/aggregation and recipient terms before publication. Process isolation alone does not establish compatibility. |
| Pinned MobileNet ONNX weights | Exact conversion repository commit has no LICENSE/model card. Its config refers to Google MobileNet; that card says `license: other`. | No affirmative redistribution grant for these exact converted weights was established. Obtain a defensible grant/provenance chain or approve a separately licensed replacement/re-export and requalify. |
| Pinned PyTorch hub labels | Exact repository tree lacks LICENSE/COPYING/NOTICE. | Do not infer a licence from the organization name. A torchvision BSD source is an alternative, but two labels differ and changing/normalizing it requires explicit provenance and qualification updates. |

The decoder recipe's source versions and effective LGPL headers are preserved
with its result. The original wrapper's blanket wheel statement must not be
reused as the effective licence of the custom native build. Conversely, removing
x265 does not remove LGPL duties or establish HEVC patent clearance.

Qt's own documentation emphasizes module-specific and third-party licence
conditions; a single `LGPL` package field is not the complete native graph.
[Qt licensing and third-party code](https://doc.qt.io/qt-6/licensing.html)

The NVIDIA cuDNN text inspected in the preserved wheel conditions distribution on
additional application functionality, application-only access and consistent
terms, and forbids subjecting its SDK to specified open-source obligations.
These are publication prerequisites, not authorization to relicense NVIDIA
binaries under GPL or MIT. A separately distributed helper may be relevant to the
analysis but does not decide it. [NVIDIA CUDA licence terms](https://docs.nvidia.com/cuda/eula/index.html)

Model conclusions are intentionally narrow: public availability and an Apache
licence on a training-code repository do not establish rights in converted
weights. The reviewed Google model card itself describes the model's licence as
`other`. [Model card](https://huggingface.co/google/mobilenet_v2_1.0_224),
[exact converted-model repository](https://huggingface.co/onnx-community/mobilenet_v2_1.0_224-ONNX/tree/f7f884d9505b4c69f8a260d9967ff7791bafa498)

## Collection and source manifest contract

`scripts/collect_runtime_licenses.py` resolves runtime dependencies from a
specified environment, with optional extras excluded unless explicitly included.
Environment-only packages are recorded separately and are not automatically
declared shipped. Licence texts and supplier SBOMs are copied byte-for-byte. The
final frozen runtime receives a full hash/symlink inventory and ELF dependency
inventory. Matching an ELF hash to a wheel proves byte provenance, not its entire
licence or statically linked source graph.

Use a fresh evidence directory outside the source checkout and runtime:

```sh
.venv/bin/python scripts/collect_runtime_licenses.py \
  --site-packages /absolute/build-env/lib/python3.12/site-packages \
  --runtime-root /absolute/final/ImageSorter \
  --analysis-toc /absolute/captured/build/ImageSorter/Analysis-00.toc \
  --project-root /absolute/clean/qualified/checkout \
  --source-map /absolute/reviewed-source-map.json \
  --output /absolute/new/license-and-source-evidence \
  --require-complete
```

For a component helper, supply `--component-descriptor /absolute/descriptor.json`
and `--delivery /absolute/exact-pack.tar.gz`, and select its actual runtime roots
with repeatable `--runtime-package` arguments. Enabled extras are resolved, for
example `--runtime-package 'onnxruntime-gpu[cuda,cudnn]'`; unknown extras fail.
Non-model helpers require explicit package roots, so the base application's Qt
dependencies cannot silently become an optional helper's package inventory.
The data-only `ai.mobilenet-v2` pack defaults to no Python runtime packages.
Do not include PyInstaller, setuptools or other
build tools as runtime merely because they share a build environment. The
PyInstaller bootloader is actually delivered and therefore must have its own
native-source mapping, licence/exception and matching build source. Its exception
does not waive obligations of other libraries. [PyInstaller licence and exception](https://pyinstaller.org/en/stable/license.html)

Explicit component mode verifies the descriptor's entire file/hash/size/mode
inventory and archive hash, then reads its inventory-bound `provenance.json`.
It requires the exact clean **pack-build source P** under `--project-root`, with
the provenance's HEAD and captured helper/recipe hashes matching that source.
Missing required helpers, including `reader_sandbox.py`, dirty build source or a
different component ID cannot pass. Packs do not invent the core application's
`build_identity.json`: their record is `component_identity`, while the base
artifact gate still requires its real embedded build identity. Publication
independently parses the delivery archive and verifies the later explicit P→C
relationship to the final application/catalogue commit; the collector does not
substitute C's source archive for the pack's actual build source.

The source map has `schema_version: 1`, a `components` array and
`native_mappings`. Each component contains a unique `id`, exact `version`,
`license_expression`, `relationship`, `correspondence`, `review`, and `archives`.
Runtime Python component IDs use normalized distribution names. Each archive has
a simple `filename`, a complete `sha256`, an authoritative HTTPS `url` and/or a
preserved local `path`. Downloads are verified before acceptance; existing
evidence directories are never overwritten.

`correspondence: upstream-version-matched` means the version matches but exact
binary build source is unproved. Only a completed review of exact inputs,
modifications, build/install configurations and provenance may use
`verified-build-inputs`; record named `reviewer`, timestamp `reviewed_at` and
concrete `basis`. This is an attributed review record, not cryptographic proof
that a person or upstream assertion is truthful.

Corresponding-source hard gates apply to GPL/LGPL components and components
explicitly marked `corresponding_source_required: true` after an applicable
obligation review. A false value cannot silently disable a GPL/LGPL duty.
Permissive packages do not acquire a blanket exact source-to-wheel provenance
requirement merely because the collector can record one. Preserve their required
notices and identify any separate bundled-library obligations. Missing optional
provenance is a limitation to disclose, not by itself a new legal prohibition.

Each native mapping key is the exact runtime-relative path and its value contains
the exact `sha256` and a nonempty `components` list covering the native source
graph. Do not silently map a bundled libjpeg or static AV1 codec only to Pillow.
Unmapped or changed native binaries leave the licence inventory incomplete. Include
externally packaged Python, compiler runtimes and any collected host libraries;
do not conflate a file actually shipped in the artifact with an unshipped system
library. External DT_NEEDED entries are dependencies, not proof those files are
shipped; host-only glibc and driver files remain outside the shipped inventory.
Platform exemptions require an explicit applicable review, not a filename heuristic.

`notice_aliases` can map a package such as FlatBuffers to an aggregate notice.
Each alias contains the runtime owner `package`, original environment-relative
`original` path, exact `sha256`, and an identifying `section` that must exist in
those bytes. The collector preserves that complete text and records attribution.
An alias does not create missing source or a new licence grant.

Non-code assets use a source-map `asset_mappings` array. Each entry has a
runtime-relative `path`, exact `sha256`, `role`, and `source_id` naming one of the
manifest's `components`. For `ai.mobilenet-v2`, exactly two separate source IDs
are mandatory: `mobilenetv2.onnx` with role `model-weights`, and `labels.txt` with
role `model-labels`. The collector checks both actual bytes and descriptor hashes.
A library's licence review cannot stand in for either asset's grant. The
publication review must separately bind each source decision to `asset_sha256`
and `asset_role`, retain its applicable terms and explicitly resolve its rights
question. Merely supplying these technical mappings establishes no grant; the
currently pinned weights and labels remain unresolved as recorded above.

The collector snapshots application source from the exact checkout, including
tracked and nonignored implementation files. It hashes the same bytes written
into the source archive and rejects concurrent changes. Dirty source or an
embedded artifact build identity that does not match the clean source blocks
completion. Final runtime inventory is necessarily post-build; a preliminary
notice collection is not a claim about final shipped closure.

`engineering_complete` is true only when the specified technical inventory has
no unresolved entries. `legal_clearance` always says `not-asserted`. Review the
report and underlying licences before publication; do not transform the former
into the latter. A correct but incomplete report is an expected useful output.

The optional `--analysis-toc` is parsed as data, never executed. It correlates a
shipped ELF only when the captured input's bytes exactly match. For host-derived
files it preserves installed OS-package/source-version metadata, package
copyright files and referenced common licence texts. Package-manager ownership
is provenance evidence, not an upstream build attestation. Files merely named in
DT_NEEDED but not bundled do not trigger host-package collection.

`scripts/acquire_distro_sources.py --inventory INVENTORY --output NEW_DIRECTORY`
can acquire exact Ubuntu source versions identified by that shipped-file
inventory. Its APT configuration, signed source indexes, keyring copy, cache,
logs and downloaded source sets stay entirely in the new evidence directory; it
never installs packages, extracts/builds source, enables host source repositories
or edits system configuration. The default copyright-text selection is a
conservative acquisition convenience, not licence adjudication; use explicit
`--source-package` selections after review. An unavailable old source version
remains reported, never silently replaced by a newer version. Preserve `.dsc`,
original tarballs and distro patch/debian tarballs together.

## Artifact-side layout and publication gate

Each binary should contain `licenses/`, `sboms/` and
`DISTRIBUTION-NOTICE.txt`, visibly accessible to recipients. Publish a versioned
source sidecar associated with the exact binary hash, containing:

- `runtime-license-inventory.json`, full native/file inventory and approved
  source manifest;
- `project-source.tar.gz` and its exact source inventory;
- original upstream source archives, all local patches and build/install
  configurations;
- full third-party notices and the collector/decoder/helper/application rebuild
  instructions and scripts;
- dependency locks, build provenance and enough materials to rebuild the
  corresponding combined binary and relink modified LGPL components.

Include this source sidecar and its hash in the release manifest. Inspect the
actual binary contents after inserting notices; regenerate the final artifact
hash/build/native evidence as needed. Do not insert a report containing its own
binary hash inside that same binary and create a hash cycle. Embedded notices
and a separately hashed final inventory avoid that cycle.

Release publication remains blocked when the report is incomplete, native source
ownership is unmapped, a grant is unresolved, notices are missing, or source/build
identity has drifted. Hosted test success does not override these prerequisites.

## Decision blockers versus remaining collection work

The user cannot cure a missing third-party grant merely by approving publication.
The remaining decisions are narrowly scoped:

- **Exact model weights:** no affirmative redistribution grant for the pinned
  converted ONNX bytes was established. Obtain a defensible grant/provenance
  chain, or approve a specifically licensed replacement/re-export with renewed
  prediction, provider and lifecycle qualification. A training-code repository
  licence alone is insufficient.
- **Exact labels:** retain a defensible grant or applicable rights analysis for
  the pinned PyTorch-hub list, or approve a separately licensed source and explicit
  handling of its two differing labels. Do not silently normalize or substitute
  strings while claiming the original asset identity.
- **NVIDIA distribution:** an attributable review must apply the retained exact
  CUDA/cuDNN terms to every shipped redistributable and the chosen separate-helper
  distribution arrangement. It must resolve applicable recipient and
  compatibility conditions. This is not a commercial-PyQt assumption or a claim
  that process isolation itself answers the licensing question.

Other unfinished gates are engineering work, not blanket requests for legal
waivers: collect the final artifact's actual native/notices/source closure,
reconcile the exact Qt/PyQt/GTK and decoder source/build materials, exercise usable
LGPL replacement/relink routes (including static AppImage libfuse), assemble the
accompanying source sidecar, and re-run the final clean-source/native/distribution
checks. Thirty-eight exact distributor source sets and the decoder/runtime source
collections are already preserved, but preliminary dirty-source inventories do
not stand in for final closure. If exact obligated source/configuration cannot be
recovered, seek it from the distributor or produce a controlled source rebuild;
do not convert an unobservable historical build into a verified correspondence
claim. Missing optional provenance for permissive components remains a disclosed
limitation unless an actual licence obligation requires more.

## Exercising LGPL modification and relinking rights

The stock catalogue deliberately rejects altered helper files. That security
check is not a licence prohibition on modification. To use modified LGPL code:

1. Extract the accompanying matching source; retain an untouched copy.
2. Modify and rebuild the library as a shared library with the preserved
   configuration. Rebuild pillow-heif/rawpy/Qt bindings as appropriate against
   the modified library; retain the applicable notices.
3. Rebuild the helper with the substituted wheel/shared library and produce its
   complete new file/hash descriptor.
4. Rebuild the open-source Image Sorter application with a source-controlled
   catalogue containing that descriptor and any required trusted history.
5. Run the rebuilt application/helper in a separate profile, including native
   and destructive-operation safety tests before touching valuable files.

Recipients may also debug modified libraries using appropriate relinking tools;
no terms in this project prohibit reverse engineering to debug such
modifications. Changing SONAMEs or RPATHs can require rebuilding dependents.
Directly editing an installed signed/hash-verified pack is expected to fail its
integrity check, so it is not the supported substitution procedure.

The decoder build preserves exact libheif/libde265 sources, configuration and
dynamic dependency evidence. Its `REBUILD-AND-RELINK.md` is supplemented by this
application/catalogue rebuilding path. Source availability must be a delivered
fact, not an assumption that upstream will remain reachable.

The independent RAW recipe, `scripts/build_raw_decoder.py`, builds rawpy 0.27.1
against pinned LibRaw 0.22.2, with private LCMS2/JPEG/JasPer/zlib build inputs.
It preserves both executable recipe files, source archives, notices, hash-locked
Python build tools, compiler versions, CMake caches and build/configure logs.
`--inputs PRIOR_BUILD_DIRECTORY` rebuilds from preserved source/tool-wheel inputs
without downloading them. No host packages or configuration are installed.
The repaired wheel targets Linux x86_64 CPython 3.12; its actual manylinux tags
are in the wheel filename/metadata. A recorded host toolchain is not a claim of
bit-for-bit identity across machines, and a build receipt is not native format
qualification or distribution clearance.

LibRaw 0.22 removed old video-camera decoders. Its CMake configuration still
reports `REDCINECODEC=true` when JasPer was detected, although the resulting
library has no JasPer linkage. This is legacy configuration telemetry, not a
supported RedCine feature. JasPer source is retained as a build input, not
asserted to be shipped in the custom wheel. Native dependency lists and actual
format fixtures take precedence over that flag. [LibRaw release notes](https://www.libraw.org/download)

The custom RAW wheel's OpenMP preimage is Ubuntu `libgomp1`
`14.2.0-4ubuntu2~24.04.1`, from source package `gcc-14` at that exact version.
The corresponding descriptor, original source and distributor patch archive
were acquired in the isolated distributor-source collection. Include these and
the actual GCC runtime licence/exception notices in its source sidecar; the
rawpy MIT notice does not replace them. Auditwheel changes runtime names/RPATHs,
so retain the repair log and before/after source mapping, not only a claim that
the final bytes are identical to the original OS library.

The first RAW rebuild inherited LCMS 2.11 from rawpy's upstream Linux recipe;
that was not an introduced downgrade, but it was unnecessary inherited age.
The accepted replacement uses the pinned LCMS 2.19.1 source release. Its source
header and runtime API deliberately retain the encoded version `2190`; that
API alone cannot distinguish the 2.19.1 hotfix from 2.19. The source archive hash
and captured build provide that distinction. Upstream supports its latest
release for security fixes. [LCMS releases](https://github.com/mm2/Little-CMS/releases),
[LCMS security policy](https://github.com/mm2/Little-CMS/blob/master/SECURITY.md)

## AppImage executable runtime materials

`scripts/collect_appimage_runtime_sources.py` preserves the exact selected
runtime, debug/signature/key assets, release/build metadata, complete runtime
source and its hash-pinned libfuse/SquashFuse source inputs. It also preserves
build-era Alpine source recipes/patches and musl, zstd, zlib and mimalloc source
and notices. No runtime, upstream container/chroot build, or package installer
is executed by this collector. The collected signature is not described as
verified without a separate trusted-key verification record.

For the selected release, the original build log endpoint returns HTTP 410.
The recipe used moving `alpine:3.21` and unversioned package inputs. The preserved
Alpine commit immediately preceding that build is useful reconstruction material,
not proof of the precise installed static archive bytes. Do not invent missing
logs or treat missing permissive-library provenance as a blanket source duty.
Libfuse's exact source and local patch, by contrast, are explicitly pinned by the
release's own recipe. Its library/include/Meson files have LGPL2.1 terms; other
files in its source package are GPL2. This does not mean that every GPL tool in
the source package was linked into the launcher. [Exact runtime source](https://github.com/AppImage/type2-runtime/tree/dd6cebedcbddde9c82f89b011e8e1d40b6e43868)

The retained static relink instructions require an isolated disposable build
environment and the modified library plus runtime source/build materials. Do not
run the upstream privileged chroot script on the workstation: upstream itself
warns about its host risk. A collected recipe is not proof that the modified
library relink was exercised; record that test before claiming the delivery route
complete. AppDir contents and the executable prefix have separate source/licence
closures, and both accompany the delivered AppImage.

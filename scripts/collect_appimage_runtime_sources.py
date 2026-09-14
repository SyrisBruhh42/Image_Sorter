"""Preserve pinned AppImage runtime terms/source and expose provenance limits.

This downloads public evidence into a new directory; it never runs the runtime,
upstream build/chroot scripts or package installers. It does not clear a release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

import build_heif_decoder as shared

RECIPE = Path(__file__).read_bytes()
HELPER = Path(shared.__file__).read_bytes()
COMMIT = "dd6cebedcbddde9c82f89b011e8e1d40b6e43868"
APORTS = "590fd1489813473277cf110fd88b1c3888d19367"
RUNTIME_SHA = "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
RUNTIME_SIZE = 944632
SOURCES = {
    "type2-runtime": (
        "20251108",
        f"https://codeload.github.com/AppImage/type2-runtime/tar.gz/{COMMIT}",
        "f5fec23be76e50e2445ed2d018bac49b367490fc483c62c4637e99ec705d27ba",
        "exact-release-source",
    ),
    "libfuse": (
        "3.15.0",
        "https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz",
        "70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0",
        "hash-pinned-in-exact-runtime-build-recipe",
    ),
    "squashfuse": (
        "0.5.2",
        "https://codeload.github.com/vasi/squashfuse/tar.gz/refs/tags/0.5.2",
        "db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3",
        "hash-pinned-in-exact-runtime-build-recipe",
    ),
    "alpine-aports": (
        APORTS,
        f"https://codeload.github.com/alpinelinux/aports/tar.gz/{APORTS}",
        "143327809737a2bdb6339c845ba860858aaac6299a211fba07eb03730956aaa7",
        "historical-source-recipe-before-build-not-installed-package-lock",
    ),
    "musl": (
        "1.2.5",
        "https://musl.libc.org/releases/musl-1.2.5.tar.gz",
        "a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4",
        "upstream-version-in-runtime-debug-and-historical-aports",
    ),
    "zstd": (
        "1.5.6",
        "https://codeload.github.com/facebook/zstd/tar.gz/refs/tags/v1.5.6",
        "30f35f71c1203369dc979ecde0400ffea93c27391bfd2ac5a9715d2173d92ff7",
        "historical-aports-not-binary-package-attestation",
    ),
    "zlib": (
        "1.3.1",
        "https://zlib.net/fossils/zlib-1.3.1.tar.gz",
        "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23",
        "historical-aports-not-binary-package-attestation",
    ),
    "mimalloc": (
        "2.1.7",
        "https://codeload.github.com/microsoft/mimalloc/tar.gz/refs/tags/v2.1.7",
        "0eed39319f139afde8515010ff59baf24de9e47ea316a315398e8027d198202d",
        "historical-aports-not-binary-package-attestation",
    ),
}
ASSETS = {
    "runtime-x86_64": RUNTIME_SHA,
    "runtime-x86_64.debug": "1f3c4a8439e344d74b61bfd0610b89e8499453791bbc3c5b4ae38c181fe54942",
    "runtime-x86_64.sig": "de8262d60b5f82bcff11b77598aedef86d2c375fc246d6a675f6cac68b6dfda7",
    "signing-pubkey.asc": "db8b615eb5bbf5e8418d52906b4a492960926370e2c5041ad6acaccda56ef0c6",
}


def selected_materials(archive: Path, output: Path, name: str) -> list[dict]:
    """Copy source notices/build materials as regular data, never extract links."""
    rows = []
    seen = set()
    with tarfile.open(archive) as package:
        for member in package:
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            if (
                len(parts) < 2
                or parts[0] == "/"
                or any(p in {"", ".", ".."} for p in parts)
                or "\\" in member.name
            ):
                raise ValueError("Unsafe source material path")
            relative = PurePosixPath(*parts[1:])
            if str(relative) in seen:
                raise ValueError("Duplicate source material path")
            seen.add(str(relative))
            text_name = relative.name.upper()
            notice = any(
                token in text_name
                for token in ("LICENSE", "COPYING", "COPYRIGHT", "NOTICE", "GPL2.TXT")
            )
            recipe = name == "type2-runtime" or (
                name == "alpine-aports"
                and any(
                    str(relative).startswith(p + "/")
                    for p in (
                        "main/musl",
                        "main/zstd",
                        "main/zlib",
                        "community/mimalloc2",
                    )
                )
            )
            if not (notice and name != "alpine-aports" or recipe):
                continue
            if member.size > 20 * 1024 * 1024:
                raise ValueError("Oversized source material")
            target = (
                output / ("notices" if notice else "build-source") / name / relative
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            data = package.extractfile(member).read()
            with target.open("xb") as stream:
                stream.write(data)
            rows.append(
                {
                    "path": str(target),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "source_member": member.name,
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    for name in ("sources", "assets", "metadata"):
        (output / name).mkdir()
    (output / "collect_appimage_runtime_sources.py").write_bytes(RECIPE)
    (output / "build_heif_decoder.py").write_bytes(HELPER)
    metadata = {}
    for name, url in {
        "release": "https://api.github.com/repos/AppImage/type2-runtime/releases/tags/20251108",
        "build-run": "https://api.github.com/repos/AppImage/type2-runtime/actions/runs/20037536435",
        "build-jobs": "https://api.github.com/repos/AppImage/type2-runtime/actions/runs/20037536435/jobs",
        "historical-aports": f"https://api.github.com/repos/alpinelinux/aports/commits/{APORTS}",
    }.items():
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        target = output / "metadata" / f"{name}.json"
        target.write_bytes(data)
        metadata[name] = {
            "url": url,
            "path": str(target),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    release = json.loads(Path(metadata["release"]["path"]).read_text())
    build = json.loads(Path(metadata["build-run"]["path"]).read_text())
    if (
        release["target_commitish"] != COMMIT
        or build["head_sha"] != COMMIT
        or build["conclusion"] != "success"
    ):
        raise ValueError("Upstream release/build identity changed")
    release_assets = {row["name"]: row for row in release["assets"]}
    assets = {}
    for name, digest in ASSETS.items():
        row = release_assets[name]
        if row.get("digest") != "sha256:" + digest:
            raise ValueError("Runtime release asset digest changed")
        path = output / "assets" / name
        shared.fetch(row["browser_download_url"], path, digest)
        assets[name] = {
            "path": str(path),
            "sha256": digest,
            "size": path.stat().st_size,
        }
    if assets["runtime-x86_64"]["size"] != RUNTIME_SIZE:
        raise ValueError("Runtime size changed")
    materials, sources = [], []
    for name, (version, url, digest, relationship) in SOURCES.items():
        suffix = ".tar.xz" if url.endswith(".tar.xz") else ".tar.gz"
        archive = output / "sources" / (name + "-" + version + suffix)
        shared.fetch(url, archive, digest)
        rows = selected_materials(archive, output, name)
        materials.extend(rows)
        sources.append(
            {
                "id": name,
                "version": version,
                "archive": {"path": str(archive), "sha256": digest},
                "url": url,
                "relationship": relationship,
                "material_files": rows,
            }
        )
    # Collect the read-only logs endpoint outcome. Expired logs are a provenance
    # limitation, not an invented package lock and not a blanket permissive duty.
    log_outcome = {"status": "not-attempted", "reason": "GitHub CLI unavailable"}
    if shutil.which("gh"):
        result = subprocess.run(
            [
                "gh",
                "run",
                "view",
                "20037536435",
                "--repo",
                "AppImage/type2-runtime",
                "--log",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
        path = output / "metadata/build-log-response.txt"
        path.write_bytes(result.stdout)
        log_outcome = {
            "returncode": result.returncode,
            "path": str(path),
            "sha256": shared.sha(path),
            "status": "retrieved" if result.returncode == 0 else "unavailable",
        }
    (output / "REBUILD-AND-RELINK.md").write_text(RELINK)
    report = {
        "schema_version": 1,
        "kind": "appimage-runtime-source-evidence",
        "collection_complete": True,
        "runtime": assets["runtime-x86_64"],
        "runtime_commit": COMMIT,
        "assets": assets,
        "metadata": metadata,
        "sources": sources,
        "material_files": materials,
        "build_logs": log_outcome,
        "recipe_sha256": hashlib.sha256(RECIPE).hexdigest(),
        "helper_recipe_sha256": hashlib.sha256(HELPER).hexdigest(),
        "distribution_clearance": "not-asserted",
        "verified_facts": [
            "Release binary matches exact pinned SHA/size and public release asset digest.",
            "Runtime release/build metadata identifies the pinned source commit.",
            "Exact release recipe hash-pins libfuse3.15.0 and squashfuse0.5.2 and supplies the libfuse patch.",
            "Runtime Makefile statically links mimalloc although aggregate upstream LICENSE omits it.",
        ],
        "provenance_limitations": [
            "Moving alpine:3.21/apk inputs are not locked by exact installed package metadata in the preserved release recipe.",
            "Historical aports snapshot and compatible upstream sources are not asserted to be the exact original static archive build inputs.",
            "A preserved upstream signature is not marked verified without a separate trusted-key verification result.",
        ],
        "distribution_requirements": [
            "Include the complete actual licence/notice texts, including mimalloc, not a MIT-only label.",
            "Include exact modified libfuse source, runtime work-that-uses-library source and sufficient build/relink materials under LGPL2.1.",
            "Review and test the delivered rebuild/relink route; this collector does not assert it was exercised.",
            "Review runtime vulnerabilities separately from licence/provenance inventory; no unsupported exploitability claim.",
        ],
    }
    shared.write_json(output / "runtime-source-evidence.json", report)
    print(
        json.dumps(
            {
                "output": str(output),
                "collection_complete": True,
                "runtime_sha256": RUNTIME_SHA,
                "source_sets": len(sources),
                "distribution_clearance": "not-asserted",
            },
            indent=2,
        )
    )


RELINK = """# AppImage runtime source, licence notices and static relinking

This set preserves AppImage type2-runtime release 20251108, source commit
dd6cebedcbddde9c82f89b011e8e1d40b6e43868, and the exact runtime binary/debug and
signature assets. It does not execute or rebuild them and is not legal clearance.

The launcher source is MIT. Its linked code also includes musl, libfuse,
SquashFuse, zstd, zlib and mimalloc. The upstream summary omits mimalloc even
though the exact Makefile links it; include its MIT notice. Retain the complete
source-package texts. Libfuse's LICENSE applies LGPL2.1 to include/, lib/ and
meson.build; other source-package files have GPL2 terms. Do not infer that all
libfuse tools or all GPL source files were linked into the runtime. The runtime
source patch is patches/libfuse/mount.c.diff and must accompany Libfuse3.15.0.

For the LGPL2.1 static-library route, accompany the binary with the modified
library source and the runtime work-that-uses-library source (or suitable object
materials), and the build/relink materials needed to modify Libfuse and relink.
No project term prohibits reverse engineering for debugging such modifications.
Use an isolated disposable container or VM: upstream explicitly warns that its
chroot scripts can affect a host. Never run those privileged chroot instructions
on the user's workstation as a shortcut. This collector does not run them.

Rebuild route: extract the runtime archive, retain its source version identifier,
extract libfuse3.15.0, apply the supplied mount.c patch, configure Meson for static
libraries and install into the isolated build environment. Build SquashFuse0.5.2
using its supplied autogen/configure files and static link setting. Supply the
musl, zstd, zlib and mimalloc libraries with their preserved notices and reviewed
Alpine build recipes/patches. Build src/runtime/Makefile with the recorded Clang
flags, preserving static-pie/data_sections.ld, then follow scripts/build-runtime.sh
for debug splitting, stripping and the AI type2 marker. All those exact scripts
are retained in build-source/type2-runtime and the source archive.

The original Docker recipe used a moving alpine:3.21 tag and unversioned apk
inputs. Release build logs may have expired; report the actual retrieval outcome.
The preserved historical aports commit was the latest 3.21-stable source commit
before the recorded release build time. It supplies a useful rebuild reference,
not proof of which exact package bytes the release job used. Exact upstream
libfuse/SquashFuse sources and the local patch are pinned by the original recipe;
do not downgrade those facts to a guess, or promote other package-version guesses
into verified source-to-binary correspondence. Permissive packages' provenance
limits do not invent blanket corresponding-source duties.

After modifying/relinking the runtime, rebuild the AppImage from its unchanged or
separately modified AppDir with the substituted runtime using the open-source
desktop builder's explicit runtime-file path. Regenerate archive hashes and run
native qualification. The source-controlled release guard must also be rebuilt
with the newly reviewed runtime digest/size/source materials; a stock guard's
rejection of unexpected bytes is an integrity control, not a prohibition on LGPL
modification. Verify an actual modified-library relink before claiming this
delivery route complete. Do not issue a future written source offer.
"""


if __name__ == "__main__":
    main()

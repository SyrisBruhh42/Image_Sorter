"""Build a private, dynamically linked HEIF decoder wheel; no host installation.

Requires Linux x86_64 CPython 3.12, CMake, Ninja, GCC/G++, pkg-config and readelf.
Pinned source/wheel inputs are hash verified. Host toolchain versions are recorded,
not claimed bit-for-bit reproducible across different toolchains. A fresh output
directory is required. All corresponding sources, notices and logs are retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import venv
import zipfile
from pathlib import Path

SOURCES = {
    "libde265": (
        "1.1.2",
        "d0bcab76380c079358a3156b3e3b37d17c00a078",
        "strukturag/libde265",
        "38cd323b746e4798bb605ed256f8574c463f8a1e2f44a9bd2bf69b4d8e4c3d4a",
    ),
    "libheif": (
        "1.23.3",
        "78c9746aea226b22885e8d35241353ce669c4ea5",
        "strukturag/libheif",
        "5de95dff732230c420cbd31828b02555429347965d480be2d8f9ac5308efd748",
    ),
    "pillow_heif": (
        "1.7.0",
        "f65a9ac77809609ad8ebb001c691b5a3ee01146b",
        "bigcat88/pillow_heif",
        "48acf9be9d1770848adbb0005a14cfbe3fb733394733eb23d21d7828d02f92ac",
    ),
}
# Hash-locked build tools and the Pillow dependency used only by the probe.
WHEELS = {
    "auditwheel==6.8.2": "05fd2dd4b743217cd5f66066adc95b03a079424b1f0a878ad1e40fe2cdea4704",
    "patchelf==0.19.1.0": "a8f6331ccf40c345507279f755f4a38c2cb00b9efda746fd43c17713cce0aba4",
    "setuptools==84.0.0": "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670",
    "wheel==0.48.0": "3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab",
    "packaging==26.3": "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
    "pyelftools==0.33": "f215ad5f47d3f1373a21496a6c9e0707c622840d0622f23ff7ce08678b020036",
    "pillow==12.3.0": "78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91",
}
DISABLED_HEIF = (
    "X265",
    "X264",
    "OpenH264_DECODER",
    "AOM_DECODER",
    "AOM_ENCODER",
    "DAV1D",
    "SvtEnc",
    "RAV1E",
    "KVAZAAR",
    "UVG266",
    "VVDEC",
    "VVENC",
    "JPEG_DECODER",
    "JPEG_ENCODER",
    "OpenJPEG_ENCODER",
    "OpenJPEG_DECODER",
    "FFMPEG_DECODER",
    "OPENJPH_ENCODER",
    "UNCOMPRESSED_CODEC",
    "WEBCODECS",
    "LIBSHARPYUV",
    "HEADER_COMPRESSION",
    "EXAMPLES",
    "EXAMPLE_HEIF_THUMB",
    "EXAMPLE_HEIF_VIEW",
    "GDK_PIXBUF",
    "FUZZERS",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def fetch(url: str, target: Path, expected: str) -> None:
    if len(expected) != 64:
        raise ValueError(f"Invalid pinned SHA-256 for {target.name}")
    with urllib.request.urlopen(url, timeout=60) as source, target.open("xb") as dest:
        shutil.copyfileobj(source, dest)
    if sha(target) != expected:
        raise ValueError(f"Input hash mismatch: {target}")


def run(args: list[str], cwd: Path, env: dict[str, str], log: Path) -> str:
    with log.open("ab") as stream:
        stream.write(("\nCOMMAND " + json.dumps(args) + "\n").encode())
        stream.flush()
        result = subprocess.run(
            args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        stream.write(result.stdout)
        stream.flush()
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")
    return result.stdout.decode(errors="replace")


def unpack(archive: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        # Sources have no links; forbid them and special files to keep extraction bounded.
        if any(not (m.isfile() or m.isdir()) for m in members):
            raise ValueError(
                f"Source archive contains a link or special member: {archive}"
            )
        tar.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("Source must have one root directory")
    return roots[0]


def verify_capabilities(info: dict) -> None:
    # libheif always registers its internal uncompressed mask encoder. It is
    # LGPL library code, not an external HEVC/AV1 encoder or an x265 dependency.
    if (
        info.get("libheif") != "1.23.3"
        or info.get("HEIF")
        or info.get("AVIF")
        or info.get("decoders") != {"libde265": "libde265 HEVC decoder, version 1.1.2"}
        or info.get("encoders") != {"mask": "mask"}
    ):
        raise ValueError(f"Decoder-only capability assertion failed: {info}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or sys.version_info[:2] != (3, 12)
    ):
        raise SystemExit("This pinned recipe targets Linux x86_64 CPython 3.12 only")
    for tool in ("cmake", "ninja", "gcc", "g++", "pkg-config", "readelf"):
        if not shutil.which(tool):
            raise SystemExit(
                f"Required host tool unavailable: {tool}; no host package installation is attempted"
            )
    output = args.output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    for name in (
        "sources",
        "tool-wheels",
        "unpacked",
        "build",
        "prefix",
        "raw-wheel",
        "wheels",
        "notices",
    ):
        (output / name).mkdir()
    # Snapshot this executable recipe before any compilation; include it in delivery evidence.
    shutil.copyfile(__file__, output / "build_heif_decoder.py")
    env = dict(os.environ)
    for key in (
        "CFLAGS",
        "CXXFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
        "LIBRARY_PATH",
        "LD_LIBRARY_PATH",
        "CPATH",
        "CPLUS_INCLUDE_PATH",
        "C_INCLUDE_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "CMAKE_PREFIX_PATH",
        "PKG_CONFIG_PATH",
        "LIBHEIF_ROOT",
    ):
        env.pop(key, None)
    env.update(
        {
            "LC_ALL": "C.UTF-8",
            "SOURCE_DATE_EPOCH": "1788739200",
            "PYTHONHASHSEED": "0",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_NO_CACHE_DIR": "1",
        }
    )
    log = output / "build.log"
    tools = {
        name: run([name, "--version"], output, env, log).splitlines()[0]
        for name in ("cmake", "ninja", "gcc", "g++", "pkg-config", "readelf")
    }
    write_json(
        output / "host-toolchain.json",
        {
            "tools": tools,
            "python": sys.version,
            "platform": platform.platform(),
            "libc": platform.libc_ver(),
        },
    )
    source_paths = {}
    records = []
    for name, (version, commit, repository, digest) in SOURCES.items():
        url = f"https://codeload.github.com/{repository}/tar.gz/{commit}"
        archive = output / "sources" / f"{name}-{commit}.tar.gz"
        fetch(url, archive, digest)
        source_paths[name] = unpack(archive, output / "unpacked" / name)
        records.append(
            {
                "name": name,
                "version": version,
                "commit": commit,
                "url": url,
                "path": str(archive),
                "sha256": digest,
            }
        )
        notices = output / "notices" / name
        notices.mkdir()
        for item in source_paths[name].rglob("*"):
            if item.is_file() and any(
                token in item.name.upper() for token in ("LICENSE", "COPYING", "NOTICE")
            ):
                target = notices / item.relative_to(source_paths[name])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
    write_json(output / "source-inputs.json", records)
    lock = output / "build-tools.lock"
    lock.write_text(
        "".join(f"{name} --hash=sha256:{digest}\n" for name, digest in WHEELS.items())
    )
    venv.EnvBuilder(with_pip=True).create(output / "venv")
    python = str(output / "venv/bin/python")
    env["PATH"] = str(output / "venv/bin") + os.pathsep + env["PATH"]
    run(
        [
            python,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--no-deps",
            "--require-hashes",
            "-r",
            str(lock),
            "-d",
            str(output / "tool-wheels"),
        ],
        output,
        env,
        log,
    )
    run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(output / "tool-wheels"),
            "--no-deps",
            "--require-hashes",
            "-r",
            str(lock),
        ],
        output,
        env,
        log,
    )
    prefix = output / "prefix"
    common = [
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        "-DBUILD_SHARED_LIBS=ON",
        "-DCMAKE_INSTALL_RPATH=$ORIGIN",
        "-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON",
    ]
    de265 = [
        "-DENABLE_DECODER=OFF",
        "-DENABLE_ENCODER=OFF",
        "-DENABLE_SDL=OFF",
        "-DENABLE_SHERLOCK265=OFF",
        "-DENABLE_INTERNAL_DEVELOPMENT_TOOLS=OFF",
        "-DWITH_FUZZERS=OFF",
        "-DENABLE_AVX512=OFF",
    ]
    heif = [
        f"-DCMAKE_PREFIX_PATH={prefix}",
        "-DWITH_LIBDE265=ON",
        "-DWITH_LIBDE265_PLUGIN=OFF",
        "-DENABLE_PLUGIN_LOADING=OFF",
        "-DBUILD_TESTING=OFF",
        "-DBUILD_DEVELOPMENT_TOOLS=OFF",
        "-DBUILD_DOCUMENTATION=OFF",
    ] + [f"-DWITH_{name}=OFF" for name in DISABLED_HEIF]
    for name, flags in (("libde265", de265), ("libheif", heif)):
        build = output / "build" / name
        run(
            ["cmake", "-S", str(source_paths[name]), "-B", str(build), *common, *flags],
            output,
            env,
            log,
        )
        run(
            [
                "cmake",
                "--build",
                str(build),
                "--parallel",
                str(max(1, min(args.jobs, 16))),
            ],
            output,
            env,
            log,
        )
        run(["cmake", "--install", str(build)], output, env, log)
        shutil.copyfile(build / "CMakeCache.txt", output / f"{name}-CMakeCache.txt")
    env.update(
        {
            "LIBHEIF_ROOT": str(prefix),
            "PKG_CONFIG_PATH": str(prefix / "lib/pkgconfig"),
            "LD_LIBRARY_PATH": str(prefix / "lib"),
            "LDFLAGS": f"-L{prefix / 'lib'}",
        }
    )
    run(
        [
            python,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            str(source_paths["pillow_heif"]),
            "-w",
            str(output / "raw-wheel"),
        ],
        output,
        env,
        log,
    )
    raw = list((output / "raw-wheel").glob("*.whl"))
    if len(raw) != 1:
        raise ValueError("Expected one built wheel")
    run(
        [
            str(output / "venv/bin/auditwheel"),
            "repair",
            "--plat",
            "manylinux_2_39_x86_64",
            str(raw[0]),
            "-w",
            str(output / "wheels"),
        ],
        output,
        env,
        log,
    )
    wheels = list((output / "wheels").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected one repaired wheel")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        native = [name for name in archive.namelist() if ".so" in Path(name).name]
        if (
            len(native) != 3
            or not any("libheif" in name for name in native)
            or not any("libde265" in name for name in native)
        ):
            raise ValueError(f"Unexpected native runtime closure: {native}")
        if any(
            any(bad in name.lower() for bad in ("x265", "x264", "aom", "dav1d"))
            for name in native
        ):
            raise ValueError("Unexpected encoder or non-HEVC library in decoder wheel")
    run(
        [python, "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        output,
        env,
        log,
    )
    env.pop("LD_LIBRARY_PATH", None)
    probe = run(
        [
            python,
            "-c",
            "import json,pillow_heif; print(json.dumps(pillow_heif.libheif_info(),sort_keys=True))",
        ],
        output,
        env,
        log,
    )
    info = json.loads(probe.strip().splitlines()[-1])
    verify_capabilities(info)
    write_json(output / "decoder-capabilities.json", info)
    fixture_root = source_paths["pillow_heif"] / "tests/images/heif_other"
    decode = run(
        [
            python,
            "-c",
            "import hashlib,json,sys; from pathlib import Path; from PIL import Image; import pillow_heif; pillow_heif.register_heif_opener(); rows=[]\nfor name in ('RGB_8_chroma444.heif','L_exif_xmp_iptc.heic'):\n p=Path(sys.argv[1])/name; im=Image.open(p); im.load(); rows.append({'file':name,'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':im.size,'mode':im.mode,'pixels_sha256':hashlib.sha256(im.tobytes()).hexdigest()})\np=Path(sys.argv[2])/'probe.avif'; Image.new('RGB',(24,16),(40,110,180)).save(p,format='AVIF'); im=Image.open(p); im.load(); assert im.size==(24,16); rows.append({'file':'probe.avif','source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':im.size,'mode':im.mode,'pixels_sha256':hashlib.sha256(im.tobytes()).hexdigest()}); print(json.dumps(rows))",
            str(fixture_root),
            str(output),
        ],
        output,
        env,
        log,
    )
    decode_records = json.loads(decode.strip().splitlines()[-1])
    write_json(output / "decode-probes.json", decode_records)
    runtime = Path(
        run(
            [python, "-c", "import sysconfig; print(sysconfig.get_paths()['platlib'])"],
            output,
            env,
            log,
        ).strip()
    )
    dependency_info = {}
    for member in native:
        path = runtime / member
        dependency_info[member] = {
            "sha256": sha(path),
            "dynamic": run(["readelf", "-d", str(path)], output, env, log),
        }
    write_json(output / "native-dependencies.json", dependency_info)
    # Sources and a rebuildable dynamic extension allow recipients to rebuild/relink
    # modified LGPL libraries. Frozen pack integration must retain the external .so
    # layout and include this source/notices/recipe set; isolation is not an exemption.
    (output / "REBUILD-AND-RELINK.md").write_text(
        """# Decoder-only HEIF corresponding source and relinking\n\nThis private build uses pillow-heif 1.7.0 (BSD-3-Clause), libheif 1.23.3 and\nlibde265 1.1.2 (LGPL-3.0-or-later). Full upstream notices are in notices/.\nThe upstream pillow-heif binary-wheel GPL notice describes its x265-containing\nwheels, not this separately built wheel. This build includes no x265, HEVC/AV1\nencoders or loadable codec plugins. Libheif's built-in LGPL mask encoder remains.\nAVIF is provided separately by the application's Pillow pack.\n\nThe exact source tarballs are in sources/, with commits/hashes in source-inputs.json.\nRun `python3.12 build_heif_decoder.py --output NEW_DIRECTORY` on Linux x86_64\nwith CMake, Ninja, GCC/G++, pkg-config and readelf installed. This downloads the\nsame hash-locked inputs; preserved tarballs/wheels permit an offline adaptation.\nAll exact compiler/configure/link commands and tool versions are retained. Host\ntoolchains are not pinned by container digest: cross-host bit identity is not claimed.\n\nTo exercise LGPL modification/relink rights, extract and modify the preserved\nlibrary source, rebuild shared libraries with the recorded CMake configuration,\nand rebuild the provided pillow-heif source against that private prefix. Re-run\nauditwheel repair to produce the new wheel and install it into a private environment.\nFor a frozen helper, rebuild its source-controlled helper with the substituted\nwheel. The externally stored .so files must remain replaceable; no restriction\non reverse engineering for debugging modifications may be imposed. Keep working\nbackups and use isolated tests; changing SONAME/RPATH requires relinking.\n\nDistribution must accompany this wheel/helper with the exact corresponding\nsource, notices and rebuild materials, provide appropriate prominent notices,\nand preserve the LGPL conditions. This is engineering evidence, not a legal\nopinion or clearance for HEVC patents or unrelated bundled dependencies.\n"""
    )
    receipt = {
        "schema_version": 1,
        "kind": "heif-decoder-build",
        "wheel": {"path": str(wheel), "sha256": sha(wheel)},
        "sources": records,
        "native_files": dependency_info,
        "capabilities": info,
        "decode_probes": decode_records,
        "runtime_prefix": str(runtime),
        "source_prefix": str(prefix),
        "toolchain": tools,
        "recipe_sha256": sha(output / "build_heif_decoder.py"),
        "log_sha256": sha(log),
        "licenses": {
            "pillow_heif": "BSD-3-Clause",
            "libheif": "LGPL-3.0-or-later",
            "libde265": "LGPL-3.0-or-later",
        },
        "complete": True,
    }
    write_json(output / "build-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()

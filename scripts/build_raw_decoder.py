"""Build rawpy 0.27.1 against fixed LibRaw 0.22.2 in a private prefix.

Linux x86_64 CPython 3.12 only. No host packages/configuration are changed.
Sources and Python build inputs are pinned; the host compiler is recorded, not
claimed reproducible across toolchains. This is a build/capability receipt, not
the application's nine-format or final native qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import posixpath
import shutil
import sys
import tarfile
import venv
import zipfile
from pathlib import Path

import build_heif_decoder as shared

# Captured before work starts, so edits during a long build cannot alter receipt identity.
RECIPE_BYTES = Path(__file__).read_bytes()
HELPER_BYTES = Path(shared.__file__).read_bytes()
SOURCES = {
    "rawpy": (
        "0.27.1",
        "https://files.pythonhosted.org/packages/f3/ae/c1c7816ed3f3cbf7ca284a37371243500f4eb39b6a32f7368b585c4ef5c4/rawpy-0.27.1.tar.gz",
        "3194d64ff690ac945e1a43237edae8a18f1f493751924de1ae2bcef473c0fb79",
        "MIT",
    ),
    "libraw": (
        "0.22.2",
        "https://codeload.github.com/LibRaw/LibRaw/tar.gz/b93f6e45c194f5df9b02a43b1af9a54b4f41f33f",
        "95df0a1e14ada99595632ca4556082fe4653cf74ce21ebdfb2f1d8729cab323d",
        "LGPL-2.1-only OR CDDL-1.0",
    ),
    "libraw-cmake": (
        "6e26c9e73677dc04f9eb236a97c6a4dc225ba7e8",
        "https://codeload.github.com/LibRaw/LibRaw-cmake/tar.gz/6e26c9e73677dc04f9eb236a97c6a4dc225ba7e8",
        "d0ecc870bb395ca05d4de61314d092437bb37afac1e17131cc64c58e57369a11",
        "GPL-2.0-or-later (build script)",
    ),
    "lcms2": (
        "2.19.1",
        "https://github.com/mm2/Little-CMS/releases/download/lcms2.19.1/lcms2-2.19.1.tar.gz",
        "bfc54f7bab59fbc921012014a8032e4cba4abd46db47d46b76416a8c0b2815c8",
        "MIT",
    ),
    "jpeg": (
        "3.1.3",
        "https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.1.3/libjpeg-turbo-3.1.3.tar.gz",
        "075920b826834ac4ddf97661cc73491047855859affd671d52079c6867c1c6c0",
        "BSD-3-Clause AND IJG AND Zlib",
    ),
    "jasper": (
        "4.2.5",
        "https://codeload.github.com/jasper-software/jasper/tar.gz/refs/tags/version-4.2.5",
        "3f4b1df7cab7a3cc67b9f6e28c730372f030b54b0faa8548a9ee04ae83fffd44",
        "JasPer-2.0",
    ),
    "zlib": (
        "1.3.1",
        "https://zlib.net/fossils/zlib-1.3.1.tar.gz",
        "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23",
        "Zlib",
    ),
}
WHEELS = {
    key: value for key, value in shared.WHEELS.items() if not key.startswith("pillow==")
}
WHEELS.update(
    {
        "Cython==3.3.0": "428fafed98ea26927000a287b4dfc9ef07339f56656a5329a34eaa593f79a4f8",
        "numpy==2.5.3": "b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3",
    }
)
FEATURES = {
    "DNGLOSSYCODEC": True,
    "DNGDEFLATECODEC": True,
    "OPENMP": True,
    "LCMS": True,
    "REDCINECODEC": True,
    "RAWSPEED": False,
    "DEMOSAIC_PACK_GPL2": False,
    "DEMOSAIC_PACK_GPL3": False,
    "X3FTOOLS": True,
    "6BY9RPI": True,
}
LIBRAW_FLAGS = [
    "-DENABLE_OPENMP=ON",
    "-DENABLE_LCMS=ON",
    "-DENABLE_JASPER=ON",
    "-DENABLE_EXAMPLES=OFF",
    "-DENABLE_RAWSPEED=OFF",
    "-DENABLE_DCRAW_DEBUG=OFF",
    "-DENABLE_X3FTOOLS=ON",
    "-DENABLE_6BY9RPI=ON",
    "-DLIBRAW_INSTALL=ON",
]


def unpack(archive: Path, destination: Path) -> Path:
    """Extract one bounded source tree, permitting only internal source symlinks."""
    destination.mkdir()
    with tarfile.open(archive) as package:
        members = package.getmembers()
        names = set()
        roots = set()
        for member in members:
            name = member.name.rstrip("/")
            parts = name.split("/")
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or any(p in {"", ".", ".."} for p in parts)
                or name in names
            ):
                raise ValueError("Unsafe/duplicate source member")
            names.add(name)
            roots.add(parts[0])
            if member.issym():
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), member.linkname)
                )
                if (
                    member.linkname.startswith("/")
                    or "\\" in member.linkname
                    or target.split("/")[0] != parts[0]
                ):
                    raise ValueError("Source symlink escapes source tree")
            elif not (member.isfile() or member.isdir()):
                raise ValueError("Source contains hard link or special member")
        if len(roots) != 1:
            raise ValueError("Source must have one root")
        # Also refuses extraction through a symlink ancestor, uid/gid and special modes.
        package.extractall(destination, filter="data")
    root = destination / roots.pop()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Source root is not a directory")
    return root


def verify_capabilities(info: dict) -> None:
    if (
        info.get("rawpy") != "0.27.1"
        or info.get("libraw") != [0, 22, 2]
        or info.get("libraw_compiled") != [0, 22, 2]
        # Upstream 2.19.1 is a hotfix and retains LCMS_VERSION 2190; its
        # source/archive identity, not this API alone, distinguishes the hotfix.
        or info.get("lcms2_encoded_version") != 2190
        or info.get("flags") != FEATURES
    ):
        raise ValueError(f"RAW version or preserved-feature assertion failed: {info}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--inputs",
        type=Path,
        help="Prior build directory for exact offline sources/tool-wheels",
    )
    args = parser.parse_args()
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or sys.version_info[:2] != (3, 12)
    ):
        raise SystemExit("This recipe requires Linux x86_64 CPython 3.12")
    for name in ("cmake", "ninja", "gcc", "g++", "make", "pkg-config", "readelf"):
        if not shutil.which(name):
            raise SystemExit(f"Missing tool: {name}; no host installation attempted")
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
    (output / "build_raw_decoder.py").write_bytes(RECIPE_BYTES)
    (output / "build_heif_decoder.py").write_bytes(HELPER_BYTES)
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
        "CMAKE_TOOLCHAIN_FILE",
        "PKG_CONFIG_PATH",
        "PKG_CONFIG_LIBDIR",
        "PKG_CONFIG_SYSROOT_DIR",
        "PKG_CONFIG",
        "RAWPY_BUILD_GPL_CODE",
        "RAWPY_USE_SYSTEM_LIBRAW",
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
            "PIP_CONFIG_FILE": os.devnull,
        }
    )
    log = output / "build.log"
    run = lambda command, cwd=output: shared.run(command, cwd, env, log)
    toolchain = {
        name: run([name, "--version"]).splitlines()[0]
        for name in ("cmake", "ninja", "gcc", "g++", "make", "pkg-config", "readelf")
    }
    shared.write_json(
        output / "host-toolchain.json",
        {
            "tools": toolchain,
            "python": sys.version,
            "platform": platform.platform(),
            "libc": platform.libc_ver(),
        },
    )
    paths, sources = {}, []
    for name, (version, url, digest, license_expression) in SOURCES.items():
        archive = output / "sources" / f"{name}-{version}.tar.gz"
        if args.inputs:
            supplied = args.inputs.resolve() / "sources" / archive.name
            if shared.sha(supplied) != digest:
                raise ValueError(f"Offline source hash mismatch: {supplied}")
            shutil.copyfile(supplied, archive)
        else:
            shared.fetch(url, archive, digest)
        paths[name] = unpack(archive, output / "unpacked" / name)
        sources.append(
            {
                "name": name,
                "version": version,
                "url": url,
                "path": str(archive),
                "sha256": digest,
                "license_expression": license_expression,
                "role": "build-tool" if name == "libraw-cmake" else "runtime-source",
            }
        )
        for item in paths[name].rglob("*"):
            if (
                item.is_file()
                and not item.is_symlink()
                and any(
                    t in item.name.upper()
                    for t in ("LICENSE", "COPYING", "NOTICE", "COPYRIGHT", "README.IJG")
                )
            ):
                target = output / "notices" / name / item.relative_to(paths[name])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
    shared.write_json(output / "source-inputs.json", sources)
    lock = output / "build-tools.lock"
    lock.write_text(
        "".join(f"{name} --hash=sha256:{digest}\n" for name, digest in WHEELS.items())
    )
    venv.EnvBuilder(with_pip=True).create(output / "venv")
    python = str(output / "venv/bin/python")
    env["PATH"] = str(output / "venv/bin") + os.pathsep + env["PATH"]
    if args.inputs:
        for wheel in (args.inputs.resolve() / "tool-wheels").glob("*.whl"):
            shutil.copyfile(wheel, output / "tool-wheels" / wheel.name)
    else:
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
            ]
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
        ]
    )
    prefix = output / "prefix"
    env.update(
        {
            "PKG_CONFIG_PATH": str(prefix / "lib/pkgconfig"),
            "PKG_CONFIG_LIBDIR": str(prefix / "lib/pkgconfig"),
            "LD_LIBRARY_PATH": str(prefix / "lib"),
        }
    )
    common = [
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        "-DBUILD_SHARED_LIBS=ON",
        "-DCMAKE_INSTALL_RPATH=$ORIGIN",
        "-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON",
        f"-DCMAKE_PREFIX_PATH={prefix}",
        "-DCMAKE_EXPORT_NO_PACKAGE_REGISTRY=ON",
        "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF",
    ]
    jobs = str(max(1, min(args.jobs, 16)))

    def cmake(name, source, flags):
        build = output / "build" / name
        run(["cmake", "-S", str(source), "-B", str(build), *common, *flags])
        run(["cmake", "--build", str(build), "--parallel", jobs])
        run(["cmake", "--install", str(build)])
        shutil.copyfile(build / "CMakeCache.txt", output / f"{name}-CMakeCache.txt")

    cmake("zlib", paths["zlib"], ["-DZLIB_BUILD_EXAMPLES=OFF"])
    cmake(
        "jpeg",
        paths["jpeg"],
        ["-DENABLE_SHARED=ON", "-DENABLE_STATIC=OFF", "-DWITH_JPEG8=ON"],
    )
    lcms_build = output / "build/lcms2"
    lcms_build.mkdir()
    run(
        [
            str(paths["lcms2"] / "configure"),
            f"--prefix={prefix}",
            "--without-jpeg",
            "--without-tiff",
            "--enable-shared",
            "--disable-static",
        ],
        lcms_build,
    )
    run(["make", "-j", jobs], lcms_build)
    run(["make", "install"], lcms_build)
    shutil.copyfile(lcms_build / "config.log", output / "lcms2-config.log")
    cmake(
        "jasper",
        paths["jasper"],
        [
            "-DJAS_ENABLE_OPENGL=OFF",
            "-DJAS_ENABLE_DOC=OFF",
            "-DJAS_ENABLE_PROGRAMS=OFF",
            "-DJAS_ENABLE_TESTS=OFF",
            "-DJAS_ENABLE_SHARED=ON",
            f"-DJPEG_INCLUDE_DIR={prefix / 'include'}",
            f"-DJPEG_LIBRARY={prefix / 'lib/libjpeg.so'}",
        ],
    )
    cmake(
        "libraw",
        paths["libraw-cmake"],
        [
            *LIBRAW_FLAGS,
            f"-DLIBRAW_PATH={paths['libraw']}",
            f"-DJPEG_INCLUDE_DIR={prefix / 'include'}",
            f"-DJPEG_LIBRARY_RELEASE={prefix / 'lib/libjpeg.so'}",
            f"-DZLIB_INCLUDE_DIR={prefix / 'include'}",
            f"-DZLIB_LIBRARY_RELEASE={prefix / 'lib/libz.so'}",
            f"-DJASPER_INCLUDE_DIR={prefix / 'include'}",
            f"-DJASPER_LIBRARY_RELEASE={prefix / 'lib/libjasper.so'}",
        ],
    )
    env.update(
        {
            "RAWPY_USE_SYSTEM_LIBRAW": "1",
            "RAWPY_BUILD_GPL_CODE": "0",
            "LDFLAGS": f"-L{prefix / 'lib'}",
        }
    )
    if run(["pkg-config", "--modversion", "libraw_r"]).strip() != "0.22.2":
        raise ValueError("Wrapper did not select pinned private LibRaw")
    run(
        [
            python,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            str(paths["rawpy"]),
            "-w",
            str(output / "raw-wheel"),
        ]
    )
    wheels = list((output / "raw-wheel").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected one built wheel")
    run(
        [
            str(output / "venv/bin/auditwheel"),
            "repair",
            "--plat",
            "manylinux_2_39_x86_64",
            str(wheels[0]),
            "-w",
            str(output / "wheels"),
        ]
    )
    wheels = list((output / "wheels").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected one repaired wheel")
    wheel = wheels[0]
    run([python, "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)])
    env.pop("LD_LIBRARY_PATH")
    probe = run(
        [
            python,
            "-c",
            "import json,rawpy,ctypes; from pathlib import Path; libs=list((Path(rawpy.__file__).parent.parent/'rawpy.libs').glob('liblcms2-*.so*')); assert len(libs)==1; lcms=ctypes.CDLL(str(libs[0])); lcms.cmsGetEncodedCMMversion.restype=ctypes.c_uint; print(json.dumps({'rawpy':rawpy.__version__,'libraw':rawpy.libraw_version,'libraw_compiled':rawpy.libraw_version_compiled,'lcms2_encoded_version':lcms.cmsGetEncodedCMMversion(),'flags':rawpy.flags},sort_keys=True))",
        ]
    )
    info = json.loads(probe.strip().splitlines()[-1])
    verify_capabilities(info)
    shared.write_json(output / "decoder-capabilities.json", info)
    runtime = Path(
        run(
            [python, "-c", "import sysconfig; print(sysconfig.get_paths()['platlib'])"]
        ).strip()
    )
    native = {}
    with zipfile.ZipFile(wheel) as package:
        for item in package.infolist():
            if not item.is_dir() and package.read(item)[:4] == b"\x7fELF":
                path = runtime / item.filename
                native[item.filename] = {
                    "sha256": shared.sha(path),
                    "dynamic": run(["readelf", "-d", str(path)]),
                }
    for required in (
        "_rawpy",
        "libraw_r",
        "libjpeg",
        "liblcms2",
        "libgomp",
    ):
        if not any(required in Path(name).name for name in native):
            raise ValueError(f"Missing required dynamically linked runtime: {required}")
    # zlib/libstdc++/libc may remain platform dependencies under auditwheel policy.
    shared.write_json(output / "native-dependencies.json", native)
    (output / "REBUILD-AND-RELINK.md").write_text(RELINK_TEXT)
    receipt = {
        "schema_version": 1,
        "kind": "raw-decoder-build",
        "complete": True,
        "wheel": {"path": str(wheel), "sha256": shared.sha(wheel)},
        "sources": sources,
        "native_files": native,
        "capabilities": info,
        "runtime_prefix": str(runtime),
        "source_prefix": str(prefix),
        "toolchain": toolchain,
        "recipe_sha256": hashlib.sha256(RECIPE_BYTES).hexdigest(),
        "helper_recipe_sha256": hashlib.sha256(HELPER_BYTES).hexdigest(),
        "log_sha256": shared.sha(log),
        "application_format_qualification": "not-performed",
        "capability_limitations": [
            "REDCINECODEC is a legacy CMake flag, not actual support: LibRaw 0.22 removed old video-camera decoders; JasPer has no runtime linkage.",
            "Actual advertised still-camera formats require application fixture qualification.",
        ],
        "licenses": {name: value[3] for name, value in SOURCES.items()},
        "distribution_status": "requires-complete-artifact-runtime-source-and-license-review",
    }
    shared.write_json(output / "build-receipt.json", receipt)
    print(json.dumps(receipt, indent=2))


RELINK_TEXT = """# RAW decoder corresponding source and relinking

This custom wheel is rawpy 0.27.1 (MIT) built against LibRaw 0.22.2; for this
distribution choose LibRaw's LGPL-2.1 alternative and retain its full
copyright and licence texts. LCMS2, JasPer, libjpeg-turbo and zlib notices are
included under notices/. LibRaw-cmake is a GPL-2.0-or-later build script; its
presence is not a blanket GPL claim for every runtime library. No GPL demosaic
packs are built. OpenMP uses GCC libgomp: its effective GPL licence plus GCC
Runtime Library Exception and corresponding distributor source/notices must be
mapped in the final artifact closure; this recipe alone does not close that duty.
JasPer is built to preserve the prior wrapper's configuration but is not linked
or shipped by this wheel: LibRaw 0.22 removed old video-camera decoders. The
REDCINECODEC flag remains legacy configuration metadata, not decoding evidence.

The exact source tarballs and hashes are in sources/ and source-inputs.json.
Both Python recipe files, hash-locked tool wheels, tool versions, CMake caches,
LCMS configure log and every compiler/link command are retained. Run
`python3.12 build_raw_decoder.py --output NEW_DIRECTORY --inputs THIS_DIRECTORY`
for an offline-input rebuild on Linux x86_64 with the recorded build tools.
Host toolchains are recorded, not container-pinned: cross-host bit identity is
not asserted. The repaired wheel requires the manylinux_2_39 platform baseline.

For LGPL modification/relinking, extract and modify the preserved LibRaw source,
rebuild its shared library using the recorded CMake flags against the private
dependency prefix, then rebuild the preserved rawpy source with
RAWPY_USE_SYSTEM_LIBRAW=1 and PKG_CONFIG_PATH/PKG_CONFIG_LIBDIR pointing to the
private prefix/lib/pkgconfig. Repair the resulting wheel with auditwheel and
install it in a private environment. Rebuild the open-source frozen helper with
that substituted wheel and rebuild the open-source application catalogue's
file hashes. Stock catalogue integrity checks reject arbitrary substitutions;
the documented rebuilt application/catalogue route permits modified helpers.
Do not impose restrictions on reverse engineering to debug LGPL modifications.
Retain backups and re-run real format/native qualification before normal use.

When distributing the wheel or frozen helper, accompany it with the applicable
full corresponding source, licence notices and these build/relink materials.
No future written offer is used. This engineering build evidence is not legal
clearance and does not cover unrelated delivered packages or patent rights.
"""


if __name__ == "__main__":
    main()

"""Explicit Linux pack builds from preserved, fully hash-locked wheel inputs.

Nothing is installed into the caller's environment. Resolution, installation,
freezing and artifact receipts live beneath the explicit output directory.
The generated catalogue is a build output; adopting it is a separate review.
"""
from __future__ import annotations

import argparse
import base64
import email
import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = {
    "ai.mobilenet-v2": [],
    "codec.heif-avif": ["Pillow", "pillow-heif"],
    "codec.camera-raw": ["Pillow", "rawpy"],
    "viewer.animation-multipage": ["Pillow"],
    "provider.onnx-nvidia": ["Pillow", "numpy", "onnxruntime-gpu[cuda,cudnn]"],
}
COLLECT = {
    "codec.heif-avif": ["PIL", "pillow_heif"],
    "codec.camera-raw": ["PIL", "rawpy", "numpy"],
    "viewer.animation-multipage": ["PIL"],
    "provider.onnx-nvidia": ["PIL", "numpy", "onnxruntime", "nvidia"],
}
MODELS = {
    "mobilenetv2.onnx": (
        ("https://huggingface.co/onnx-community/mobilenet_v2_1.0_224-ONNX/resolve/"
         "f7f884d9505b4c69f8a260d9967ff7791bafa498/onnx/model.onnx"),
        "2e731702ec8374128edfc9f7d344c44287e7791bb3c7ae25a628c2c2dec83ce6"),
    "labels.txt": (
        ("https://raw.githubusercontent.com/pytorch/hub/"
         "a6fc887fbbbda0dd37c440bf8a145f1da6707d6b/imagenet_classes.txt"),
        "1f386e0d1cb6e28b9c2dac651c3dea6801e98ad1b41a14ce6bb1a093d72069f5"),
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run(command: list[str], log: Path, *, cwd: Path | None = None) -> None:
    print(json.dumps({"stage": "command", "program": command[0], "log": str(log)}), flush=True)
    with log.open("ab") as output:
        output.write((json.dumps(command) + "\n").encode())
        subprocess.run(command, cwd=cwd or ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)


def wheel_inputs(wheels: Path, lock: Path, payload: Path) -> list[dict]:
    dependencies = []
    requirements = []
    for wheel in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA") and name.count("/") == 1]
            if len(metadata_names) != 1:
                raise RuntimeError(f"Invalid wheel metadata: {wheel.name}")
            metadata = email.message_from_bytes(archive.read(metadata_names[0]))
            name, version = metadata["Name"], metadata["Version"]
            digest = sha(wheel)
            requirements.append(f"{name}=={version} --hash=sha256:{digest}")
            licenses = []
            for member in archive.namelist():
                if member.endswith("/") or not any(token in member.lower() for token in ("license", "copying", "notice")):
                    continue
                # Names are converted to flat hashes, never extracted as paths.
                relative = f"licenses/{name}/{hashlib.sha256(member.encode()).hexdigest()[:16]}-{Path(member).name}"
                target = payload / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
                licenses.append({"original": member, "path": relative, "sha256": sha(target)})
            dependencies.append({"name": name, "version": version, "wheel": wheel.name,
                "sha256": digest, "size": wheel.stat().st_size,
                "license_expression": metadata.get("License-Expression"),
                "license_declared": metadata.get("License"), "licenses": licenses,
                "requires_dist": metadata.get_all("Requires-Dist", [])})
    if not requirements:
        raise RuntimeError("No wheel inputs resolved")
    lock.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    return dependencies


def source_receipt() -> dict:
    paths = [Path(__file__), ROOT / "src/imagesorter/component_worker.py",
             ROOT / "src/imagesorter/ai_preprocessing.py", ROOT / "src/imagesorter/apng_frames.py",
             ROOT / "src/imagesorter/reader_sandbox.py",
             ROOT / "src/imagesorter/__init__.py", ROOT / "LICENSE"]
    return {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "files": {str(path.relative_to(ROOT)): sha(path) for path in paths},
            "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))}


def make_archive(payload: Path, archive: Path) -> dict:
    files = {}
    with archive.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as package:
            for path in sorted(payload.rglob("*")):
                if path.is_symlink() or (not path.is_dir() and not path.is_file()):
                    raise RuntimeError(f"Special file in payload: {path}")
                if path.is_dir():
                    continue
                name = path.relative_to(payload).as_posix()
                mode = 0o700 if os.access(path, os.X_OK) else 0o600
                path.chmod(mode)
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode, entry.mtime = path.stat().st_size, mode, 0
                with path.open("rb") as stream:
                    package.addfile(entry, stream)
                files[name] = {"sha256": sha(path), "size": entry.size, "mode": mode}
    return files


def flatten_nvidia(payload: Path) -> None:
    """Retain exactly one copy per NVIDIA soname in the private loader path."""
    directory = payload / "_internal"
    for library in sorted((directory / "nvidia").rglob("*.so*")):
        if not library.is_file():
            continue
        primary = directory / library.name
        if primary.exists():
            if sha(primary) != sha(library):
                raise RuntimeError(f"Conflicting NVIDIA soname: {library.name}")
            library.unlink()
        else:
            library.rename(primary)


def heif_inputs(source: Path, wheels: Path, payload: Path) -> dict:
    """Accompany the separately rebuilt decoder with its exact relinking inputs."""
    receipt = json.loads((source / "build-receipt.json").read_text())
    if (receipt.get("complete") is not True or receipt.get("kind") != "heif-decoder-build" or
            set(receipt["capabilities"]["encoders"]) - {"mask"} or
            set(receipt["capabilities"]["decoders"]) != {"libde265"}):
        raise RuntimeError("HEIF decoder build is not qualified or includes an unexpected codec")
    wheel = Path(receipt["wheel"]["path"])
    if sha(wheel) != receipt["wheel"]["sha256"]:
        raise RuntimeError("HEIF decoder wheel changed after qualification")
    for path in wheels.glob("pillow_heif-*.whl"):
        path.unlink()  # This is this job's copied wheelhouse, never the preserved inputs.
    shutil.copy2(wheel, wheels / wheel.name)
    legal = payload / "corresponding-source/heif"
    legal.mkdir(parents=True)
    for item in receipt["sources"]:
        path = Path(item["path"])
        if sha(path) != item["sha256"]:
            raise RuntimeError("HEIF corresponding source changed")
    for directory in ("sources", "notices", "tool-wheels"):
        shutil.copytree(source / directory, legal / directory)
    for name in ("build-receipt.json", "source-inputs.json", "REBUILD-AND-RELINK.md",
                 "build_heif_decoder.py", "host-toolchain.json", "build.log",
                 "native-dependencies.json", "build-tools.lock"):
        shutil.copy2(source / name, legal / name)
    for name in ("libde265", "libheif"):
        shutil.copy2(source / f"{name}-CMakeCache.txt", legal / f"{name}-CMakeCache.txt")
    return {"build_receipt_sha256": sha(source / "build-receipt.json"),
            "wheel_sha256": receipt["wheel"]["sha256"], "licenses": receipt["licenses"],
            "native_files": receipt["native_files"], "corresponding_source": "corresponding-source/heif"}


def raw_inputs(source: Path, wheels: Path, payload: Path) -> dict:
    """Require the repaired native decoder and preserve its exact rebuild inputs."""
    receipt = json.loads((source / "build-receipt.json").read_text())
    capabilities = receipt.get("capabilities", {})
    lcms = next((item for item in receipt.get("sources", []) if item.get("name") == "lcms2"), {})
    if (receipt.get("complete") is not True or receipt.get("kind") != "raw-decoder-build" or
            capabilities.get("libraw") != [0, 22, 2] or capabilities.get("libraw_compiled") != [0, 22, 2] or
            lcms.get("version") != "2.19.1" or
            capabilities.get("flags", {}).get("DEMOSAIC_PACK_GPL2") is not False or
            capabilities.get("flags", {}).get("DEMOSAIC_PACK_GPL3") is not False):
        raise RuntimeError("RAW build must qualify LibRaw 0.22.2 and LCMS 2.19.1 without GPL demosaic packs")
    wheel = Path(receipt["wheel"]["path"])
    if sha(wheel) != receipt["wheel"]["sha256"]:
        raise RuntimeError("RAW decoder wheel changed after qualification")
    for item in receipt["sources"]:
        if sha(Path(item["path"])) != item["sha256"]:
            raise RuntimeError("RAW corresponding source changed")
    for name, key in (("build_raw_decoder.py", "recipe_sha256"), ("build_heif_decoder.py", "helper_recipe_sha256")):
        if sha(source / name) != receipt[key]:
            raise RuntimeError("RAW build recipe differs from its captured execution")
    for path in wheels.glob("rawpy-*.whl"):
        path.unlink()  # Only this job's copied wheelhouse, never preserved originals.
    shutil.copy2(wheel, wheels / wheel.name)
    legal = payload / "corresponding-source/raw"
    legal.mkdir(parents=True)
    for directory in ("sources", "notices", "tool-wheels"):
        shutil.copytree(source / directory, legal / directory)
    for name in ("build-receipt.json", "source-inputs.json", "REBUILD-AND-RELINK.md",
                 "build_raw_decoder.py", "build_heif_decoder.py", "host-toolchain.json", "build.log",
                 "native-dependencies.json", "build-tools.lock", "decoder-capabilities.json", "lcms2-config.log"):
        shutil.copy2(source / name, legal / name)
    for path in source.glob("*-CMakeCache.txt"):
        shutil.copy2(path, legal / path.name)
    return {"build_receipt_sha256": sha(source / "build-receipt.json"),
            "wheel_sha256": receipt["wheel"]["sha256"], "licenses": receipt["licenses"],
            "native_files": receipt["native_files"], "capabilities": capabilities,
            "capability_limitations": receipt.get("capability_limitations", []),
            "corresponding_source": "corresponding-source/raw"}


def build(component_id: str, output: Path, release_tag: str, version: str,
          inputs: Path | None = None, heif_build: Path | None = None, raw_build: Path | None = None) -> dict:
    job = output / component_id
    job.mkdir(mode=0o700)
    payload = job / "payload"
    payload.mkdir()
    sources = source_receipt()
    captured = job / "source"
    for name, digest in sources["files"].items():
        target = captured / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
        if sha(target) != digest:
            raise RuntimeError("Source changed while capturing component build inputs")
    dependencies = []
    native_build = None
    if component_id == "ai.mobilenet-v2":
        for filename, (url, digest) in MODELS.items():
            target = payload / filename
            if inputs:
                shutil.copy2(inputs / component_id / "payload" / filename, target)
            else:
                with urllib.request.urlopen(url, timeout=30) as response, target.open("xb") as stream:
                    shutil.copyfileobj(response, stream)
            if sha(target) != digest:
                raise RuntimeError(f"Pinned model checksum failed: {filename}")
            dependencies.append({"name": filename, "url": url, "sha256": digest,
                                 "size": target.stat().st_size})
    else:
        wheels = job / "wheels"
        log = job / "build.log"
        lock = job / "requirements.lock"
        if inputs:
            shutil.copytree(inputs / component_id / "wheels", wheels)
            wheel_inputs(wheels, lock, payload)
            if lock.read_bytes() != (inputs / component_id / "requirements.lock").read_bytes():
                raise RuntimeError("Preserved wheel inputs no longer match the original hash lock")
            # Rebuild notices from the final inputs below, not from a replaced wheel.
            shutil.rmtree(payload / "licenses")
        else:
            wheels.mkdir()
            run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                 "--dest", str(wheels), "PyInstaller", *SPECS[component_id]], log)
        if component_id == "codec.heif-avif":
            if heif_build is None:
                raise RuntimeError("An explicitly qualified decoder-only --heif-build is required")
            native_build = heif_inputs(heif_build, wheels, payload)
        if component_id == "codec.camera-raw":
            if raw_build is None:
                raise RuntimeError("An explicitly qualified --raw-build is required")
            native_build = raw_inputs(raw_build, wheels, payload)
        dependencies = wheel_inputs(wheels, lock, payload)
        environment = job / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = str(environment / "bin/python")
        run([python, "-m", "pip", "install", "--require-hashes", "--no-index",
             "--find-links", str(wheels), "-r", str(lock)], log)
        run([python, "-m", "pip", "check"], log)
        command = [python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                   "--name", "helper", "--distpath", str(job / "frozen"),
                   "--workpath", str(job / "freeze-work"), "--specpath", str(job),
                   "--paths", str(captured / "src"), "--exclude-module", "PyQt6"]
        for module in COLLECT[component_id]:
            command += ["--collect-all", module]
        command.append(str(captured / "src/imagesorter/component_worker.py"))
        run(command, log)
        # Dereference PyInstaller's library symlinks inside this owned build tree.
        shutil.copytree(job / "frozen/helper", payload, dirs_exist_ok=True, symlinks=False)
        if component_id == "provider.onnx-nvidia":
            # One copy of each NVIDIA library is enough. Frozen preloading uses
            # the private _internal directory, not the original wheel paths.
            for alias in (job / "frozen/helper/_internal").iterdir():
                if not alias.is_symlink() or "nvidia" not in alias.resolve().parts:
                    continue
                nested = payload / alias.resolve().relative_to(job / "frozen/helper")
                primary = payload / "_internal" / alias.name
                if nested != primary and sha(nested) == sha(primary):
                    nested.unlink()
            flatten_nvidia(payload)
        shutil.copy2(lock, payload / "requirements.lock")
        write_json(payload / "component_config.json", {"component_id": component_id,
            "version": version, "protocol_version": 1})
        with tempfile.TemporaryDirectory(prefix="probe-", dir=job) as probe_dir:
            run([str(payload / "helper"), "--probe"], log, cwd=Path(probe_dir))
    license_dir = payload / "licenses"
    license_dir.mkdir(exist_ok=True)
    shutil.copy2(captured / "LICENSE", license_dir / "ImageSorter-MIT.txt")
    python_license = Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, license_dir / "Python-LICENSE.txt")
    write_json(payload / "sbom.json", {"schema_version": 1, "component_id": component_id,
        "dependencies": dependencies, "native_build": native_build,
        "native_files": "See the catalogue's complete hashed file inventory"})
    write_json(payload / "provenance.json", {"schema_version": 1, "component_id": component_id,
        "source": sources, "python": sys.version, "platform": platform.platform(),
        "libc": platform.libc_ver(), "created_unix": int(time.time()),
        "runtime_isolation": "Self-contained PyInstaller helper; system kernel, glibc and GPU driver remain host dependencies"})
    filename = f"{component_id}-{version}-{'any' if component_id == 'ai.mobilenet-v2' else 'linux-x86_64'}.tar.gz"
    archive = output / filename
    files = make_archive(payload, archive)
    if archive.stat().st_size >= 2 * 1024 ** 3:
        raise RuntimeError("Artifact exceeds GitHub release-asset limit; packaging must be revised")
    descriptor = {"id": component_id, "version": version, "protocol_version": 1,
        "reader_policy_version": None if component_id == "ai.mobilenet-v2" else 1,
        "min_landlock_abi": (None if component_id == "ai.mobilenet-v2" else
                             6 if component_id == "provider.onnx-nvidia" else 3),
        "platform": "any" if component_id == "ai.mobilenet-v2" else "linux-x86_64",
        "abi": "data" if component_id == "ai.mobilenet-v2" else "glibc>=2.39; Linux x86_64",
        "url": f"https://github.com/SyrisBruhh42/Image_Sorter/releases/download/{release_tag}/{filename}",
        "sha256": sha(archive), "archive_size": archive.stat().st_size,
        "installed_size": sum(item["size"] for item in files.values()), "files": files,
        "entrypoint": None if component_id == "ai.mobilenet-v2" else "helper",
        "sbom": "sbom.json", "provenance": "provenance.json", "licenses": "licenses/"}
    if component_id == "ai.mobilenet-v2":
        descriptor["import_content"] = {name: base64.b64encode((payload / name).read_bytes()).decode("ascii")
            for name in files if name not in {"mobilenetv2.onnx", "labels.txt"}}
    write_json(job / "descriptor.json", descriptor)
    print(json.dumps({"stage": "complete", "id": component_id, "archive": str(archive),
                      "sha256": descriptor["sha256"], "size": descriptor["archive_size"]}), flush=True)
    return descriptor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--component", action="append", choices=list(SPECS))
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--inputs", type=Path, help="reuse and verify a previously resolved hash-locked build directory")
    parser.add_argument("--heif-build", type=Path, help="verified decoder-only build with corresponding source and notices")
    parser.add_argument("--raw-build", type=Path, help="verified LibRaw/LCMS repair with corresponding source and notices")
    parser.add_argument("--previous-catalog", type=Path, help="retain trusted previous versions for rollback")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("Only the locally qualified Linux x86_64 platform is built")
    if args.release_tag.startswith("v") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-" for c in args.release_tag + args.version):
        parser.error("Use safe version identifiers and a dedicated non-v* component release tag")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    for component_id in args.component or SPECS:
        build(component_id, output, args.release_tag, args.version, args.inputs, args.heif_build, args.raw_build)
    descriptors = [json.loads(path.read_text()) for path in sorted(output.glob("*/descriptor.json"))]
    if args.previous_catalog:
        previous = json.loads(args.previous_catalog.read_text())["components"]
        keys = {(item["id"], item["version"]): item for item in descriptors}
        for item in previous:
            key = (item["id"], item["version"])
            if key in keys and keys[key] != item:
                raise RuntimeError("A component version was reused with a different identity")
            if key not in keys:
                descriptors.append(item)
    write_json(output / "component_catalog.json", {"schema_version": 1, "components": descriptors})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

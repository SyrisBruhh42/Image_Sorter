"""Collect artifact-side runtime notices, exact inventories and source archives.

Never writes a future-source offer or infers a licence grant. A source manifest
must distinguish version-matched upstream source from verified corresponding
build source. The report is engineering evidence, not legal clearance.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import shutil
import stat
import subprocess
import tarfile
import urllib.request
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

DEFAULT_PACKAGES = (
    "PyQt6",
    "Pillow",
    "piexif",
    "onnxruntime",
    "numpy",
    "psutil",
    "Send2Trash",
)
PACK_SOURCE_PATHS = {
    "src/imagesorter/component_worker.py",
    "src/imagesorter/ai_preprocessing.py",
    "src/imagesorter/apng_frames.py",
    "src/imagesorter/__init__.py",
    "src/imagesorter/reader_sandbox.py",
    "scripts/build_component_packs.py",
    "LICENSE",
}


def requires_corresponding_source(component: dict) -> bool:
    explicit = component.get("corresponding_source_required", False)
    if type(explicit) is not bool:
        raise ValueError("corresponding_source_required must be a boolean")
    expression = str(component.get("license_expression", ""))
    return explicit or bool(re.search(r"(?:A?L?GPL)[- v]", expression, re.IGNORECASE))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def relative_file(root: Path, name: str) -> Path:
    path = root / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError(f"Unsafe inventory path: {name}")
    if not path.is_file():
        raise ValueError(f"Missing inventory file: {name}")
    return path


def resolve_packages(
    site: Path, requested: list[str]
) -> dict[str, metadata.Distribution]:
    installed = {
        canonicalize_name(d.metadata["Name"]): d
        for d in metadata.distributions(path=[str(site)])
    }
    result = {}
    pending = [Requirement(name) for name in requested]
    visited = set()
    environment = default_environment()
    environment["extra"] = ""
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        extra_key = (name, frozenset(requirement.extras))
        if extra_key in visited:
            continue
        if name not in installed:
            raise ValueError(
                f"Runtime dependency is not installed in specified environment: {name}"
            )
        dist = result[name] = installed[name]
        if dist.version not in requirement.specifier:
            raise ValueError(
                f"Installed runtime dependency does not satisfy {requirement}"
            )
        extras = {canonicalize_name(value) for value in requirement.extras}
        supported = {
            canonicalize_name(value)
            for value in dist.metadata.get_all("Provides-Extra", [])
        }
        if extras - supported:
            raise ValueError(
                f"Unknown runtime extras for {name}: {sorted(extras - supported)}"
            )
        visited.add(extra_key)
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker and not any(
                req.marker.evaluate({**environment, "extra": extra})
                for extra in {"", *extras}
            ):
                continue
            child = canonicalize_name(req.name)
            if child not in installed or installed[child].version not in req.specifier:
                raise ValueError(f"Installed runtime dependency does not satisfy {raw}")
            pending.append(req)
    return dict(sorted(result.items()))


def source_materials(manifest: dict, output: Path) -> tuple[list[dict], list[str]]:
    records, unresolved = [], []
    seen = set()
    for component in manifest.get("components", []):
        key = component["id"]
        if key in seen or not re.fullmatch(r"[A-Za-z0-9_.+-]+", key):
            raise ValueError(f"Duplicate or unsafe source component ID: {key}")
        seen.add(key)
        if not all(
            component.get(k) for k in ("version", "license_expression", "relationship")
        ):
            raise ValueError(
                f"Source component lacks version/licence/relationship: {key}"
            )
        entry = {
            k: component.get(k)
            for k in (
                "id",
                "version",
                "license_expression",
                "relationship",
                "correspondence",
                "review",
            )
        }
        entry["archives"] = []
        entry["corresponding_source_required"] = requires_corresponding_source(
            component
        )
        for source in component.get("archives", []):
            sha = source["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError("Source archive requires a complete pinned SHA-256")
            filename = source["filename"]
            if Path(filename).name != filename or filename in ("", ".", ".."):
                raise ValueError("Source filename must be a simple basename")
            target = output / "sources" / key / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.get("path"):
                origin = Path(source["path"])
                if digest(origin) != sha:
                    raise ValueError(f"Source archive hash mismatch: {origin}")
                with origin.open("rb") as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
            else:
                url = source["url"]
                if not url.startswith("https://"):
                    raise ValueError("Source download must use HTTPS")
                with (
                    urllib.request.urlopen(url, timeout=60) as src,
                    target.open("xb") as dst,
                ):
                    shutil.copyfileobj(src, dst)
            if digest(target) != sha:
                raise ValueError(f"Preserved source archive hash mismatch: {target}")
            entry["archives"].append(
                {
                    "path": str(target.relative_to(output)),
                    "sha256": sha,
                    "url": source.get("url"),
                    "size": target.stat().st_size,
                }
            )
        review = component.get("review") or {}
        if entry["corresponding_source_required"] and not entry["archives"]:
            unresolved.append(f"{key}: corresponding source archive absent")
        if entry["corresponding_source_required"] and (
            component.get("correspondence") != "verified-build-inputs"
            or not all(review.get(k) for k in ("reviewer", "reviewed_at", "basis"))
        ):
            unresolved.append(
                f"{key}: upstream version alone does not establish exact build source, patches and configuration"
            )
        records.append(entry)
    return records, unresolved


def project_snapshot(root: Path, output: Path) -> dict:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args])

    before = git("status", "--porcelain=v1", "--untracked-files=normal")
    head = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    names = sorted(
        set(
            git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
            .decode()
            .split("\0")
        )
        - {""}
    )
    archive = output / "project-source.tar.gz"
    inventory = {}
    with tarfile.open(archive, "w:gz") as tar:
        for name in names:
            path = relative_file(root, name)
            if path.is_symlink():
                raise ValueError(f"Source symlink requires explicit review: {name}")
            data = path.read_bytes()
            inventory[name] = hashlib.sha256(data).hexdigest()
            member = tarfile.TarInfo("Image_Sorter/" + name)
            member.mode = path.stat().st_mode & 0o777
            member.size = len(data)
            member.mtime = 0
            tar.addfile(member, io.BytesIO(data))
    if (
        before != git("status", "--porcelain=v1", "--untracked-files=normal")
        or head != git("rev-parse", "HEAD").decode().strip()
    ):
        raise ValueError("Project source changed during source capture")
    if any(digest(relative_file(root, name)) != sha for name, sha in inventory.items()):
        raise ValueError("Project file changed during source capture")
    write_json(output / "project-source-inventory.json", inventory)
    return {
        "source_sha": head,
        "source_tree": tree,
        "dirty": bool(before),
        "archive": {"path": archive.name, "sha256": digest(archive)},
        "files": inventory,
    }


def native_inventory(
    root: Path, package_origins: dict[str, list[dict]], manifest: dict
) -> tuple[list[dict], list[str]]:
    rows, unresolved = [], []
    mappings = manifest.get("native_mappings", {})
    sources = {row["id"] for row in manifest.get("components", [])}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"Runtime symlink escapes artifact: {path}")
            continue
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        sha = digest(path)
        relative = str(path.relative_to(root))
        dynamic = subprocess.check_output(
            ["readelf", "-d", str(path)], stderr=subprocess.STDOUT
        ).decode(errors="replace")
        mapping = mappings.get(relative)
        mapped = (
            mapping
            and mapping.get("sha256") == sha
            and mapping.get("components")
            and set(mapping["components"]).issubset(sources)
        )
        origins = package_origins.get(sha, [])
        row = {
            "path": relative,
            "sha256": sha,
            "size": path.stat().st_size,
            "matching_installed_files": origins,
            "needed": re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic),
            "dynamic": dynamic,
            "source_mapping": mapping if mapped else None,
        }
        # Exact package-byte matches establish provenance, not the native library
        # licence/source graph (a Python wheel may vendor many separate projects).
        if not mapped:
            unresolved.append(
                f"native {relative}: exact-digest component/source mapping absent"
            )
        rows.append(row)
    return rows, unresolved


def component_identity(
    runtime: Path,
    descriptor: dict,
    files: list[dict],
    project: dict | None,
    delivery: Path | None,
) -> tuple[dict, list[str]]:
    """Bind a pack to its exact build P; final-catalogue adoption is a later gate."""
    expected = descriptor.get("files")
    if (
        not isinstance(descriptor.get("id"), str)
        or not descriptor["id"]
        or not isinstance(descriptor.get("version"), str)
        or not descriptor["version"]
        or type(descriptor.get("protocol_version")) is not int
        or descriptor["protocol_version"] != 1
        or not re.fullmatch(r"[0-9a-f]{64}", str(descriptor.get("sha256", "")))
        or not isinstance(expected, dict)
        or not expected
    ):
        raise ValueError("Component descriptor identity or file inventory is invalid")
    observed = {row["path"]: row for row in files}
    if set(observed) != set(expected):
        raise ValueError("Component runtime differs from descriptor file inventory")
    for name, row in expected.items():
        path = relative_file(runtime, name)
        info = path.lstat()
        if (
            Path(name).as_posix() != name
            or "\\" in name
            or not isinstance(row, dict)
            or not stat.S_ISREG(info.st_mode)
            or "symlink" in observed[name]
            or observed[name].get("sha256") != row.get("sha256")
            or type(row.get("size")) is not int
            or info.st_size != row["size"]
            or type(row.get("mode")) is not int
            or stat.S_IMODE(info.st_mode) != row["mode"]
        ):
            raise ValueError(f"Component runtime bytes, size or mode differ: {name}")
    unresolved = []
    if delivery is None:
        unresolved.append(
            "component delivery archive absent; descriptor alone is not delivery evidence"
        )
    elif digest(delivery) != descriptor[
        "sha256"
    ] or delivery.stat().st_size != descriptor.get("archive_size"):
        raise ValueError("Component delivery archive differs from descriptor")
    name = descriptor.get("provenance", "provenance.json")
    if name not in expected:
        raise ValueError("Component provenance is absent from descriptor inventory")
    provenance = json.loads(relative_file(runtime, name).read_text())
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema_version") != 1
        or provenance.get("component_id") != descriptor["id"]
    ):
        raise ValueError("Component provenance identity does not match descriptor")
    source = provenance.get("source")
    if not isinstance(source, dict):
        raise ValueError("Component provenance source must be an object")
    captured = source.get("files")
    if (
        source.get("dirty") is not False
        or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("head", "")))
        or not project
        or project.get("dirty") is not False
        or source.get("head") != project.get("source_sha")
        or not isinstance(captured, dict)
        or not PACK_SOURCE_PATHS.issubset(captured)
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(value))
            or project.get("files", {}).get(key) != value
            for key, value in captured.items()
        )
    ):
        unresolved.append(
            "component provenance does not match its exact clean captured build-source project"
        )
    return {
        "descriptor": descriptor,
        "provenance": provenance,
        "provenance_path": name,
        "provenance_sha256": observed[name]["sha256"],
    }, unresolved


def asset_inventory(
    runtime: Path | None, manifest: dict, descriptor: dict | None
) -> tuple[list[dict], list[str]]:
    """Map non-code model assets to distinct obligations without inferring grants."""
    mappings = manifest.get("asset_mappings", [])
    if not isinstance(mappings, list):
        raise ValueError("asset_mappings must be a list")
    source_ids = {row["id"] for row in manifest.get("components", [])}
    records, seen = [], set()
    for row in mappings:
        if not isinstance(row, dict) or not runtime:
            raise ValueError("Asset mapping requires a runtime and structured entry")
        name = row.get("path")
        if (
            not isinstance(name, str)
            or name in seen
            or Path(name).as_posix() != name
            or "\\" in name
            or row.get("source_id") not in source_ids
            or not isinstance(row.get("role"), str)
            or not row["role"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))
        ):
            raise ValueError(
                "Asset mapping path, hash, role or source identity is invalid"
            )
        path = relative_file(runtime, name)
        if path.is_symlink() or digest(path) != row["sha256"]:
            raise ValueError(f"Asset mapping bytes differ: {name}")
        if (
            descriptor
            and descriptor.get("files", {}).get(name, {}).get("sha256") != row["sha256"]
        ):
            raise ValueError(f"Asset mapping differs from descriptor: {name}")
        seen.add(name)
        records.append(
            {key: row[key] for key in ("path", "sha256", "role", "source_id")}
        )
    unresolved = []
    if descriptor and descriptor.get("id") == "ai.mobilenet-v2":
        required = {"mobilenetv2.onnx": "model-weights", "labels.txt": "model-labels"}
        if (
            len(records) != 2
            or seen != set(required)
            or len({row["source_id"] for row in records}) != 2
            or any(row["role"] != required.get(row["path"]) for row in records)
        ):
            unresolved.append(
                "model weights and labels require distinct exact-asset source/grant mappings"
            )
    return records, unresolved


def preserve_host_origins(
    native: list[dict], toc_path: Path, output: Path
) -> list[dict]:
    """Correlate captured build inputs, not guessed host-only dependencies."""
    entries = {}

    def visit(value: object) -> None:
        if isinstance(value, (list, tuple)):
            if (
                len(value) == 3
                and all(isinstance(part, str) for part in value)
                and value[2] in ("BINARY", "EXTENSION")
            ):
                entries[value[0]] = value[1]
            else:
                for child in value:
                    visit(child)

    visit(ast.literal_eval(toc_path.read_text()))
    packages = {}
    for row in native:
        destination = row["path"].removeprefix("_internal/")
        source = Path(entries[destination]) if destination in entries else None
        if source is None or not source.is_file() or digest(source) != row["sha256"]:
            continue
        row["captured_build_input"] = {"path": str(source), "sha256": row["sha256"]}
        if row["matching_installed_files"]:
            continue
        owner = None
        for candidate in dict.fromkeys((str(source), str(source.resolve()))):
            query = subprocess.run(
                ["dpkg-query", "-S", candidate], text=True, capture_output=True
            )
            if query.returncode == 0:
                for line in query.stdout.splitlines():
                    matched = re.fullmatch(
                        r"([a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?): (.+)", line
                    )
                    if matched and matched.group(2) == candidate:
                        owner = matched.group(1)
                        break
                if owner:
                    break
        if not owner:
            continue
        if owner not in packages:
            query = subprocess.run(
                [
                    "dpkg-query",
                    "-W",
                    "-f=${binary:Package}\\t${Version}\\t${source:Package}\\t${source:Version}",
                    owner,
                ],
                text=True,
                capture_output=True,
            )
            if query.returncode:
                continue
            fields = query.stdout.split("\t")
            if len(fields) != 4:
                continue
            record = {
                "package": fields[0],
                "version": fields[1],
                "source_package": fields[2],
                "source_version": fields[3],
                "notice": None,
                "evidence_kind": "installed-package-metadata-not-upstream-build-attestation",
            }
            copyright_path = (
                Path("/usr/share/doc") / owner.split(":", 1)[0] / "copyright"
            )
            if copyright_path.is_file():
                target = (
                    output
                    / "licenses"
                    / "host-packages"
                    / (owner.replace(":", "_") + "-copyright")
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(copyright_path, target)
                record["notice"] = {
                    "path": str(target.relative_to(output)),
                    "sha256": digest(target),
                }
                record["referenced_license_texts"] = []
                for name in sorted(
                    set(
                        re.findall(
                            r"/usr/share/common-licenses/([A-Za-z0-9_.+-]+)",
                            copyright_path.read_text(errors="replace"),
                        )
                    )
                ):
                    common = Path("/usr/share/common-licenses") / name
                    if common.is_file():
                        destination = output / "licenses" / "common" / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(common, destination)
                        record["referenced_license_texts"].append(
                            {
                                "path": str(destination.relative_to(output)),
                                "sha256": digest(destination),
                            }
                        )
            packages[owner] = record
        row["installed_os_package"] = owner
    return list(packages.values())


def collect(
    site: Path,
    runtime: Path | None,
    output: Path,
    packages: list[str],
    source_map: dict,
    project: Path | None,
    analysis_toc: Path | None = None,
    delivery: Path | None = None,
    component_descriptor: dict | None = None,
) -> dict:
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    distributions = resolve_packages(site, packages)
    package_rows, origins, unresolved = [], {}, []
    source_ids = {row["id"]: row for row in source_map.get("components", [])}
    for name, dist in distributions.items():
        texts, sboms, native = [], [], []
        for member in dist.files or []:
            path = Path(dist.locate_file(member))
            if not path.is_file() or not path.resolve().is_relative_to(site):
                continue
            relative = str(member)
            upper = path.name.upper()
            is_notice = any(word in upper for word in ("LICENSE", "COPYING", "NOTICE"))
            is_sbom = "sbom" in relative.lower() or path.name.endswith(".cdx.json")
            if is_notice or is_sbom:
                sha = digest(path)
                target = (
                    output
                    / ("sboms" if is_sbom else "licenses")
                    / name
                    / (sha[:16] + "-" + path.name)
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copyfile(path, target)
                (sboms if is_sbom else texts).append(
                    {
                        "original": relative,
                        "path": str(target.relative_to(output)),
                        "sha256": sha,
                    }
                )
            with path.open("rb") as stream:
                elf = stream.read(4) == b"\x7fELF"
            if elf:
                sha = digest(path)
                origins.setdefault(sha, []).append(
                    {"package": name, "version": dist.version, "file": relative}
                )
                native.append({"file": relative, "sha256": sha})
        for alias in source_map.get("notice_aliases", {}).get(name, []):
            owner = distributions.get(canonicalize_name(alias["package"]))
            if owner is None:
                raise ValueError(
                    f"Notice alias references an absent runtime package: {name}"
                )
            original = relative_file(site, alias["original"])
            if (
                digest(original) != alias["sha256"]
                or not alias.get("section")
                or alias["section"] not in original.read_text(errors="replace")
            ):
                raise ValueError(f"Notice alias bytes or section do not match: {name}")
            target = (
                output
                / "licenses"
                / name
                / (alias["sha256"][:16] + "-" + original.name)
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            texts.append(
                {
                    "original": alias["original"],
                    "path": str(target.relative_to(output)),
                    "sha256": alias["sha256"],
                    "aggregate_section": alias["section"],
                    "owner": alias["package"],
                }
            )
        if not texts:
            unresolved.append(
                f"{name}: no package-local license/notice file; identify any covering aggregate notice explicitly"
            )
        license_expression = dist.metadata.get(
            "License-Expression"
        ) or dist.metadata.get("License")
        if name not in source_ids and requires_corresponding_source(
            {"license_expression": license_expression}
        ):
            unresolved.append(f"{name}: corresponding source component absent")
        elif name in source_ids and source_ids[name].get("version") != dist.version:
            unresolved.append(
                f"{name}: corresponding source version differs from installed runtime"
            )
        package_rows.append(
            {
                "id": name,
                "version": dist.version,
                "role": "runtime",
                "license_expression": dist.metadata.get("License-Expression")
                or dist.metadata.get("License"),
                "requires_dist": dist.requires or [],
                "notices": texts,
                "upstream_sboms": sboms,
                "installed_native_files": native,
            }
        )
    source_rows, source_unresolved = source_materials(source_map, output)
    unresolved.extend(source_unresolved)
    project_record = project_snapshot(project, output) if project else None
    if project_record is None or project_record["dirty"]:
        unresolved.append(
            "project source is missing or dirty; regenerate from the exact final qualified commit"
        )
    native_rows = []
    host_packages = []
    runtime_files = []
    build_identity = None
    pack_identity = None
    if component_descriptor is not None and runtime is None:
        raise ValueError("Component descriptor mode requires its runtime root")
    if runtime:
        native_rows, native_unresolved = native_inventory(runtime, origins, source_map)
        unresolved.extend(native_unresolved)
        if analysis_toc:
            host_packages = preserve_host_origins(native_rows, analysis_toc, output)
        for path in sorted(runtime.rglob("*")):
            if path.is_symlink():
                if not path.resolve().is_relative_to(runtime):
                    raise ValueError(f"Runtime symlink escapes artifact: {path}")
                runtime_files.append(
                    {
                        "path": str(path.relative_to(runtime)),
                        "symlink": str(path.readlink()),
                    }
                )
            elif path.is_file():
                runtime_files.append(
                    {"path": str(path.relative_to(runtime)), "sha256": digest(path)}
                )
        if component_descriptor is not None:
            pack_identity, pack_unresolved = component_identity(
                runtime, component_descriptor, runtime_files, project_record, delivery
            )
            unresolved.extend(pack_unresolved)
        else:
            identities = list(runtime.rglob("build_identity.json"))
            if len(identities) == 1:
                build_identity = json.loads(identities[0].read_text())
            if (
                not build_identity
                or not project_record
                or build_identity.get("dirty") is not False
                or any(
                    build_identity.get(k) != project_record[k]
                    for k in ("source_sha", "source_tree")
                )
            ):
                unresolved.append(
                    "runtime embedded build identity does not match the clean captured project source"
                )
    else:
        unresolved.append(
            "no final frozen runtime supplied; installed package inventory is not shipped closure"
        )
    assets, asset_unresolved = asset_inventory(
        runtime, source_map, component_descriptor
    )
    unresolved.extend(asset_unresolved)
    build_tools = []
    for dist in metadata.distributions(path=[str(site)]):
        name = canonicalize_name(dist.metadata["Name"])
        if name not in distributions:
            build_tools.append(
                {
                    "id": name,
                    "version": dist.version,
                    "role": "environment-only-not-asserted-shipped",
                }
            )
    receipt = {
        "schema_version": 1,
        "kind": "runtime-license-source-inventory",
        "runtime_root": str(runtime) if runtime else None,
        "site_packages": str(site),
        "packages": package_rows,
        "sources": source_rows,
        "native_files": native_rows,
        "shipped_os_packages": host_packages,
        "runtime_files": runtime_files,
        "build_identity": build_identity,
        "component_identity": pack_identity,
        "asset_mappings": assets,
        "project": project_record,
        "environment_only": sorted(build_tools, key=lambda r: r["id"]),
        "unresolved": sorted(set(unresolved)),
        "engineering_complete": not unresolved,
        "legal_clearance": "not-asserted",
        # Hash identifies the delivery selected by the collector, but is not
        # proof that extraction matches: publication_guard checks its members.
        "delivery_sha256": digest(delivery) if delivery else None,
    }
    write_json(output / "runtime-license-inventory.json", receipt)
    shutil.copyfile(__file__, output / "collect_runtime_licenses.py")
    notice = """Image Sorter combined binary distribution\n\nThe application's original source remains under its accompanying MIT license.\nThis binary includes GPL-3.0-only PyQt6; the combined distribution must satisfy\nGPL version 3. This notice does not relicense third-party works as MIT or erase\ntheir separate copyright notices. Applicable texts are under licenses/.\n\nExact source archives, modifications, configuration and rebuilding materials\nare supplied alongside the corresponding binary, not by a future written offer.\nDo not publish an incomplete source inventory. An upstream version match alone\nis not proof that a binary was produced from unmodified source.\n\nLGPL components may be modified and rebuilt. The stock component catalog\nrejects modified binaries as a security control. To use a modified LGPL helper,\nrebuild that helper from the provided source with the substituted library or\nwheel, then rebuild the open-source application with a catalog describing the\nmodified helper's exact files and hashes. Retain original source backups. No\nlicense term here prohibits reverse engineering to debug such modifications.\n\nOptional NVIDIA runtime components retain their proprietary license terms and\nmust be assessed separately; this notice supplies no new NVIDIA rights or\nassumption that process isolation resolves license compatibility. Model weights\nand labels require their own affirmative redistribution grants.\n"""
    if "pyqt6" not in distributions:
        notice = notice.replace(
            "combined binary distribution", "optional helper distribution"
        ).replace(
            "This binary includes GPL-3.0-only PyQt6; the combined distribution must satisfy\nGPL version 3. ",
            "This helper's individual runtime components retain their applicable terms. ",
        )
    notice = notice.replace(
        "are supplied alongside the corresponding binary, not by a future written offer.",
        "must accompany the corresponding binary where required, not by a future written offer.",
    )
    (output / "DISTRIBUTION-NOTICE.txt").write_text(notice)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-package", action="append")
    parser.add_argument("--source-map", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--analysis-toc", type=Path)
    parser.add_argument(
        "--delivery",
        type=Path,
        help="Exact wheel, onedir archive or AppImage to bind; publication independently checks its payload",
    )
    parser.add_argument(
        "--component-descriptor",
        type=Path,
        help="Exact pack descriptor JSON; use its clean build-source checkout, not a later catalogue commit",
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    manifest = (
        json.loads(args.source_map.read_text())
        if args.source_map
        else {"schema_version": 1, "components": []}
    )
    if manifest.get("schema_version") != 1:
        raise SystemExit("Unsupported source map schema")
    output = args.output.absolute()
    if args.project_root and output.is_relative_to(args.project_root.resolve()):
        raise SystemExit("Evidence output must be outside the project source snapshot")
    if args.runtime_root and output.is_relative_to(args.runtime_root.resolve()):
        raise SystemExit(
            "Evidence output must be outside the runtime being inventoried"
        )
    descriptor = (
        json.loads(args.component_descriptor.read_text())
        if args.component_descriptor
        else None
    )
    if (
        descriptor is not None
        and descriptor.get("id") != "ai.mobilenet-v2"
        and not args.runtime_package
    ):
        raise SystemExit(
            "Component helpers require explicit --runtime-package roots, including enabled extras"
        )
    packages = args.runtime_package or (
        [] if descriptor is not None else list(DEFAULT_PACKAGES)
    )
    result = collect(
        args.site_packages.resolve(),
        args.runtime_root.resolve() if args.runtime_root else None,
        output,
        packages,
        manifest,
        args.project_root.resolve() if args.project_root else None,
        args.analysis_toc.resolve() if args.analysis_toc else None,
        args.delivery.resolve() if args.delivery else None,
        descriptor,
    )
    print(
        json.dumps(
            {
                "inventory": str(output / "runtime-license-inventory.json"),
                "engineering_complete": result["engineering_complete"],
                "unresolved": result["unresolved"],
            },
            indent=2,
        )
    )
    if args.require_complete and not result["engineering_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

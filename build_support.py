"""Embed verifiable checkout identity without editing tracked package sources."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


def verify_frozen_modules(root: Path, identity: dict, modules) -> None:
    """Reject ambient/editable application imports in an otherwise attested build."""
    root = root.resolve()
    for name, source, _kind in modules:
        if name == "imagesorter" or name.startswith("imagesorter."):
            path = Path(source).resolve()
            if not path.is_relative_to(root / "src"):
                raise ValueError(f"Application module came from another checkout: {name}: {source}")
            relative = path.relative_to(root).as_posix()
            if hashlib.sha256(path.read_bytes()).hexdigest() != identity["source_files"].get(relative):
                raise ValueError(f"Application module differs from source attestation: {name}")


def source_identity(root: Path) -> dict:
    try:
        checkout = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root,
                                               text=True, stderr=subprocess.DEVNULL).strip())
        if checkout.resolve() != root.resolve():
            raise subprocess.CalledProcessError(1, "source export is not the checkout root")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root))
    except subprocess.CalledProcessError:
        exported = json.loads((root / "_source_identity.json").read_text())
        for name, expected in exported["source_files"].items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Exported source identity failed: {name}")
        return exported
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).split(b"\0")
    files = {}
    for encoded in sorted(set(names)):
        if not encoded:
            continue
        name = encoded.decode("utf-8")
        path = root / name
        if path.is_symlink():
            raise ValueError(f"Source attestation refuses a symbolic link: {name}")
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"schema_version": 1, "source_sha": commit, "source_tree": tree,
            "dirty": dirty, "source_files": files}


class AttestedBuildPy(build_py):
    def run(self):
        root = Path(__file__).resolve().parent
        identity = source_identity(root)
        super().run()
        if source_identity(root) != identity:
            raise ValueError("Source changed during wheel build")
        target = Path(self.build_lib) / "imagesorter/resources/build_identity.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(identity, sort_keys=True, indent=2) + "\n")


class AttestedSdist(sdist):
    def make_release_tree(self, base_dir, files):
        root = Path(__file__).resolve().parent
        identity = source_identity(root)
        super().make_release_tree(base_dir, sorted(set(files) | set(identity["source_files"])))
        for name, expected in identity["source_files"].items():
            if hashlib.sha256((Path(base_dir) / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Source changed while exporting: {name}")
        if source_identity(root) != identity:
            raise ValueError("Source changed during source distribution build")
        (Path(base_dir) / "_source_identity.json").write_text(json.dumps(identity, sort_keys=True, indent=2) + "\n")

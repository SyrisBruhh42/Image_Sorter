"""Verified, explicit-only component installation with durable version activation.

The shipped catalogue is the trust root. No catalogue or package is fetched while
constructing a manager, listing components, or starting the application.
"""
from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

from .paths import get_components_dir

CATALOG_VERSION = 1
HELPER_VERSION = 1
KNOWN_IDS = frozenset({"ai.mobilenet-v2", "provider.onnx-nvidia", "codec.heif-avif",
                       "codec.camera-raw", "viewer.animation-multipage"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ComponentError(RuntimeError):
    """An install, compatibility, or integrity condition failed closed."""


class ComponentBusy(ComponentError):
    """A version is currently leased by a running helper."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sync_dir(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".registry-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_relative(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ComponentError("Invalid component member path")
    path = Path(value)
    if path.is_absolute() or any(part in {"..", "."} for part in value.split("/")):
        raise ComponentError(f"Unsafe component member: {value}")
    if any(not part or any(ord(c) < 32 for c in part) for part in value.split("/")):
        raise ComponentError("Invalid component member name")
    return path


def clean_helper_environment() -> dict[str, str]:
    result = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH",
                 "LD_PRELOAD", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
                 "_PYI_PARENT_PROCESS_LEVEL", "_PYI_SPLASH_IPC"):
        result.pop(name, None)
    original = result.pop("LD_LIBRARY_PATH_ORIG", None)
    result.pop("LD_LIBRARY_PATH", None)
    if original:
        result["LD_LIBRARY_PATH"] = original
    result.update(ONNXRUNTIME_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                  HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                  PYINSTALLER_RESET_ENVIRONMENT="1")
    return result


class ComponentManager:
    def __init__(self, root: Path | None = None, catalog_path: Path | None = None):
        requested = Path(root) if root is not None else get_components_dir()
        if not requested.is_absolute():
            raise ComponentError("Component store must be an absolute path")
        for parent in (requested, *requested.parents):
            if parent.is_symlink():
                raise ComponentError("Component store may not use symbolic links")
        requested.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = requested.resolve()
        info = self.root.stat()
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ComponentError("Component store belongs to another user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            if root is not None:
                raise ComponentError("Component store must be private (0700)")
            self.root.chmod(0o700)
        self.catalog_path = catalog_path or Path(__file__).parent / "resources" / "component_catalog.json"
        raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != CATALOG_VERSION:
            raise ComponentError("Unsupported component catalogue version")
        self.catalog: dict[str, dict] = {}
        self.trusted_versions: dict[tuple[str, str], dict] = {}
        for item in raw.get("components", []):
            self._validate_descriptor(item)
            key = (item["id"], item["version"])
            if key in self.trusted_versions:
                raise ComponentError("Duplicate catalogue version")
            self.trusted_versions[key] = item
            self.catalog.setdefault(item["id"], item)
        self.registry_path = self.root / "registry.json"
        self.jobs_dir = self.root / "jobs"
        if self.jobs_dir.is_symlink():
            raise ComponentError("Component jobs directory is a symbolic link")
        self.jobs_dir.mkdir(mode=0o700, exist_ok=True)

    @staticmethod
    def _validate_descriptor(item: dict) -> None:
        if item.get("id") not in KNOWN_IDS or not _IDENTIFIER.fullmatch(item.get("version", "")):
            raise ComponentError("Unknown component or unsafe version")
        if type(item.get("protocol_version")) is not int or item["protocol_version"] != HELPER_VERSION:
            raise ComponentError("Unsupported helper protocol")
        if item.get("reader_policy_version") is not None:
            minimum = 6 if item["id"] == "provider.onnx-nvidia" else 3
            if (type(item["reader_policy_version"]) is not int or item["reader_policy_version"] != 1 or
                    type(item.get("min_landlock_abi")) is not int or item["min_landlock_abi"] != minimum or
                    item["id"] == "ai.mobilenet-v2"):
                raise ComponentError("Unsupported reader containment contract")
        elif item.get("min_landlock_abi") is not None:
            raise ComponentError("Reader ABI requires a versioned policy")
        if not _DIGEST.fullmatch(item.get("sha256", "")):
            raise ComponentError("Missing archive digest")
        for field in ("archive_size", "installed_size"):
            if type(item.get(field)) is not int or item[field] <= 0:
                raise ComponentError(f"Invalid component {field}")
        inventory = item.get("files")
        if not isinstance(inventory, dict) or not inventory:
            raise ComponentError("Missing complete file inventory")
        total = 0
        for name, entry in inventory.items():
            _safe_relative(name)
            if name == ".installed.json" or ".installed.json" in Path(name).parts:
                raise ComponentError("Archive may not supply installation metadata")
            if any(parent.as_posix() in inventory for parent in Path(name).parents if parent != Path(".")):
                raise ComponentError("Archive file conflicts with a parent directory")
            if not _DIGEST.fullmatch(entry.get("sha256", "")):
                raise ComponentError("Invalid member digest")
            if type(entry.get("size")) is not int or entry["size"] < 0:
                raise ComponentError("Invalid member size")
            if entry.get("mode") not in (0o600, 0o700):
                raise ComponentError("Invalid member permissions")
            total += entry["size"]
        if total != item["installed_size"]:
            raise ComponentError("Installed size disagrees with inventory")
        if item.get("entrypoint") is not None:
            entry = inventory.get(item["entrypoint"])
            if not entry or entry["mode"] != 0o700:
                raise ComponentError("Entrypoint is absent or not executable")
        if item.get("url") and not item["url"].startswith("https://"):
            raise ComponentError("Component downloads require HTTPS")

    @contextlib.contextmanager
    def _lock(self, path: Path | None = None, *, shared: bool = False,
              blocking: bool = True) -> Iterator[None]:
        target = path or self.root / ".store.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        try:
            if os.name == "posix":
                import fcntl
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                try:
                    fcntl.flock(descriptor, mode | (0 if blocking else fcntl.LOCK_NB))
                except BlockingIOError as exc:
                    raise ComponentBusy("Component is in use") from exc
            elif os.name == "nt":
                import msvcrt
                os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                while True:
                    try:
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if not blocking:
                            raise ComponentBusy("Component is in use") from exc
                        time.sleep(.05)
            else:
                raise ComponentError("No supported OS component lock on this platform")
            yield
        finally:
            os.close(descriptor)

    def _registry(self) -> dict:
        if not self.registry_path.exists():
            return {"schema_version": 1, "components": {}}
        if self.registry_path.is_symlink():
            raise ComponentError("Registry is a symbolic link")
        value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1 or not isinstance(value.get("components"), dict):
            raise ComponentError("Invalid component registry; preserve for recovery")
        return value

    def _version_path(self, component_id: str, version: str) -> Path:
        if component_id not in KNOWN_IDS or not _IDENTIFIER.fullmatch(version):
            raise ComponentError("Unsafe component identity")
        target = self.root / component_id / version
        if target.is_symlink() or target.parent.is_symlink():
            raise ComponentError("Component version is a symbolic link")
        return target

    def active_path(self, component_id: str) -> Path | None:
        entry = self._registry()["components"].get(component_id, {})
        version = entry.get("active")
        if not entry.get("enabled", False) or not version:
            return None
        path = self._version_path(component_id, version)
        return path if path.is_dir() else None

    def status(self) -> list[dict]:
        registry = self._registry()["components"]
        pending = self.pending_jobs()
        result = []
        for component_id in sorted(KNOWN_IDS):
            descriptor = self.catalog.get(component_id)
            entry = registry.get(component_id, {})
            result.append({"id": component_id, "available": descriptor is not None,
                           "version": descriptor.get("version") if descriptor else None,
                           "active": entry.get("active"), "previous": entry.get("previous"),
                           "enabled": entry.get("enabled", False),
                           "archive_size": descriptor.get("archive_size", 0) if descriptor else 0,
                           "installed_size": descriptor.get("installed_size", 0) if descriptor else 0,
                           "pending_jobs": [job for job in pending if job.get("component_id") == component_id],
                           "compatibility_detail": self._compatibility_detail(descriptor) if descriptor else "Not in catalog",
                           "compatible": self._compatible(descriptor) if descriptor else False})
        return result

    def pending_jobs(self) -> list[dict]:
        records = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            if path.is_symlink():
                raise ComponentError("Installation receipt is a symbolic link")
            job = json.loads(path.read_text(encoding="utf-8"))
            if job.get("state") not in {"completed", "failed", "cancelled"}:
                records.append(job)
        return records

    @staticmethod
    def _compatibility_detail(descriptor: dict) -> str:
        if descriptor.get("entrypoint") and not descriptor.get("reader_policy_version"):
            return "This historical helper lacks reader containment; install a qualified updated pack."
        if descriptor.get("reader_policy_version"):
            from .reader_sandbox import landlock_abi
            minimum = descriptor["min_landlock_abi"]
            if landlock_abi() < minimum:
                return f"Requires Linux Landlock ABI {minimum}+; no administrator fallback. Base CPU needs ABI 3+."
        return "Compatible" if ComponentManager._compatible(descriptor) else "Unsupported platform or runtime ABI"

    @staticmethod
    def _compatible(descriptor: dict) -> bool:
        if descriptor.get("entrypoint") and not descriptor.get("reader_policy_version"):
            return False
        if descriptor.get("reader_policy_version"):
            from .reader_sandbox import landlock_abi
            if landlock_abi() < descriptor["min_landlock_abi"]:
                return False
        target = descriptor.get("platform", "any")
        if target == "any":
            return True
        if not (target == "linux-x86_64" and platform.system() == "Linux" and
                platform.machine().lower() in {"x86_64", "amd64"}):
            return False
        abi = descriptor.get("abi", "")
        if abi.startswith("glibc>="):
            minimum = tuple(int(n) for n in abi.split(";", 1)[0][7:].split("."))
            family, version = platform.libc_ver()
            try:
                return family == "glibc" and tuple(int(n) for n in version.split(".")) >= minimum
            except ValueError:
                return False
        return not abi

    def _verify_tree(self, path: Path, descriptor: dict) -> None:
        observed = set()
        for candidate in path.rglob("*"):
            if candidate.is_symlink():
                raise ComponentError("Installed component contains a symbolic link")
            if candidate.is_dir():
                continue
            relative = candidate.relative_to(path).as_posix()
            if relative == ".installed.json":
                continue
            if not candidate.is_file() or relative not in descriptor["files"]:
                raise ComponentError(f"Unexpected component file: {relative}")
            entry = descriptor["files"][relative]
            if candidate.stat().st_size != entry["size"] or sha256_file(candidate) != entry["sha256"]:
                raise ComponentError(f"Component integrity failed: {relative}")
            if stat.S_IMODE(candidate.stat().st_mode) != entry["mode"]:
                raise ComponentError(f"Component permissions changed: {relative}")
            observed.add(relative)
        if observed != set(descriptor["files"]):
            raise ComponentError("Component files are missing")

    def _installed_descriptor(self, path: Path) -> dict:
        descriptor_path = path / ".installed.json"
        if descriptor_path.is_symlink():
            raise ComponentError("Installed manifest is a symbolic link")
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        self._validate_descriptor(descriptor)
        trusted = self.trusted_versions.get((descriptor["id"], descriptor["version"]))
        if trusted != descriptor or path != self._version_path(descriptor["id"], descriptor["version"]):
            raise ComponentError("Installed manifest is not authorized by the shipped catalogue")
        return descriptor

    def _probe(self, path: Path, descriptor: dict) -> None:
        entrypoint = descriptor.get("entrypoint")
        if not entrypoint:
            return
        if not self._compatible(descriptor):
            raise ComponentError(self._compatibility_detail(descriptor))
        with tempfile.TemporaryDirectory(prefix=".probe-", dir=self.root) as work, \
                tempfile.TemporaryFile(dir=work) as output, tempfile.TemporaryFile(dir=work) as error:
            process = subprocess.Popen([str(path / entrypoint), "--probe"], stdout=output,
                                       stderr=error, cwd=work, env=clean_helper_environment())
            try:
                deadline = time.monotonic() + 30
                while True:
                    if os.fstat(output.fileno()).st_size > 65536 or os.fstat(error.fileno()).st_size > 1048576:
                        raise ComponentError("Component probe exceeded its bounded output channel")
                    if process.poll() is not None:
                        break
                    if time.monotonic() >= deadline:
                        raise ComponentError("Component probe exceeded its 30-second deadline")
                    time.sleep(.025)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
            output.seek(0)
            response_bytes = output.read(65537)
            error.seek(0)
            error_bytes = error.read(1500)
        if process.returncode:
            raise ComponentError(f"Component probe failed: {error_bytes.decode(errors='replace')}")
        try:
            response = json.loads(response_bytes)
        except ValueError as exc:
            raise ComponentError("Invalid helper probe response") from exc
        if (not isinstance(response, dict) or type(response.get("protocol_version")) is not int or
                response.get("protocol_version") != HELPER_VERSION or response.get("ready") is not True or
                response.get("component_id") != descriptor["id"] or
                response.get("component_version") != descriptor["version"]):
            raise ComponentError("Helper is not compatible or ready")
        if descriptor.get("reader_policy_version"):
            from .reader_sandbox import ReaderIsolationError, validate_reader_isolation
            try:
                validate_reader_isolation(response, policy_version=descriptor["reader_policy_version"],
                                          minimum_abi=descriptor["min_landlock_abi"])
            except ReaderIsolationError as exc:
                raise ComponentError(str(exc)) from exc

    @staticmethod
    def _stage_local_archive(source: Path, target: Path, descriptor: dict, check_cancel) -> None:
        """Verify one opened regular input into private staging, never reopen it for extraction."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(source, flags), "rb") as content:
            before = os.fstat(content.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != descriptor["archive_size"]:
                raise ComponentError("Local archive must be a regular file with the pinned size")
            digest, count = hashlib.sha256(), 0
            with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                for block in iter(lambda: content.read(1024 * 1024), b""):
                    check_cancel()
                    count += len(block)
                    if count > descriptor["archive_size"]:
                        raise ComponentError("Local archive grew beyond its pinned size")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(content.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if (identity(before) != identity(after) or count != descriptor["archive_size"] or
                digest.hexdigest() != descriptor["sha256"]):
            raise ComponentError("Local archive changed or failed its pinned checksum")

    def install(self, component_id: str, archive: Path | None = None, *,
                progress: Callable[[dict], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> Path:
        descriptor = self.catalog.get(component_id)
        if not descriptor or not self._compatible(descriptor):
            raise ComponentError("No qualified component artifact for this platform")
        job = {"id": uuid.uuid4().hex, "component_id": component_id,
               "version": descriptor["version"], "state": "queued", "created": time.time()}
        job_path = self.jobs_dir / (job["id"] + ".json")
        def transition(state: str, **extra):
            updated = dict(job, state=state, updated=time.time(), **extra)
            _write_json(job_path, updated)
            job.update(updated)
            if progress:
                progress(dict(job))
        def check_cancel():
            if cancelled and cancelled():
                raise InterruptedError("Component installation cancelled")
        with self._lock():
            destination = self._version_path(component_id, descriptor["version"])
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            _sync_dir(self.root)
            temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=self.root))
            transition("queued", staging=str(temporary))
            activation_intent = False
            try:
                available = shutil.disk_usage(self.root).free
                required = descriptor["installed_size"] + descriptor["archive_size"] + 256 * 1024 * 1024
                if available < required:
                    raise ComponentError("Insufficient space; recovery material was preserved")
                if archive is None:
                    if not descriptor.get("url"):
                        raise ComponentError("Component has no published download URL")
                    transition("downloading")
                    archive = temporary / "download.tar.gz"
                    started = time.monotonic()
                    with urllib.request.urlopen(descriptor["url"], timeout=15) as response:
                        if not response.geturl().startswith("https://"):
                            raise ComponentError("Insecure component download redirect")
                        fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        count = 0
                        with os.fdopen(fd, "wb") as output:
                            while True:
                                check_cancel()
                                if time.monotonic() - started > 3600:
                                    raise ComponentError("Component download deadline exceeded")
                                block = response.read(1024 * 1024)
                                if not block:
                                    break
                                count += len(block)
                                if count > descriptor["archive_size"]:
                                    raise ComponentError("Download exceeds pinned size")
                                output.write(block)
                            output.flush()
                            os.fsync(output.fileno())
                else:
                    transition("staging")
                    snapshot = temporary / "import.tar.gz"
                    self._stage_local_archive(Path(archive), snapshot, descriptor, check_cancel)
                    archive = snapshot
                check_cancel()
                transition("verifying")
                if archive.is_symlink() or archive.stat().st_size != descriptor["archive_size"]:
                    raise ComponentError("Archive size or file identity is invalid")
                if sha256_file(archive) != descriptor["sha256"]:
                    raise ComponentError("Archive checksum mismatch")
                unpacked = temporary / "version"
                unpacked.mkdir(mode=0o700)
                transition("extracting")
                observed = set()
                with tarfile.open(archive, "r:gz") as package:
                    for member in package:
                        check_cancel()
                        relative = _safe_relative(member.name)
                        if not member.isfile() or member.name in observed:
                            raise ComponentError("Archive must contain unique regular files only")
                        entry = descriptor["files"].get(member.name)
                        if not entry or member.size != entry["size"]:
                            raise ComponentError("Archive member disagrees with pinned inventory")
                        target = unpacked / relative
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        source = package.extractfile(member)
                        if source is None:
                            raise ComponentError("Archive member is unreadable")
                        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        with source, os.fdopen(fd, "wb") as output:
                            shutil.copyfileobj(source, output, 1024 * 1024)
                            output.flush()
                            os.fchmod(output.fileno(), entry["mode"])
                            os.fsync(output.fileno())
                        observed.add(member.name)
                if observed != set(descriptor["files"]):
                    raise ComponentError("Archive inventory is incomplete")
                self._verify_tree(unpacked, descriptor)
                transition("probing")
                self._probe(unpacked, descriptor)
                check_cancel()
                _write_json(unpacked / ".installed.json", descriptor)
                for directory in sorted((p for p in unpacked.rglob("*") if p.is_dir()), reverse=True):
                    _sync_dir(directory)
                _sync_dir(unpacked)
                transition("activating")
                activation_intent = True
                if destination.exists():
                    if self._installed_descriptor(destination) != descriptor:
                        raise ComponentError("Existing version is not the requested trusted installation")
                    self._verify_tree(destination, descriptor)
                else:
                    os.rename(unpacked, destination)
                    _sync_dir(destination.parent)
                registry = self._registry()
                entry = registry["components"].setdefault(component_id, {})
                old = entry.get("active")
                entry.update(active=descriptor["version"], enabled=True)
                if old and old != descriptor["version"]:
                    entry["previous"] = old
                _write_json(self.registry_path, registry)
                transition("completed", path=str(destination))
                return destination
            except InterruptedError:
                transition("cancelled")
                raise
            except Exception as exc:
                # Once activation starts, a failed fsync/receipt write cannot
                # establish failure. Preserve the durable intent and reconcile
                # actual registry/tree state on the next explicit recovery.
                if not activation_intent:
                    transition("failed", error=str(exc))
                else:
                    raise ComponentError("Activation outcome requires reconciliation; run Recover before retrying") from exc
                raise
            finally:
                shutil.rmtree(temporary)
                _sync_dir(self.root)

    def import_legacy_model(self, source: Path, *, cancelled=None, progress=None) -> Path:
        """Import only pinned model bytes; recreate the catalogued data pack.

        Small legal/provenance files are embedded in the shipped catalogue so
        importing existing verified assets requires no network access.
        """
        descriptor = self.catalog.get("ai.mobilenet-v2")
        if not descriptor or not isinstance(descriptor.get("import_content"), dict):
            raise ComponentError("This catalogue has no offline legacy import recipe; import the complete model pack")
        from .component_runtime import _snapshot
        with tempfile.TemporaryDirectory(prefix=".model-import-", dir=self.root) as directory:
            staging = Path(directory)
            payload = staging / "payload"
            payload.mkdir(mode=0o700)
            for name, entry in descriptor["files"].items():
                if cancelled and cancelled():
                    raise InterruptedError("Legacy model import cancelled")
                relative = _safe_relative(name)
                target = payload / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if name in {"mobilenetv2.onnx", "labels.txt"}:
                    snapshot = _snapshot(source / name, payload)
                    snapshot.rename(target)
                else:
                    content = base64.b64decode(descriptor["import_content"][name], validate=True)
                    if len(content) > 1024 * 1024:
                        raise ComponentError("Legacy import metadata exceeds bound")
                    target.write_bytes(content)
                target.chmod(entry["mode"])
            self._verify_tree(payload, descriptor)
            archive = staging / "import.tar.gz"
            with archive.open("xb") as output, gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as package:
                    for name, entry in sorted(descriptor["files"].items()):
                        member = tarfile.TarInfo(name)
                        member.size, member.mode, member.mtime = entry["size"], entry["mode"], 0
                        with (payload / name).open("rb") as content:
                            package.addfile(member, content)
            return self.install("ai.mobilenet-v2", archive, cancelled=cancelled, progress=progress)

    @contextlib.contextmanager
    def acquire(self, component_id: str) -> Iterator[tuple[Path, dict]]:
        with self._acquire(component_id) as leased:
            yield leased

    @contextlib.contextmanager
    def acquire_path(self, component_id: str, path: Path) -> Iterator[tuple[Path, dict]]:
        """Lease the exact selected version, even if activation changed meanwhile."""
        with self._acquire(component_id, path=Path(path)) as leased:
            yield leased

    @contextlib.contextmanager
    def _acquire(self, component_id: str, *, path: Path | None = None) -> Iterator[tuple[Path, dict]]:
        with self._lock():
            if path is not None:
                expected = self._version_path(component_id, path.name)
                entry = self._registry()["components"].get(component_id, {})
                if path != expected or not entry.get("enabled") or not path.is_dir():
                    raise ComponentError("Selected managed component version is unavailable or disabled")
            else:
                path = self.active_path(component_id)
            if path is None:
                raise ComponentError(f"Optional component is not enabled: {component_id}")
            descriptor = self._installed_descriptor(path)
            if not self._compatible(descriptor):
                raise ComponentError(self._compatibility_detail(descriptor))
            lease = self._lock(self.root / (f".lease-{component_id}-{path.name}"), shared=True)
            lease.__enter__()
            try:
                self._verify_tree(path, descriptor)
            except BaseException:
                lease.__exit__(None, None, None)
                raise
        try:
            yield path, descriptor
        finally:
            lease.__exit__(None, None, None)

    def verify(self, component_id: str) -> None:
        with self.acquire(component_id) as (path, descriptor):
            self._probe(path, descriptor)

    def enable(self, component_id: str, enabled: bool = True) -> None:
        with self._lock():
            registry = self._registry()
            entry = registry["components"].get(component_id)
            if not entry or not entry.get("active"):
                raise ComponentError("Component is not installed")
            if enabled:
                path = self._version_path(component_id, entry["active"])
                descriptor = self._installed_descriptor(path)
                self._verify_tree(path, descriptor)
                self._probe(path, descriptor)
            entry["enabled"] = enabled
            _write_json(self.registry_path, registry)

    def rollback(self, component_id: str) -> None:
        with self._lock():
            registry = self._registry()
            entry = registry["components"].get(component_id, {})
            previous = entry.get("previous")
            if not previous:
                raise ComponentError("No previous qualified version is retained")
            path = self._version_path(component_id, previous)
            descriptor = self._installed_descriptor(path)
            self._verify_tree(path, descriptor)
            self._probe(path, descriptor)
            entry["active"], entry["previous"] = previous, entry.get("active")
            entry["enabled"] = True
            _write_json(self.registry_path, registry)

    def remove(self, component_id: str, version: str | None = None) -> None:
        with self._lock(), contextlib.ExitStack() as leases:
            if component_id not in KNOWN_IDS:
                raise ComponentError("Unknown component")
            registry = self._registry()
            entry = registry["components"].get(component_id, {})
            directory = self.root / component_id
            if directory.is_symlink():
                raise ComponentError("Component directory is a symbolic link")
            versions = [version] if version else [path.name for path in directory.iterdir()] if directory.exists() else []
            if not versions:
                return
            paths = []
            for target_version in versions:
                path = self._version_path(component_id, target_version)
                leases.enter_context(self._lock(self.root / f".lease-{component_id}-{target_version}", blocking=False))
                descriptor = self._installed_descriptor(path)
                self._verify_tree(path, descriptor)
                paths.append(path)
            for target_version in versions:
                if entry.get("active") == target_version:
                    entry.update(active=None, enabled=False)
                if entry.get("previous") == target_version:
                    entry["previous"] = None
            _write_json(self.registry_path, registry)
            for path in paths:
                shutil.rmtree(path)
                _sync_dir(path.parent)

    def recover_jobs(self) -> list[dict]:
        """Reconcile interrupted jobs, preserving unclassified material for review."""
        records = []
        with self._lock():
            registry = self._registry()
            for path in sorted(self.jobs_dir.glob("*.json")):
                if path.is_symlink():
                    raise ComponentError("Installation receipt is a symbolic link")
                job = json.loads(path.read_text(encoding="utf-8"))
                if job["state"] == "recovery_required":
                    records.append(job)
                    continue
                if job["state"] in {"completed", "failed", "cancelled"}:
                    continue
                component = registry["components"].get(job["component_id"], {})
                if job["state"] == "activating" and component.get("active") == job["version"]:
                    destination = self._version_path(job["component_id"], job["version"])
                    self._verify_tree(destination, self._installed_descriptor(destination))
                    job["state"] = "completed"
                else:
                    job.update(state="recovery_required", error="Installation was interrupted; staged files retained")
                _write_json(path, job)
                records.append(job)
        return records

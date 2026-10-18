"""Filesystem primitives for recoverable local-file transactions.

Checks detect ordinary concurrent edits; an external writer retaining a writable
descriptor is not made immutable by hashing. Source claims remain recoverable.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from typing import Any


class FileChangedError(OSError):
    pass


class OperationCancelled(Exception):
    pass


class PrerequisiteError(OSError):
    """A required primitive was not established before original-file claims."""


def sync_directory(path: str) -> None:
    """Fail closed if directory durability cannot be established."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stable(st: os.stat_result) -> tuple[int, ...]:
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _read_xattrs(fd: int) -> dict[str, str]:
    if not all(callable(getattr(os, name, None)) for name in ("listxattr", "getxattr")):
        raise PrerequisiteError("Extended-attribute observation is unavailable; metadata is unknown")
    try:
        return {name: base64.b64encode(os.getxattr(fd, name)).decode("ascii")
                for name in sorted(os.listxattr(fd))}
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            raise PrerequisiteError("Extended-attribute observation is unsupported; metadata is unknown") from exc
        raise


def _set_xattrs(fd: int, expected: dict[str, str]) -> None:
    if not all(callable(getattr(os, name, None)) for name in ("setxattr", "removexattr")):
        raise PrerequisiteError("Extended-attribute preservation is unavailable")
    try:
        for name in set(_read_xattrs(fd)) - set(expected):
            os.removexattr(fd, name)
        for name, encoded in expected.items():
            os.setxattr(fd, name, base64.b64decode(encoded, validate=True))
    except OSError as exc:
        raise OSError(f"Destination cannot preserve source extended attributes/ACLs; source retained: {exc}") from exc


def apply_attributes(path: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Apply exact attributes privately, failing closed before publication."""
    fd = _open_regular(path)
    try:
        _set_xattrs(fd, expected.get("xattrs", {}))
        os.fchmod(fd, expected.get("mode", 0o600) & 0o7777)
        if expected.get("mtime_ns") is not None:
            os.utime(fd, ns=(expected["mtime_ns"], expected["mtime_ns"]))
        os.fsync(fd)
    finally:
        os.close(fd)
    observed = fingerprint(path)
    if (observed["xattrs"] != expected.get("xattrs", {})
            or observed["mode"] != expected.get("mode", 0o600)
            or (expected.get("mtime_ns") is not None and observed["mtime_ns"] != expected["mtime_ns"])):
        raise FileChangedError(f"Destination attributes/ACLs did not round-trip exactly; source retained: {path}")
    return observed


def _open_regular(path: str) -> int:
    if os.path.islink(path):
        raise ValueError(f"File is a symbolic link: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"Path is not a regular file: {path}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def fingerprint(path: str, cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    fd = _open_regular(path)
    try:
        before = os.fstat(fd)
        h = hashlib.sha256()
        while True:
            if cancelled and cancelled():
                raise OperationCancelled("Cancelled before publication")
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            h.update(chunk)
        xattrs = _read_xattrs(fd)
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if _stable(before) != _stable(after) or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino):
            raise FileChangedError(f"File changed while it was read: {path}")
        return {"size": after.st_size, "sha256": h.hexdigest(), "dev": after.st_dev,
                "ino": after.st_ino, "mtime": after.st_mtime, "mtime_ns": after.st_mtime_ns,
                "mode": stat.S_IMODE(after.st_mode), "xattrs": xattrs, "is_reg": True, "is_symlink": False}
    finally:
        os.close(fd)


def verify(path: str, expected: dict[str, Any], *, identity: bool = True) -> dict[str, Any]:
    observed = fingerprint(path)
    keys = (("size", "sha256", "dev", "ino", "mode", "mtime_ns", "xattrs") if identity
            else ("size", "sha256", "mode", "mtime_ns", "xattrs"))
    for key in keys:
        if key in expected and expected[key] != observed[key]:
            raise FileChangedError(f"File {key} mismatch; preserving changed file: {path}")
    return observed


def private_directory(parent: str, prefix: str) -> str:
    if not os.path.isdir(parent) or os.path.islink(parent):
        raise ValueError(f"Directory missing or symbolic: {parent}")
    path = tempfile.mkdtemp(prefix=prefix, dir=parent)
    try:
        os.chmod(path, 0o700)
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != os.getuid():
            raise PrerequisiteError("Private directory permissions or ownership did not round-trip")
        sync_directory(path)
        sync_directory(parent)
    except Exception as exc:
        # No user data has been put here. Retain an uncertain created name;
        # cleanup cannot be inferred from a successful mkdir/chmod call.
        raise PrerequisiteError(f"Private directory prerequisite failed; retained {path}: {exc}") from exc
    return path


def probe_directory(parent: str, *, require_links: bool = True) -> dict[str, Any]:
    """Test this operation's actual parent using only owned, constant bytes.

    A successful probe observes kernel calls, not power-loss durability or
    future availability. It does not qualify per-inode flags or other owners.
    The caller still verifies original provenance and rechecks at commit.
    """
    parent = os.path.abspath(parent)
    before = os.stat(parent, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise PrerequisiteError("Mutation parent is not a real directory")
    directory = private_directory(parent, ".imagesorter-prerequisite-")
    identity = os.stat(directory, follow_symlinks=False)
    first = os.path.join(directory, "probe")
    moved = os.path.join(directory, "renamed")
    public = directory + "-publication"
    quarantine = os.path.join(directory, "publication-cleanup")
    expected: dict[str, Any] | None = None
    try:
        fd = os.open(first, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            observed = os.fstat(fd)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or observed.st_uid != os.getuid():
                raise PrerequisiteError("Private file identity prerequisite failed")
            os.fchmod(fd, 0o600)
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
                raise PrerequisiteError("Private file permissions did not round-trip")
            payload = b"Image Sorter disposable filesystem prerequisite\n"
            if os.write(fd, payload) != len(payload):
                raise PrerequisiteError("Prerequisite probe short write")
            os.fsync(fd)
        finally:
            os.close(fd)
        expected = fingerprint(first)
        os.rename(first, moved)
        sync_directory(directory)
        verify(moved, expected)
        if require_links:
            os.link(moved, public, follow_symlinks=False)
            sync_directory(parent)
            verify(public, expected)
            try:
                os.link(moved, public, follow_symlinks=False)
            except FileExistsError:
                pass
            else:
                raise PrerequisiteError("No-replace publication did not refuse an occupied name")
            verify(public, expected)
            # This public name is ours, but a pathname check is not an
            # inode-conditional unlink. Claim and validate before cleanup.
            unlink_owned(public, expected, quarantine=quarantine)
        after = os.stat(parent, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise PrerequisiteError("Mutation parent identity changed during preflight")
        observed = os.stat(directory, follow_symlinks=False)
        if (identity.st_dev, identity.st_ino) != (observed.st_dev, observed.st_ino):
            raise PrerequisiteError("Prerequisite directory identity changed")
        unlink_owned(moved, expected)
        # An unexpected entry prevents removal; never recursively clean it.
        os.rmdir(directory)
        sync_directory(parent)
        return {"parent": parent, "dev": after.st_dev, "ino": after.st_ino,
                "links_required": require_links, "status": "SUPPORTED_OBSERVED_FOR_THIS_OPERATION"}
    except Exception as exc:
        # An unsupported primitive or uncertain cleanup never authorizes an
        # original-file claim. Keep every remaining probe entry inspectable.
        raise PrerequisiteError(f"Filesystem prerequisite failed in {parent}; inspect retained probe {directory}: {exc}") from exc


def ensure_capacity(parent: str, staging_bytes: int) -> None:
    usage = shutil.disk_usage(parent)
    reserve = max(256 * 1024**2, min(1024**3, usage.total // 20))
    if usage.free < staging_bytes + reserve:
        raise OSError(f"Insufficient free space for safe staging and recovery reserve in {parent}")


def secure_copy(source: str, stage: str, expected: dict[str, Any],
                cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Exclusively create a mode-0600 stage and verify both bytes and source."""
    source_fd = _open_regular(source)
    stage_fd: int | None = None
    try:
        before = os.fstat(source_fd)
        if any(expected.get(key, val) != val for key, val in (("dev", before.st_dev), ("ino", before.st_ino))):
            raise FileChangedError(f"Source was replaced: {source}")
        stage_fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        h = hashlib.sha256()
        while True:
            if cancelled and cancelled():
                raise OperationCancelled("Cancelled before publication")
            chunk = os.read(source_fd, 65536)
            if not chunk:
                break
            h.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(stage_fd, view)
                if written <= 0:
                    raise OSError("Staged write made no progress")
                view = view[written:]
        source_xattrs = _read_xattrs(source_fd)
        after = os.fstat(source_fd)
        named = os.stat(source, follow_symlinks=False)
        if (_stable(before) != _stable(after) or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)
                or after.st_size != expected["size"] or h.hexdigest() != expected["sha256"]
                or source_xattrs != expected.get("xattrs", source_xattrs)):
            raise FileChangedError(f"Source changed during staging: {source}")
        _set_xattrs(stage_fd, source_xattrs)
        # POSIX ACL application can update mode bits. Keep staged bytes private
        # until the final original ACL/mode is restored in the private commit
        # preparation directory, before the publication intent is persisted.
        os.fchmod(stage_fd, 0o600)
        os.fsync(stage_fd)
    finally:
        os.close(source_fd)
        if stage_fd is not None:
            os.close(stage_fd)
    return verify(stage, {key: expected[key] for key in ("size", "sha256")}, identity=False)


def publish_no_replace(stage: str, destination: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Atomically link-if-absent; never replace a public destination reservation."""
    verify(stage, expected)
    os.link(stage, destination, follow_symlinks=False)
    sync_directory(os.path.dirname(destination))
    return verify(destination, expected)


def claim_source(source: str, claim: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Rename into a newly created private operation directory, retaining bytes."""
    verify(source, expected)
    if os.path.lexists(claim):
        raise FileExistsError(claim)
    os.rename(source, claim)
    sync_directory(os.path.dirname(claim))
    sync_directory(os.path.dirname(source))
    return verify(claim, expected)


def unlink_owned(path: str, expected: dict[str, Any], *, quarantine: str | None = None) -> None:
    """Delete a private owned name, or first claim a public name for validation.

    Public callers must durably record a unique private quarantine path before
    calling. A pathname verification is not an inode-conditional unlink: an
    ordinary editor may replace the public name immediately after verification.
    A mismatched claim is retained and restored only if its public name is free.
    """
    owned = path
    if quarantine is not None:
        if not os.path.lexists(quarantine):
            verify(path, expected)
            os.rename(path, quarantine)
            sync_directory(os.path.dirname(quarantine))
            sync_directory(os.path.dirname(path))
        owned = quarantine
        try:
            verify(owned, expected)
        except Exception as exc:
            try:
                os.link(owned, path, follow_symlinks=False)
                sync_directory(os.path.dirname(path))
            except OSError:
                pass
            raise FileChangedError(f"Changed public file retained at {owned}: {exc}") from exc
    else:
        # Only service-owned names inside private operation directories use
        # this branch. It is not safe for a publicly replaceable pathname.
        verify(owned, expected)
    os.unlink(owned)
    sync_directory(os.path.dirname(owned))

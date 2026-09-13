"""Bounded JSON transport and launch identity for isolated worker roles."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import sys
from pathlib import Path

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024

def encode(message: dict) -> bytes:
    body = json.dumps({"protocol_version": PROTOCOL_VERSION, **message}, allow_nan=False).encode()
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError("Worker message exceeds size limit")
    return body + b"\n"

def decode(body: bytes) -> dict:
    if len(body) > MAX_MESSAGE_BYTES:
        raise ValueError("Worker message exceeds size limit")
    def invalid_constant(value):
        raise ValueError(f"Invalid JSON constant: {value}")
    message = json.loads(body, parse_constant=invalid_constant)
    if (not isinstance(message, dict) or type(message.get("protocol_version")) is not int or
            message.get("protocol_version") != PROTOCOL_VERSION):
        raise ValueError("Unsupported worker protocol")
    return message

def worker_command(role: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--{role}"]
    return [sys.executable, "-m", "imagesorter.bootstrap", f"--{role}"]

def worker_environment() -> dict[str, str]:
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        source = Path(__file__).resolve().parent.parent
        if (source.parent / "pyproject.toml").is_file():
            env["PYTHONPATH"] = str(source)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env

def _checked_socket_base(path: Path = Path("/tmp")) -> Path:
    # macOS TMPDIR and application profiles can exceed sockaddr_un.sun_path.
    # Resolve the legitimate /tmp -> /private/tmp link, then trust only the
    # system-owned sticky directory rather than a caller-controlled TMPDIR.
    base = path.resolve(strict=True)
    info = base.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or not info.st_mode & stat.S_ISVTX:
        raise PermissionError("Mutation socket base must be a system-owned sticky directory")
    return base


def _private_socket_directory() -> Path:
    directory = _checked_socket_base() / f"imagesorter-sockets-{os.getuid()}"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o700):
        raise PermissionError("Mutation socket directory must be owned by the current user with mode 0700 and no symlink")
    return directory


def service_address(db_path: str) -> str:
    digest = hashlib.sha256(os.path.realpath(db_path).encode()).hexdigest()[:32]
    if sys.platform.startswith("linux"):
        return f"\0imagesorter-{os.getuid()}-{digest}"
    address = str(_private_socket_directory() / f"{digest}.sock")
    # Darwin's sun_path holds 104 bytes including its terminating NUL.
    if len(os.fsencode(address)) > 103:
        raise ValueError("Mutation socket address exceeds the supported pathname limit")
    return address

def verify_peer(connection: socket.socket) -> None:
    if hasattr(socket, "SO_PEERCRED"):
        import struct
        _pid, uid, _gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise PermissionError("Worker belongs to a different user")

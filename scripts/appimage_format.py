"""Independently verify type2 runtime bytes; never execute the delivery to trust it.

The pinned appimagetool 1.9.1 digest.c reads uninitialized buffer gaps and tails.
Its optional MD5 metadata is deliberately left empty; SHA-256 is authoritative.
Only a builder may normalize that exact named field in its owned output.
Upstream format references:
https://github.com/AppImage/type2-runtime/blob/dd6cebedcbddde9c82f89b011e8e1d40b6e43868/src/runtime/runtime.c
https://github.com/AppImage/appimagetool/blob/8c8c91f762b412a19f4e8d2c4b35afb98f2d7c81/src/digest.c
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import struct
from pathlib import Path

MAX_PREFIX = 20 * 1024 * 1024


class AppImageFormatError(ValueError):
    pass


def _need(condition, message):
    if not condition:
        raise AppImageFormatError(message)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open(path, *, writable=False):
    flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        _need(stat.S_ISREG(os.fstat(fd).st_mode), "AppImage input must be a regular non-symlink file")
        return os.fdopen(fd, "r+b" if writable else "rb")
    except BaseException:
        os.close(fd)
        raise


def _layout(data):
    _need(len(data) >= 64 and data[:6] == b"\x7fELF\x02\x01" and data[8:11] == b"AI\x02",
          "Only ELF64 little-endian type2 AppImage runtimes are supported")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    phoff, shoff, ehsize, phsize, phnum, shsize, shnum = header[5:7] + header[8:13]
    _need(ehsize == 64 and shsize == 64 and 0 < shnum < 65535 and phnum < 65535 and
          (phnum == 0 or phsize == 56) and shoff >= 64 and shoff + shsize * shnum <= len(data),
          "Invalid or unsupported AppImage ELF tables")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shsize) for index in range(shnum)]
    last = sections[-1]
    offset = max(shoff + shsize * shnum, last[4] + last[5])
    _need(0 < offset <= len(data) and offset <= MAX_PREFIX and phoff + phsize * phnum <= offset,
          "Invalid AppImage runtime boundary")
    for section in sections:
        _need(section[1] == 8 or section[4] + section[5] <= offset, "ELF section extends into payload")
    for index in range(phnum):
        segment = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phsize)
        _need(segment[2] + segment[5] <= offset, "ELF program segment extends into payload")
    _need(0 <= header[13] < shnum, "Unsupported extended section-name index")
    strings = sections[header[13]]
    _need(strings[4] + strings[5] <= offset, "ELF section strings escape runtime")
    names = data[strings[4]:strings[4] + strings[5]]
    digest_fields = []
    for section in sections:
        name = names[section[0]:].split(b"\0", 1)[0] if section[0] < len(names) else b""
        if name == b".digest_md5":
            _need(section[1] == 1 and section[5] == 16 and section[4] + 16 <= offset,
                  "Compatibility digest must be the exact named 16-byte data section")
            digest_fields.append(section[4])
    _need(len(digest_fields) <= 1, "Ambiguous compatibility digest section")
    return offset, digest_fields[0] if digest_fields else None


def appimage_offset(path):
    with _open(path) as stream:
        data = stream.read(MAX_PREFIX + 4)
    offset, _ = _layout(data)
    _need(data[offset:offset + 4] == b"hsqs", "SquashFS is not at the actual runtime ELF boundary")
    return offset


def _trusted_runtime(path, expected_sha):
    _need(isinstance(expected_sha, str) and re.fullmatch(r"[0-9a-f]{64}", expected_sha), "Full source-approved runtime SHA-256 required")
    with _open(path) as stream:
        before = os.fstat(stream.fileno())
        _need(before.st_size <= MAX_PREFIX, "Runtime build input exceeds prefix bound")
        data = stream.read(MAX_PREFIX + 1)
        _need(_identity(before) == _identity(os.fstat(stream.fileno())), "Runtime input changed during verification")
    _need(hashlib.sha256(data).hexdigest() == expected_sha, "Runtime build input differs from source-approved SHA-256")
    offset, field = _layout(data)
    _need(len(data) == offset, "Runtime input contains bytes beyond its ELF boundary")
    if field is not None:
        _need(data[field:field + 16] == b"\0" * 16, "Trusted runtime MD5 metadata must be unpopulated")
    return data, offset, field


def _verify_stream(stream, trusted, offset, expected_sha, field):
    before = os.fstat(stream.fileno())
    stream.seek(0)
    _need(stream.read(offset) == trusted, "AppImage executable prefix differs from pinned runtime")
    _need(stream.read(4) == b"hsqs", "SquashFS is not at the actual runtime ELF boundary")
    stream.seek(0)
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    _need(_identity(before) == _identity(os.fstat(stream.fileno())), "AppImage changed while being verified")
    return {"squashfs_offset": offset, "runtime_sha256": expected_sha,
            "artifact_sha256": value.hexdigest(), "md5_metadata": "intentionally-unpopulated",
            "digest_section": {"offset": field, "length": 16} if field is not None else None}


def verify_runtime_prefix(artifact: Path, runtime_input: Path, expected_runtime_sha256: str) -> dict:
    """Read-only verification: no differing prefix byte, including MD5, is accepted."""
    trusted, offset, field = _trusted_runtime(runtime_input, expected_runtime_sha256)
    with _open(artifact) as stream:
        return _verify_stream(stream, trusted, offset, expected_runtime_sha256, field)


def normalize_runtime_digest(artifact: Path, runtime_input: Path, expected_runtime_sha256: str) -> dict:
    """Builder-only, owned-output normalization of exactly the named 16-byte field.

    Refuses any other prefix change before writing, is idempotent, fsyncs the
    artifact, then verifies strict prefix equality and the whole SHA-256.
    """
    trusted, offset, field = _trusted_runtime(runtime_input, expected_runtime_sha256)
    _need(field is not None, "Trusted runtime has no digest section to normalize")
    with _open(artifact, writable=True) as stream:
        before = os.fstat(stream.fileno())
        prefix = bytearray(stream.read(offset))
        _need(len(prefix) == offset and stream.read(4) == b"hsqs", "Incomplete owned AppImage output")
        changed = prefix[field:field + 16] != trusted[field:field + 16]
        prefix[field:field + 16] = trusted[field:field + 16]
        _need(bytes(prefix) == trusted, "Refusing normalization: another runtime-prefix byte differs")
        _need(_identity(before) == _identity(os.fstat(stream.fileno())), "Owned AppImage changed before normalization")
        if changed:
            stream.seek(field)
            stream.write(trusted[field:field + 16])
            stream.flush()
            os.fsync(stream.fileno())
        result = _verify_stream(stream, trusted, offset, expected_runtime_sha256, field)
        result["normalized_md5_metadata"] = changed
        return result

"""Bounded Darwin metadata primitives; this does not admit native mutations.

Only descriptor APIs are used. The portable-endian ACL image is validated before
passing it to acl_copy_int (which has no length argument). See macos-metadata.md.
"""
from __future__ import annotations

import base64
import ctypes as C
import errno
import os
import stat
import struct
import sys
from functools import lru_cache
from typing import Any

ACL_TYPE_EXTENDED = 0x100
ACL_MAGIC = 0x012CC16D
ACL_HEADER = 44
ACL_ENTRY = 24
ACL_MAX_ENTRIES = 128
ACL_ABSENT = struct.pack(">I16s16sII", ACL_MAGIC, bytes(16), bytes(16), 0xFFFFFFFF, 0)
PC_EXTENDED_SECURITY = 13
ALLOWED_FLAGS = 0x8001  # UF_HIDDEN | UF_NODUMP; no content/access semantics.
MAX_NAMES = 65536
MAX_ATTRIBUTES = 256
MAX_VALUE = 16 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024


class MetadataUnavailable(OSError):
    """Observation/restoration is incomplete; retain source and private stages."""


def _fail(message: str) -> None:
    raise MetadataUnavailable(message)


def _bytes(encoded: Any, limit: int) -> bytes:
    if not isinstance(encoded, str) or len(encoded) > ((limit + 2) // 3) * 4:
        _fail("Invalid or oversized native metadata encoding")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise MetadataUnavailable("Invalid native metadata encoding") from exc
    if len(raw) > limit or base64.b64encode(raw).decode("ascii") != encoded:
        _fail("Noncanonical or oversized native metadata encoding")
    return raw


def validate_acl(raw: bytes) -> int:
    """Validate complete framing before any native pointer-based decoder call."""
    if len(raw) < ACL_HEADER or len(raw) > ACL_HEADER + ACL_MAX_ENTRIES * ACL_ENTRY:
        _fail("Unsupported Darwin ACL size")
    magic, owner, group, count, flags = struct.unpack(">I16s16sII", raw[:ACL_HEADER])
    if raw == ACL_ABSENT:
        return -1
    if (magic != ACL_MAGIC or owner != bytes(16) or group != bytes(16)
            or count > ACL_MAX_ENTRIES or len(raw) != ACL_HEADER + count * ACL_ENTRY):
        _fail("Invalid or unsupported Darwin ACL representation")
    if flags & ~0x20000:
        _fail("Unknown or deferred-inheritance Darwin ACL flags")
    for offset in range(ACL_HEADER, len(raw), ACL_ENTRY):
        ace_flags, rights = struct.unpack(">II", raw[offset + 16:offset + ACL_ENTRY])
        if ace_flags & 0xF not in (1, 2) or ace_flags & ~0x1F3 or rights & ~0x103FFE:
            _fail("Unknown Darwin ACL entry semantics")
    return count


def validate_xattrs(expected: Any) -> dict[bytes, bytes]:
    if not isinstance(expected, dict) or len(expected) > MAX_ATTRIBUTES:
        _fail("Invalid or excessive native extended attributes")
    decoded: dict[bytes, bytes] = {}
    total = 0
    for name, encoded in expected.items():
        if not isinstance(name, str):
            _fail("Invalid native attribute name")
        try:
            key = name.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise MetadataUnavailable("Invalid native attribute name") from exc
        if not key or len(key) > 127 or b"\0" in key or key == b"com.apple.system.Security":
            _fail("Unsupported native attribute name/security channel")
        value = _bytes(encoded, MAX_VALUE)
        total += len(key) + len(value)
        if total > MAX_TOTAL:
            _fail("Native metadata exceeds bounded snapshot limit")
        decoded[key] = value
    return decoded


def validate_descriptor(value: Any) -> bytes:
    keys = {"schema", "backend", "acl", "flags", "uid", "gid", "ownership", "creation_time"}
    if (not isinstance(value, dict) or set(value) != keys or value.get("schema") != 1
            or type(value.get("schema")) is not int
            or value.get("backend") != "darwin-acl-ext-v1"
            or value.get("ownership") != "same-user-group-preserved"
            or value.get("creation_time") != "observed-not-restored"):
        _fail("Missing, foreign or unsupported Darwin metadata descriptor")
    for key in ("uid", "gid", "flags"):
        if type(value[key]) is not int or not 0 <= value[key] <= 0xFFFFFFFF:
            _fail("Invalid Darwin metadata integer")
    if value["flags"] & ~ALLOWED_FLAGS:
        _fail("Unsupported Darwin file flags; source retained")
    raw = _bytes(value["acl"], ACL_HEADER + ACL_MAX_ENTRIES * ACL_ENTRY)
    validate_acl(raw)
    return raw


class DarwinMetadata:
    def __init__(self, library: Any = None) -> None:
        if library is None:
            if sys.platform != "darwin":
                _fail("Darwin native metadata is unavailable on this platform")
            library = C.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        self.lib = library
        signatures = {
            "flistxattr": ([C.c_int, C.c_void_p, C.c_size_t, C.c_int], C.c_ssize_t),
            "fgetxattr": ([C.c_int, C.c_char_p, C.c_void_p, C.c_size_t, C.c_uint32, C.c_int], C.c_ssize_t),
            "fsetxattr": ([C.c_int, C.c_char_p, C.c_void_p, C.c_size_t, C.c_uint32, C.c_int], C.c_int),
            "fremovexattr": ([C.c_int, C.c_char_p, C.c_int], C.c_int),
            "acl_get_fd_np": ([C.c_int, C.c_int], C.c_void_p),
            "acl_size": ([C.c_void_p], C.c_ssize_t),
            "acl_copy_ext": ([C.c_void_p, C.c_void_p, C.c_ssize_t], C.c_ssize_t),
            "acl_copy_int": ([C.c_void_p], C.c_void_p),
            "acl_set_fd_np": ([C.c_int, C.c_void_p, C.c_int], C.c_int),
            "acl_free": ([C.c_void_p], C.c_int),
            "fchflags": ([C.c_int, C.c_uint32], C.c_int),
            "fpathconf": ([C.c_int, C.c_int], C.c_long),
        }
        try:
            for name, (args, result) in signatures.items():
                function = getattr(library, name)
                function.argtypes, function.restype = args, result
        except AttributeError as exc:
            raise MetadataUnavailable("Required Darwin metadata API is unavailable") from exc

    @staticmethod
    def _check(result: int, operation: str) -> int:
        if result < 0:
            error = C.get_errno()
            raise MetadataUnavailable(error, f"Darwin {operation} failed; metadata is unknown")
        return result

    def _sized(self, call: Any, limit: int) -> bytes:
        for _ in range(3):
            size = self._check(call(None, 0), "metadata sizing")
            if size > limit:
                _fail("Darwin metadata exceeds bounded snapshot limit")
            # Non-NULL even at zero: a second size query could conceal growth.
            buffer = C.create_string_buffer(max(1, size))
            result = call(buffer, size)
            if result < 0 and C.get_errno() == errno.ERANGE:
                continue
            self._check(result, "metadata read")
            if result == size:
                return buffer.raw[:result]
        _fail("Darwin metadata changed during bounded observation")

    def read_xattrs(self, fd: int) -> dict[str, str]:
        def names() -> bytes:
            return self._sized(lambda b, n: self.lib.flistxattr(fd, b, n, 0), MAX_NAMES)
        first = names()
        if first and not first.endswith(b"\0"):
            _fail("Malformed Darwin attribute name list")
        raw_names = first[:-1].split(b"\0") if first else []
        if len(raw_names) > MAX_ATTRIBUTES or len(set(raw_names)) != len(raw_names):
            _fail("Excessive or duplicate Darwin attribute names")
        result = {}
        total = 0
        for name in sorted(raw_names):
            if not name or len(name) > 127 or name == b"com.apple.system.Security":
                _fail("Unsupported Darwin attribute name/security channel")
            try:
                key = name.decode("utf-8", "strict")
            except UnicodeError as exc:
                raise MetadataUnavailable("Invalid Darwin attribute name encoding") from exc
            raw = self._sized(lambda b, n, name=name: self.lib.fgetxattr(fd, name, b, n, 0, 0), min(MAX_VALUE, MAX_TOTAL - total))
            total += len(name) + len(raw)
            if total > MAX_TOTAL:
                _fail("Darwin metadata exceeds bounded snapshot limit")
            result[key] = base64.b64encode(raw).decode("ascii")
        if names() != first:
            _fail("Darwin attribute list changed during observation")
        return result

    def set_xattrs(self, fd: int, expected: dict[str, str]) -> None:
        decoded = validate_xattrs(expected)  # Validate everything before the first write.
        current = self.read_xattrs(fd)
        for name in set(current) - set(expected):
            self._check(self.lib.fremovexattr(fd, name.encode("utf-8"), 0), "attribute removal")
        for name, raw in decoded.items():
            buffer = C.create_string_buffer(raw)
            self._check(self.lib.fsetxattr(fd, name, buffer, len(raw), 0, 0), "attribute restore")
        if self.read_xattrs(fd) != expected:
            _fail("Darwin extended attributes did not round-trip; private stage retained")

    def _acl_external(self, pointer: Any) -> bytes:
        size = self._check(self.lib.acl_size(pointer), "ACL sizing")
        if not ACL_HEADER <= size <= ACL_HEADER + ACL_MAX_ENTRIES * ACL_ENTRY:
            _fail("Unsupported Darwin ACL size")
        buffer = C.create_string_buffer(size)
        if self._check(self.lib.acl_copy_ext(buffer, pointer, size), "ACL serialization") != size:
            _fail("Darwin ACL serialization size changed")
        raw = buffer.raw
        validate_acl(raw)
        return raw

    def read_acl(self, fd: int) -> bytes:
        if self.lib.fpathconf(fd, PC_EXTENDED_SECURITY) != 1:
            _fail("Darwin filesystem extended-security capability is unsupported or unknown")
        C.set_errno(0)
        pointer = self.lib.acl_get_fd_np(fd, ACL_TYPE_EXTENDED)
        if not pointer:
            # Apple's fd-only implementation sets ENOENT when FILESEC_ACL is
            # absent after successful fstatx. EBADF/access/unsupported/unknown
            # are distinct refusals. Keep absence distinct from an empty ACL.
            if C.get_errno() == errno.ENOENT:
                return ACL_ABSENT
            self._check(-1, "ACL observation")
        try:
            return self._acl_external(pointer)
        finally:
            self.lib.acl_free(pointer)

    def set_acl(self, fd: int, raw: bytes) -> None:
        count = validate_acl(raw)
        if count == -1:
            # Documented _FILESEC_REMOVE_ACL sentinel; not an allocated acl_t.
            self._check(self.lib.acl_set_fd_np(fd, 1, ACL_TYPE_EXTENDED), "ACL absence restore")
            if self.read_acl(fd) != raw:
                _fail("Darwin ACL absence did not round-trip; private stage retained")
            return
        buffer = C.create_string_buffer(raw)
        pointer = self.lib.acl_copy_int(buffer)
        if not pointer:
            self._check(-1, "ACL deserialization")
        try:
            if self._acl_external(pointer) != raw:
                _fail("Darwin ACL representation did not round-trip")
            self._check(self.lib.acl_set_fd_np(fd, pointer, ACL_TYPE_EXTENDED), "ACL restore")
        finally:
            self.lib.acl_free(pointer)
        if self.read_acl(fd) != raw:
            _fail("Darwin ACL did not round-trip; private stage retained")

    def check_stat(self, st: os.stat_result) -> None:
        flags = getattr(st, "st_flags", None)
        if type(flags) is not int or flags & ~ALLOWED_FLAGS:
            _fail("Unsupported or unknown Darwin file flags; source retained without reading content")
        if st.st_uid != os.geteuid() or st.st_gid not in {os.getegid(), *os.getgroups()}:
            _fail("Darwin owner/group cannot be preserved without elevation")
        if stat.S_IMODE(st.st_mode) & 0o7000:
            _fail("Special permission bits are not qualified for Darwin restoration")
        if not hasattr(st, "st_birthtime"):
            _fail("Darwin creation time observation is unavailable")

    def capture(self, fd: int) -> dict[str, Any]:
        st = os.fstat(fd)
        self.check_stat(st)
        return {"schema": 1, "backend": "darwin-acl-ext-v1", "acl": base64.b64encode(self.read_acl(fd)).decode("ascii"),
                "flags": st.st_flags, "uid": st.st_uid, "gid": st.st_gid,
                "ownership": "same-user-group-preserved", "creation_time": "observed-not-restored"}

    def require_private(self, fd: int, mode: int) -> None:
        st = os.fstat(fd)
        self.check_stat(st)
        raw = self.read_acl(fd)
        if (stat.S_IMODE(st.st_mode) != mode or st.st_uid != os.geteuid()
                or validate_acl(raw) > 0 or struct.unpack(">I", raw[40:44])[0] != 0):
            _fail("Darwin private stage/directory has nonempty ACL or unexpected ownership/mode")

    def restore(self, fd: int, descriptor: dict[str, Any]) -> None:
        raw = validate_descriptor(descriptor)
        current = os.fstat(fd)
        self.check_stat(current)
        if descriptor["uid"] != current.st_uid or descriptor["gid"] not in {os.getegid(), *os.getgroups()}:
            _fail("Darwin owner/group restoration is unsupported")
        if current.st_gid != descriptor["gid"]:
            os.fchown(fd, -1, descriptor["gid"])
        self.set_acl(fd, raw)
        self._check(self.lib.fchflags(fd, descriptor["flags"]), "file flag restoration")
        if self.capture(fd) != descriptor:
            _fail("Darwin native metadata did not round-trip; private stage retained")


@lru_cache(maxsize=1)
def backend() -> DarwinMetadata:
    return DarwinMetadata()

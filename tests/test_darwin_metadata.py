"""Synthetic ABI/fault checks plus mandatory owned-fixture macOS qualification.

Native tests are absent on Linux, never counted as native proof. The dedicated
macOS CI invocation requires all native tests and its explicit receipt.
"""
from __future__ import annotations

import base64
import ctypes as C
import errno
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from imagesorter import darwin_metadata as dm
from imagesorter import file_safety as safe


def acl(count=0, flags=0):
    return struct.pack(">I16s16sII", dm.ACL_MAGIC, bytes(16), bytes(16), count, flags) + (bytes(16) + struct.pack(">II", 1, 2)) * count


def descriptor(raw=None):
    return {"schema": 1, "backend": "darwin-acl-ext-v1", "acl": base64.b64encode(acl() if raw is None else raw).decode(),
            "flags": 0, "uid": 501, "gid": 20, "ownership": "same-user-group-preserved", "creation_time": "observed-not-restored"}


def fake_library():
    names = ["flistxattr", "fgetxattr", "fsetxattr", "fremovexattr", "acl_get_fd_np", "acl_size", "acl_copy_ext", "acl_copy_int", "acl_set_fd_np", "acl_free", "fchflags", "fpathconf"]
    library = SimpleNamespace(**{name: Mock(return_value=0) for name in names})
    library.fpathconf.return_value = 1
    return library


@pytest.mark.parametrize("change", [lambda d: d.pop("acl"), lambda d: d.update(schema=True),
    lambda d: d.update(backend="linux"), lambda d: d.update(flags=2), lambda d: d.update(uid=True),
    lambda d: d.update(gid=-1), lambda d: d.update(extra="unknown"), lambda d: d.update(acl="bad")])
def test_native_descriptor_unknown_is_not_empty(change):
    d = descriptor()
    change(d)
    with pytest.raises(dm.MetadataUnavailable):
        dm.validate_descriptor(d)


@pytest.mark.parametrize("raw", [b"", bytes(44), acl() + b"x", acl(1)[:-1],
    struct.pack(">I16s16sII", dm.ACL_MAGIC, bytes(16), bytes(16), 0xFFFFFFFF, 1),
    struct.pack(">I16s16sII", dm.ACL_MAGIC, bytes(16), bytes(16), 129, 0)])
def test_acl_malformed_framing_never_reaches_native_decoder(raw):
    library = fake_library()
    native = dm.DarwinMetadata(library)
    with pytest.raises(dm.MetadataUnavailable):
        native.set_acl(3, raw)
    library.acl_copy_int.assert_not_called()
    library.acl_set_fd_np.assert_not_called()


def test_pointer_signatures_and_allocation_freed_on_error():
    library = fake_library()
    pointer = 2**40 + 123
    library.acl_get_fd_np.return_value = pointer
    library.acl_size.return_value = dm.ACL_HEADER + dm.ACL_MAX_ENTRIES * dm.ACL_ENTRY + 1
    native = dm.DarwinMetadata(library)
    assert library.acl_get_fd_np.restype is C.c_void_p
    assert library.acl_copy_ext.argtypes == [C.c_void_p, C.c_void_p, C.c_ssize_t]
    with pytest.raises(dm.MetadataUnavailable, match="size"):
        native.read_acl(3)
    library.acl_free.assert_called_once_with(pointer)


def test_restore_failure_frees_acl_exactly_once():
    library = fake_library()
    library.acl_copy_int.return_value = 2**40 + 77
    library.acl_set_fd_np.return_value = -1
    native = dm.DarwinMetadata(library)
    native._acl_external = lambda _ptr: acl()
    C.set_errno(errno.EACCES)
    with pytest.raises(dm.MetadataUnavailable) as raised:
        native.set_acl(3, acl())
    assert raised.value.errno == errno.EACCES
    library.acl_free.assert_called_once_with(2**40 + 77)


def test_missing_symbol_is_unavailable():
    library = fake_library()
    del library.acl_copy_int
    with pytest.raises(dm.MetadataUnavailable, match="API"):
        dm.DarwinMetadata(library)


@pytest.mark.parametrize("error", [errno.EACCES, errno.ENOTSUP, errno.EIO])
def test_native_read_error_not_empty(error):
    library = fake_library()
    library.flistxattr.return_value = -1
    C.set_errno(error)
    with pytest.raises(dm.MetadataUnavailable) as raised:
        dm.DarwinMetadata(library).read_xattrs(3)
    assert raised.value.errno == error


def test_size_growth_retries_bounded_and_short_reads_refuse():
    native = dm.DarwinMetadata(fake_library())
    calls = []
    def growing(buffer, size):
        calls.append(size)
        if buffer is None:
            return 2
        C.set_errno(errno.ERANGE)
        return -1
    with pytest.raises(dm.MetadataUnavailable, match="changed"):
        native._sized(growing, 20)
    assert len(calls) == 6
    with pytest.raises(dm.MetadataUnavailable, match="changed"):
        native._sized(lambda buffer, size: 2 if buffer is None else 1, 20)
    with pytest.raises(dm.MetadataUnavailable, match="limit"):
        native._sized(lambda buffer, size: 21, 20)


@pytest.mark.parametrize("names", [b"no-terminator", b"a\0a\0", b"\0", b"\xff\0", b"x" * 128 + b"\0"])
def test_malformed_attribute_names_refused(names):
    native = dm.DarwinMetadata(fake_library())
    native._sized = lambda _call, _limit: names
    with pytest.raises(dm.MetadataUnavailable):
        native.read_xattrs(3)


@pytest.mark.parametrize("attrs", [{"a\0b": ""}, {"com.apple.system.Security": ""}, {"a": "bad"}, {"a": None}, {1: ""}])
def test_all_attributes_validated_before_any_mutation(attrs):
    library = fake_library()
    native = dm.DarwinMetadata(library)
    with pytest.raises(dm.MetadataUnavailable):
        native.set_xattrs(3, attrs)
    library.flistxattr.assert_not_called()
    library.fsetxattr.assert_not_called()
    library.fremovexattr.assert_not_called()


@pytest.mark.parametrize("flags", [2, 4, 0x20, 0x40000000, 0x100, None])
def test_flags_refuse_before_content_read(tmp_path, monkeypatch, flags):
    image = tmp_path / "image"
    image.write_bytes(b"original")
    native = dm.DarwinMetadata(fake_library())
    real = os.fstat
    def stat_with_flags(fd):
        st = real(fd)
        return SimpleNamespace(st_flags=flags, st_uid=os.geteuid(), st_gid=os.getegid(), st_mode=st.st_mode, st_birthtime=1)
    monkeypatch.setattr(dm.os, "fstat", stat_with_flags)
    with pytest.raises(dm.MetadataUnavailable, match="flags"):
        with image.open("rb") as handle:
            native.capture(handle.fileno())
    native.lib.acl_get_fd_np.assert_not_called()
    assert image.read_bytes() == b"original"


def test_private_acl_not_inferred_from_mode(tmp_path, monkeypatch):
    native = dm.DarwinMetadata(fake_library())
    monkeypatch.setattr(native, "check_stat", lambda _st: None)
    monkeypatch.setattr(native, "read_acl", lambda _fd: acl(1))
    folder = tmp_path / "private"
    folder.mkdir(mode=0o700)
    fd = os.open(folder, os.O_RDONLY)
    try:
        with pytest.raises(dm.MetadataUnavailable, match="nonempty ACL"):
            native.require_private(fd, 0o700)
    finally:
        os.close(fd)


NATIVE = pytest.mark.skipif(sys.platform != "darwin", reason="requires actual Darwin APIs; no native qualification on this OS")


def native_acl(native, inherited=False):
    uuid = C.create_string_buffer(16)
    function = native.lib.mbr_uid_to_uuid
    function.argtypes, function.restype = [C.c_uint32, C.c_void_p], C.c_int
    assert function(os.getuid(), uuid) == 0
    # Grant read to current owner; inheritance changes only owned fixtures.
    entry = uuid.raw + struct.pack(">II", 1 | (0x60 if inherited else 0), 2)
    return acl()[:36] + struct.pack(">II", 1, 0) + entry


@NATIVE
def test_native_sdk_contract(tmp_path):
    source = tmp_path / "metadata-abi.c"
    source.write_text(r'''
#include <stdio.h>
#include <stddef.h>
#include <sys/acl.h>
#include <sys/kauth.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <copyfile.h>
#include <unistd.h>
int main(void) {
 printf("%d %zu %zu %d %u %u %u %d %d\n", ACL_TYPE_EXTENDED,
   (size_t)KAUTH_FILESEC_SIZE(0), sizeof(struct kauth_ace), ACL_MAX_ENTRIES,
   KAUTH_FILESEC_MAGIC, UF_HIDDEN | UF_NODUMP, COPYFILE_XATTR, XATTR_MAXNAMELEN, _PC_EXTENDED_SECURITY_NP);
 return 0;
}
''')
    binary = tmp_path / "metadata-abi"
    subprocess.run(["/usr/bin/xcrun", "clang", "-Wall", "-Werror", str(source), "-o", str(binary)], check=True, capture_output=True, timeout=60)
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
    assert [int(n) for n in result.stdout.split()] == [dm.ACL_TYPE_EXTENDED, dm.ACL_HEADER, dm.ACL_ENTRY,
        dm.ACL_MAX_ENTRIES, dm.ACL_MAGIC, dm.ALLOWED_FLAGS, 4, 127, dm.PC_EXTENDED_SECURITY]


@NATIVE
def test_native_full_metadata_private_stage_and_restore(tmp_path):
    native = dm.backend()
    source = tmp_path / "owned-unicode-猫.jpg"
    source.write_bytes(b"synthetic image payload\x00" * 17)
    os.chmod(source, 0o640)
    attrs = {"org.imagesorter.empty": "", "org.imagesorter.猫": base64.b64encode(b"bytes\x00\xff").decode(),
             "com.apple.ResourceFork": base64.b64encode(b"owned synthetic resource fork").decode(),
             "com.apple.FinderInfo": base64.b64encode(b"JPEG" + bytes(28)).decode(),
             "com.apple.quarantine": base64.b64encode(b"0081;00000000;ImageSorterFixture;").decode()}
    with source.open("rb") as handle:
        native.set_xattrs(handle.fileno(), attrs)
        native.set_acl(handle.fileno(), native_acl(native))
        assert native.lib.fchflags(handle.fileno(), dm.ALLOWED_FLAGS) == 0
    original = safe.fingerprint(str(source))
    assert original["xattrs"] == attrs
    assert dm.validate_acl(base64.b64decode(original["native_metadata"]["acl"])) == 1
    private = Path(safe.private_directory(str(tmp_path), ".owned-stage-"))
    stage = private / "stage"
    staged = safe.secure_copy(str(source), str(stage), original)
    assert staged["mode"] == 0o600
    assert dm.validate_acl(base64.b64decode(staged["native_metadata"]["acl"])) in (-1, 0)
    restored = safe.apply_attributes(str(stage), original)
    for key in ("size", "sha256", "xattrs", "mode", "mtime_ns", "native_metadata"):
        assert restored[key] == original[key]
    assert safe.verify(str(source), original) == original
    receipt = {"status": "PASS_NATIVE_METADATA_FIXTURE_ONLY", "os": platform.system(), "release": platform.release(),
        "architecture": platform.machine(), "python": platform.python_version(),
        "fixture_fs_block_size": os.statvfs(tmp_path).f_bsize,
        "content_sha256": original["sha256"], "acl_sha256": hashlib.sha256(base64.b64decode(original["native_metadata"]["acl"])).hexdigest(),
        "xattr_names_count": len(attrs), "source_unchanged": True, "native_descriptor_equal": True,
        "creation_time": "observed-not-restored", "product_mutation_admission": "HELD", "physical_USB_power_loss": "NOT_RUN"}
    output = os.environ.get("IMAGESORTER_DARWIN_METADATA_RECEIPT")
    if output:
        Path(output).write_text(json.dumps(receipt, indent=2) + "\n")


@NATIVE
def test_native_inherited_acl_private_directory_refused(tmp_path):
    native = dm.backend()
    parent = tmp_path / "inherited-parent"
    parent.mkdir(mode=0o700)
    fd = os.open(parent, os.O_RDONLY)
    try:
        native.set_acl(fd, native_acl(native, inherited=True))
    finally:
        os.close(fd)
    with pytest.raises(safe.PrerequisiteError, match="ACL"):
        safe.private_directory(str(parent), ".owned-inherited-")
    assert len(list(parent.iterdir())) == 1  # inspectable failed empty directory retained


@NATIVE
def test_native_immutable_source_refused_before_read(tmp_path, monkeypatch):
    native = dm.backend()
    source = tmp_path / "immutable"
    source.write_bytes(b"owned immutable fixture")
    with source.open("rb") as handle:
        assert native.lib.fchflags(handle.fileno(), 2) == 0
        try:
            monkeypatch.setattr(safe.os, "read", Mock(side_effect=AssertionError("content read before flag refusal")))
            with pytest.raises(dm.MetadataUnavailable, match="flags"):
                safe.fingerprint(str(source))
            assert os.fstat(handle.fileno()).st_flags & 2
        finally:
            # Test owns this inode and clears only its own test-created flag.
            assert native.lib.fchflags(handle.fileno(), 0) == 0


@NATIVE
def test_native_restore_failure_retains_original_and_stage(tmp_path, monkeypatch):
    native = dm.backend()
    source = tmp_path / "original"
    source.write_bytes(b"owned original")
    before = safe.fingerprint(str(source))
    folder = Path(safe.private_directory(str(tmp_path), ".restore-failure-"))
    stage = folder / "stage"
    safe.secure_copy(str(source), str(stage), before)
    monkeypatch.setattr(native, "set_acl", Mock(side_effect=dm.MetadataUnavailable("injected ACL restore failure")))
    with pytest.raises(dm.MetadataUnavailable, match="injected"):
        safe.apply_attributes(str(stage), before)
    assert stage.read_bytes() == source.read_bytes() == b"owned original"
    assert safe.verify(str(source), before) == before


@NATIVE
def test_native_public_attribute_restore_is_not_enabled(tmp_path):
    source = tmp_path / "public" / "image"
    source.parent.mkdir(mode=0o755)
    source.write_bytes(b"unchanged")
    before = safe.fingerprint(str(source))
    with pytest.raises(dm.MetadataUnavailable, match="private"):
        safe.apply_attributes(str(source), before)
    assert safe.verify(str(source), before) == before


def test_acl_absence_is_distinct_and_uses_documented_removal_sentinel():
    library = fake_library()
    def absent(*_args):
        C.set_errno(errno.ENOENT)
    library.acl_get_fd_np.side_effect = absent
    native = dm.DarwinMetadata(library)
    assert native.read_acl(3) == dm.ACL_ABSENT
    assert dm.validate_acl(dm.ACL_ABSENT) == -1
    assert dm.validate_acl(acl()) == 0
    native.set_acl(3, dm.ACL_ABSENT)
    library.acl_set_fd_np.assert_called_once_with(3, 1, dm.ACL_TYPE_EXTENDED)
    library.acl_copy_int.assert_not_called()
    library.acl_free.assert_not_called()


@pytest.mark.parametrize("error", [0, errno.EACCES, errno.ENOTSUP, errno.EBADF])
def test_unknown_acl_observation_never_becomes_absence(error):
    library = fake_library()
    def failed(*_args):
        C.set_errno(error)
    library.acl_get_fd_np.side_effect = failed
    with pytest.raises(dm.MetadataUnavailable):
        dm.DarwinMetadata(library).read_acl(3)
    library.acl_free.assert_not_called()


@pytest.mark.parametrize("capability", [-1, 0, 2])
def test_unsupported_filesystem_security_never_becomes_empty_acl(capability):
    library = fake_library()
    library.fpathconf.return_value = capability
    with pytest.raises(dm.MetadataUnavailable, match="capability"):
        dm.DarwinMetadata(library).read_acl(3)
    library.acl_get_fd_np.assert_not_called()


@pytest.mark.parametrize("value", [None, {}, {"backend": "foreign"}])
def test_foreign_native_record_cannot_match_missing_field(monkeypatch, value):
    monkeypatch.setattr(safe, "fingerprint", lambda _path: {"size": 1, "sha256": "a"})
    with pytest.raises(safe.FileChangedError, match="Foreign"):
        safe.verify("synthetic", {"size": 1, "sha256": "a", "native_metadata": value})
    assert safe.verify("synthetic", {"size": 1, "sha256": "a"}) == {"size": 1, "sha256": "a"}

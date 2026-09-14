"""Strict runtime trust and builder-only compatibility normalization."""
import hashlib
import struct

import pytest

from scripts.appimage_format import (
    AppImageFormatError,
    normalize_runtime_digest,
    verify_runtime_prefix,
)


def runtime_bytes():
    names = b"\0.shstrtab\0.digest_md5\0"
    ident = b"\x7fELF\x02\x01\x01\0AI\x02" + b"\0" * 5
    # Three sections at 64; string data then the exact sixteen-byte MD5 field.
    strings, field = 256, 256 + len(names)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 62, 1, 0, 0, 64, 0, 64, 56, 0, 64, 3, 1)
    sections = struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    sections += struct.pack("<IIQQQQIIQQ", 1, 3, 0, 0, strings, len(names), 0, 0, 1, 0)
    sections += struct.pack("<IIQQQQIIQQ", 11, 1, 0, 0, field, 16, 0, 0, 1, 0)
    return header + sections + names + b"\0" * 16, field


@pytest.fixture
def inputs(tmp_path):
    data, field = runtime_bytes()
    runtime, artifact = tmp_path / "runtime", tmp_path / "owned.AppImage"
    runtime.write_bytes(data)
    artifact.write_bytes(data + b"hsqspayload")
    return runtime, artifact, hashlib.sha256(data).hexdigest(), field


def test_normalization_is_exact_idempotent_and_preserves_payload(inputs):
    runtime, artifact, sha, field = inputs
    data = bytearray(artifact.read_bytes())
    data[field:field + 16] = b"compatibilityMD5"
    artifact.write_bytes(data)
    with pytest.raises(AppImageFormatError, match="prefix differs"):
        verify_runtime_prefix(artifact, runtime, sha)
    receipt = normalize_runtime_digest(artifact, runtime, sha)
    assert receipt["normalized_md5_metadata"] is True
    assert receipt["digest_section"] == {"offset": field, "length": 16}
    assert artifact.read_bytes() == runtime.read_bytes() + b"hsqspayload"
    assert receipt["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert normalize_runtime_digest(artifact, runtime, sha)["normalized_md5_metadata"] is False
    assert verify_runtime_prefix(artifact, runtime, sha)["runtime_sha256"] == sha


@pytest.mark.parametrize("change", ["other-prefix-byte", "runtime-pin", "squashfs-boundary", "wrong-field-length", "runtime-tail"])
def test_normalizer_rejects_before_writing_any_unapproved_bytes(inputs, change):
    runtime, artifact, sha, field = inputs
    data = bytearray(artifact.read_bytes())
    data[field:field + 16] = b"compatibilityMD5"
    if change == "other-prefix-byte":
        data[24] ^= 1
    elif change == "runtime-pin":
        sha = "0" * 64
    elif change == "squashfs-boundary":
        data[len(runtime.read_bytes())] = 0
    elif change == "wrong-field-length":
        trusted = bytearray(runtime.read_bytes())
        struct.pack_into("<Q", trusted, 64 + 2 * 64 + 32, 15)
        runtime.write_bytes(trusted)
        sha = hashlib.sha256(trusted).hexdigest()
    else:
        runtime.write_bytes(runtime.read_bytes() + b"unapproved tail")
        sha = hashlib.sha256(runtime.read_bytes()).hexdigest()
    artifact.write_bytes(data)
    original = artifact.read_bytes()
    with pytest.raises(AppImageFormatError):
        normalize_runtime_digest(artifact, runtime, sha)
    assert artifact.read_bytes() == original


def test_normalizer_refuses_symlink_output(inputs, tmp_path):
    runtime, artifact, sha, _field = inputs
    link = tmp_path / "link.AppImage"
    link.symlink_to(artifact)
    original = artifact.read_bytes()
    with pytest.raises(OSError):
        normalize_runtime_digest(link, runtime, sha)
    assert artifact.read_bytes() == original

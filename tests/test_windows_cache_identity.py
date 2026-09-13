"""Exact Windows handle identities without losing pathname or cache guards."""
import os
import stat
from types import SimpleNamespace

import pytest
from PIL import Image

from imagesorter import image_identity


@pytest.fixture
def windows_identity_os(monkeypatch):
    """Expose Windows' different pathname/handle ctime meanings locally."""
    api = SimpleNamespace(**vars(os))
    api.name = "nt"
    opened = []

    def path_stat(path, *, follow_symlinks):
        assert follow_symlinks is False
        info = os.stat(path, follow_symlinks=False)
        values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
        values["st_ctime_ns"] = 946684800000000000  # Pathname creation time.
        return SimpleNamespace(**values)

    def readonly_open(path, flags):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        descriptor = os.open(path, flags)
        opened.append(descriptor)
        return descriptor

    api.stat = path_stat
    api.open = readonly_open
    monkeypatch.setattr(image_identity, "os", api)
    yield api
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def _changed_stat(info, field):
    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    values[field] += 1  # Even one nanosecond of real identity drift must matter.
    return SimpleNamespace(**values)


def test_windows_cache_identity_uses_full_reader_handle_identity(tmp_path, qapp, windows_identity_os):
    from imagesorter.reader_process import run_reader

    source = tmp_path / "reader-identity.bmp"
    Image.new("RGB", (4, 3), "red").save(source)
    before = source.read_bytes(), source.stat().st_mode
    metadata, pixels = run_reader({"action": "decode", "request_id": "ctime-contract", "filepath": str(source)})
    key = image_identity.cache_key(source)
    assert len(key.source) == 5
    assert key.source[4] != windows_identity_os.stat(source, follow_symlinks=False).st_ctime_ns
    assert image_identity.matches_metadata(key, metadata), (key, metadata)
    assert pixels == bytes((255, 0, 0, 255)) * 12
    assert (source.read_bytes(), source.stat().st_mode) == before


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"])
def test_windows_cache_invalidates_every_handle_identity_field(tmp_path, windows_identity_os, field):
    source = tmp_path / "image.bmp"
    Image.new("RGB", (4, 3), "red").save(source)
    key = image_identity.cache_key(source)
    native_fstat = windows_identity_os.fstat
    native_stat = windows_identity_os.stat
    windows_identity_os.fstat = lambda descriptor: _changed_stat(native_fstat(descriptor), field)
    if field != "st_ctime_ns":
        windows_identity_os.stat = lambda path, **kwargs: _changed_stat(native_stat(path, **kwargs), field)
    assert image_identity.cache_key(source) != key
    metadata = {"source_identity": image_identity.source_identity(source), "decoder_identity": key.decoder,
                "frame": key.frame, "target_size": key.preview}
    assert not image_identity.matches_metadata(key, metadata)


@pytest.mark.parametrize(("boundary", "field"), [
    ("path", "st_dev"), ("path", "st_ino"), ("path", "st_size"),
    ("path", "st_mtime_ns"), ("path", "st_ctime_ns"),
    ("handle", "st_dev"), ("handle", "st_ino"), ("handle", "st_size"),
    ("handle", "st_mtime_ns"), ("handle", "st_ctime_ns"),
    ("cross-api", "st_dev"), ("cross-api", "st_ino"), ("cross-api", "st_size"), ("cross-api", "st_mtime_ns"),
])
def test_windows_identity_rejects_lookup_races(tmp_path, windows_identity_os, boundary, field):
    source = tmp_path / "original.bmp"
    source.write_bytes(b"preserved original bytes")
    before = source.read_bytes(), source.stat().st_mode
    native_stat, native_fstat = windows_identity_os.stat, windows_identity_os.fstat
    calls = {"path": 0, "handle": 0}

    def path_stat(path, **kwargs):
        calls["path"] += 1
        info = native_stat(path, **kwargs)
        return _changed_stat(info, field) if boundary == "path" and calls["path"] == 2 else info

    def handle_stat(descriptor):
        calls["handle"] += 1
        info = native_fstat(descriptor)
        changed = boundary == "cross-api" or boundary == "handle" and calls["handle"] == 2
        return _changed_stat(info, field) if changed else info

    windows_identity_os.stat, windows_identity_os.fstat = path_stat, handle_stat
    with pytest.raises(ValueError, match="changed during cache identity"):
        image_identity.source_identity(source)
    assert (source.read_bytes(), source.stat().st_mode) == before


@pytest.mark.parametrize("failure", ["fstat", "second-path-stat"])
def test_windows_identity_closes_descriptor_on_lookup_failure(tmp_path, windows_identity_os, failure):
    source = tmp_path / "original.bmp"
    source.write_bytes(b"preserved original bytes")
    native_stat = windows_identity_os.stat
    calls = []

    def path_stat(path, **kwargs):
        calls.append(path)
        if failure == "second-path-stat" and len(calls) == 2:
            raise OSError("injected disappearance")
        return native_stat(path, **kwargs)

    def failed_fstat(_descriptor):
        raise OSError("injected handle failure")

    windows_identity_os.stat = path_stat
    if failure == "fstat":
        windows_identity_os.fstat = failed_fstat
    with pytest.raises(OSError, match="injected"):
        image_identity.source_identity(source)
    assert source.read_bytes() == b"preserved original bytes"


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600])
def test_windows_identity_rejects_nonregular_path_before_open(tmp_path, windows_identity_os, mode):
    source = tmp_path / "input.bmp"
    source.write_bytes(b"unchanged")
    native_stat = windows_identity_os.stat

    def path_stat(path, **kwargs):
        info = native_stat(path, **kwargs)
        info.st_mode = mode
        return info

    def forbidden_open(*_args):
        raise AssertionError("Do not open a symlink or nonregular input")

    windows_identity_os.stat, windows_identity_os.open = path_stat, forbidden_open
    with pytest.raises(ValueError, match="regular non-symlink"):
        image_identity.source_identity(source)


def test_posix_identity_keeps_path_stat_contract_without_open(tmp_path, monkeypatch):
    source = tmp_path / "original.bmp"
    source.write_bytes(b"unchanged")
    api = SimpleNamespace(**vars(os))
    api.name = "posix"

    def forbidden_open(*_args):
        raise AssertionError("POSIX identity must retain its original path-stat contract")

    api.open = forbidden_open
    monkeypatch.setattr(image_identity, "os", api)
    assert image_identity.source_identity(source) == image_identity.stat_identity(os.stat(source, follow_symlinks=False))


def test_windows_identity_rejects_real_same_byte_replacement_before_open(tmp_path, windows_identity_os):
    source, replacement = tmp_path / "source.bmp", tmp_path / "replacement.bmp"
    source.write_bytes(b"same preserved bytes")
    replacement.write_bytes(source.read_bytes())
    before = source.stat()
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    native_open = windows_identity_os.open

    def replaced_open(path, flags):
        # Replace after pathname admission evidence, before a handle is opened;
        # bytes/size/mtime match, so object identity must reject this race.
        os.replace(replacement, source)
        return native_open(path, flags)

    windows_identity_os.open = replaced_open
    with pytest.raises(ValueError, match="changed during cache identity"):
        image_identity.source_identity(source)
    assert source.read_bytes() == b"same preserved bytes"
    assert source.stat().st_ino != before.st_ino

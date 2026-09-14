"""POSIX pathname sockets stay short and private for arbitrarily long profiles."""
from __future__ import annotations

import hashlib
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from imagesorter import worker_protocol as protocol

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Pathname mutation sockets are a POSIX capability")


@pytest.fixture
def socket_base(monkeypatch):
    # Production validates root-owned /tmp; each socket test instead owns a
    # distinct short private base and never touches a running user's endpoints.
    with tempfile.TemporaryDirectory(prefix="is-sock-", dir="/tmp") as directory:
        base = Path(directory).resolve()
        monkeypatch.setattr(protocol, "_checked_socket_base", lambda: base)
        monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform="darwin", executable=sys.executable))
        yield base


def test_linux_abstract_identity_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(protocol, "_private_socket_directory", lambda: pytest.fail("Linux must not create socket files"))
    journal = tmp_path / "journal.db"
    digest = hashlib.sha256(os.path.realpath(journal).encode()).hexdigest()[:32]
    assert protocol.service_address(str(journal)) == f"\0imagesorter-{os.getuid()}-{digest}"


def test_system_socket_base_resolves_legitimate_symlink_and_rejects_unsafe_base(tmp_path, monkeypatch):
    system = Path("/tmp").resolve()
    assert protocol._checked_socket_base() == system
    link = tmp_path / "system-tmp"
    link.symlink_to(system, target_is_directory=True)
    assert protocol._checked_socket_base(link) == system
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    with pytest.raises(PermissionError, match="system-owned sticky"):
        protocol._checked_socket_base(unsafe)
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o777, st_uid=0) if path == unsafe else real_lstat(path))
    with pytest.raises(PermissionError, match="system-owned sticky"):
        protocol._checked_socket_base(unsafe)


def test_long_native_profile_uses_real_short_private_socket(socket_base, tmp_path):
    journal = tmp_path / ("long-profile-" + "x" * 150) / "data" / "ImageSorter" / "journal.db"
    assert len(os.fsencode(journal.parent / "mutation.sock")) > 103
    address = protocol.service_address(str(journal))
    assert len(os.fsencode(address)) <= 103
    assert Path(address).parent == socket_base / f"imagesorter-sockets-{os.getuid()}"
    assert stat.S_IMODE(Path(address).parent.stat().st_mode) == 0o700
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(address)
        server.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(address)
            peer, _ = server.accept()
            with peer:
                client.sendall(b"identity-preserved")
                assert peer.recv(64) == b"identity-preserved"
    assert not journal.parent.exists()


def test_socket_namespace_preserves_existing_temporary_fallback_state(socket_base, tmp_path):
    fallback = socket_base / f"imagesorter-{os.getuid()}"
    fallback.mkdir(mode=0o755)
    settings = fallback / "settings.json"
    settings.write_bytes(b"existing fallback settings")
    address = protocol.service_address(str(tmp_path / "journal.db"))
    assert Path(address).parent == socket_base / f"imagesorter-sockets-{os.getuid()}"
    assert settings.read_bytes() == b"existing fallback settings"
    assert stat.S_IMODE(fallback.stat().st_mode) == 0o755


def test_native_socket_digest_uses_canonical_journal_identity(socket_base, tmp_path):
    directory = tmp_path / "real-profile"
    directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    journal = directory / "journal.db"
    address = protocol.service_address(str(journal))
    expected_digest = hashlib.sha256(os.path.realpath(journal).encode()).hexdigest()[:32]
    assert Path(address).name == expected_digest + ".sock"
    assert protocol.service_address(str(alias / "journal.db")) == address
    assert protocol.service_address(str(directory / "other.db")) != address


@pytest.mark.parametrize("kind", ["symlink", "regular-file", "foreign-owner", "public-mode", "owner-not-searchable"])
def test_unsafe_private_socket_directory_is_preserved_and_refused(socket_base, tmp_path, monkeypatch, kind):
    directory = socket_base / f"imagesorter-sockets-{os.getuid()}"
    target = tmp_path / "unrelated"
    target.mkdir()
    valuable = target / "notes"
    valuable.write_bytes(b"preserve these bytes")
    if kind == "symlink":
        directory.symlink_to(target, target_is_directory=True)
    elif kind == "regular-file":
        directory.write_bytes(b"unrelated existing file")
    else:
        directory.mkdir(mode=0o700)
        if kind == "foreign-owner":
            real_lstat = Path.lstat
            actual = directory.lstat()
            monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(
                st_mode=actual.st_mode, st_uid=os.getuid() + 1) if path == directory else real_lstat(path))
        else:
            directory.chmod(0o755 if kind == "public-mode" else 0o600)
    with pytest.raises(PermissionError, match="owned by the current user"):
        protocol.service_address(str(tmp_path / "journal.db"))
    assert valuable.read_bytes() == b"preserve these bytes"
    if kind == "symlink":
        assert directory.is_symlink()
    elif kind == "regular-file":
        assert directory.read_bytes() == b"unrelated existing file"
    else:
        assert directory.is_dir()
        if kind == "owner-not-searchable":
            directory.chmod(0o700)  # Only this temporary fixture needs cleanup.


def test_excessive_socket_base_is_refused_before_bind(socket_base, tmp_path, monkeypatch):
    long_base = tmp_path / ("long-socket-base-" + "b" * 130)
    long_base.mkdir()
    monkeypatch.setattr(protocol, "_checked_socket_base", lambda: long_base)
    with pytest.raises(ValueError, match="pathname limit"):
        protocol.service_address(str(tmp_path / "journal.db"))
    assert not list(long_base.rglob("*.sock"))


def test_real_mutation_service_roundtrip_uses_short_socket_for_long_profile(socket_base, tmp_path):
    profile = tmp_path / ("long-profile-" + "p" * 140) / "data" / "ImageSorter"
    profile.mkdir(parents=True)
    journal = profile / "operation_journal.db"
    source = tmp_path / "photo.jpg"
    sidecar = tmp_path / "photo.jpg.txt"
    source.write_bytes(b"original image bytes")
    sidecar.write_bytes(b"original sidecar bytes")
    destination = tmp_path / "sorted"
    destination.mkdir()
    address = protocol.service_address(str(journal))
    assert len(os.fsencode(profile / "mutation.sock")) > 103
    program = (
        "import sys; from pathlib import Path; from types import SimpleNamespace; "
        "from imagesorter import worker_protocol as protocol, mutation_service; "
        "protocol.sys = SimpleNamespace(platform='darwin', executable=sys.executable); "
        "protocol._checked_socket_base = lambda: Path(sys.argv[1]); "
        "raise SystemExit(mutation_service.main(['--journal', sys.argv[2]]))"
    )
    log = tmp_path / "service.log"
    with log.open("wb") as output:
        process = subprocess.Popen([sys.executable, "-c", program, str(socket_base), str(journal)],
                                   env=protocol.worker_environment(), stdout=output, stderr=output)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(10)
    stream = None
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                connection.connect(address)
                break
            except OSError:
                assert process.poll() is None, log.read_text(errors="replace")
                assert time.monotonic() < deadline, log.read_text(errors="replace")
                time.sleep(0.01)
        stream = connection.makefile("rb")
        connection.sendall(protocol.encode({"type": "hello"}))
        def submit(request):
            connection.sendall(protocol.encode({"type": "submit", "request": request}))
            accepted = False
            while True:
                row = protocol.decode(stream.readline(protocol.MAX_MESSAGE_BYTES + 2).strip())
                assert row.get("type") != "error", row
                if row.get("type") == "accepted" and row.get("operation_id") == request["operation_id"]:
                    accepted = True
                if row.get("type") == "result" and row["result"]["operation_id"] == request["operation_id"]:
                    assert accepted
                    return row["result"]
        moved = submit({"operation_id": "native-move", "action": "move", "source_path": str(source),
                        "destination_path": str(destination)})
        assert moved["state"] == "completed", moved
        current = Path(moved["destination_path"])
        assert current.read_bytes() == b"original image bytes"
        assert Path(str(current) + ".txt").read_bytes() == b"original sidecar bytes"
        assert not source.exists() and not sidecar.exists()
        restored = submit({"operation_id": "native-undo", "action": "undo_move", "source_path": str(current),
                           "undo_token": moved["undo_token"]})
        assert restored["state"] == "completed", restored
        assert source.read_bytes() == b"original image bytes"
        assert sidecar.read_bytes() == b"original sidecar bytes"
        # Journal-owned recovery material remains for conservative retention.
        assert not current.exists() and not Path(str(current) + ".txt").exists()
    finally:
        if stream:
            stream.close()
        connection.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()  # Test-owned service handles only these temporary files.
            process.wait(timeout=5)
    assert process.returncode == 0, log.read_text(errors="replace")
    assert journal.is_file()

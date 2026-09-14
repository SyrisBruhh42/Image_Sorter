"""Standalone evidence recipes hash exact bytes without Python 3.11 APIs."""
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = {
    "acquire_distro_sources": "sha",
    "collect_runtime_licenses": "digest",
    "native_drain_probe": "digest",
    "build_heif_decoder": "sha",
    "lock_build_inputs": "sha256_file",
}


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        f"hash_test_{name}", Path(__file__).parents[1] / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=SCRIPTS)
def recipe(request):
    module = load_script(request.param)
    return module, getattr(module, SCRIPTS[request.param])


@pytest.mark.parametrize(("data", "expected"), [
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (bytes(range(256)) * 8192 + b"tail", hashlib.sha256(bytes(range(256)) * 8192 + b"tail").hexdigest()),
])
def test_recipe_hashes_exact_bytes_across_chunk_boundaries(recipe, tmp_path, data, expected):
    _, digest = recipe
    path = tmp_path / "input"
    path.write_bytes(data)
    assert digest(path) == expected
    assert path.read_bytes() == data


def test_recipe_hashes_bound_memory_and_close_input(recipe, monkeypatch):
    module, digest = recipe

    class BoundedInput(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)

    data = b"\x00\xff" * (1024 * 1024) + b"final"
    stream = BoundedInput(data)

    def open_input(mode):
        assert mode == "rb"
        return stream

    monkeypatch.setattr(module, "Path", lambda path: SimpleNamespace(open=open_input))
    assert digest("synthetic") == hashlib.sha256(data).hexdigest()
    assert stream.closed


def test_recipe_read_error_never_returns_partial_digest_and_closes_input(recipe, monkeypatch):
    module, digest = recipe

    class BrokenInput(io.BytesIO):
        calls = 0

        def read(self, size=-1):
            self.calls += 1
            if self.calls == 2:
                raise OSError("source read failed")
            return b"partial"

    stream = BrokenInput()
    monkeypatch.setattr(module, "Path", lambda path: SimpleNamespace(open=lambda mode: stream))
    with pytest.raises(OSError, match="source read failed"):
        digest("synthetic")
    assert stream.closed


def test_lock_records_the_downloaded_wheel_bytes(tmp_path, monkeypatch):
    module = load_script("lock_build_inputs")
    output = tmp_path / "lock"
    wheel_name = "example-1.2-py3-none-any.whl"
    monkeypatch.setattr(module.sys, "argv", ["lock_build_inputs.py", "--output", str(output)])
    monkeypatch.setattr(module.importlib.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "example"}, version="1.2"),
    ])

    def local_download(args, **kwargs):
        assert args[1:4] == ["-m", "pip", "download"]
        assert args[-1] == "example==1.2"
        with zipfile.ZipFile(output / "wheels" / wheel_name, "w") as archive:
            archive.writestr("example-1.2.dist-info/METADATA", "Name: example\nVersion: 1.2\n")

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=local_download, STDOUT=module.subprocess.STDOUT))
    module.main()
    data = (output / "wheels" / wheel_name).read_bytes()
    expected = hashlib.sha256(data).hexdigest()
    manifest = json.loads((output / "build-inputs.json").read_text())
    assert manifest["wheels"] == [{"name": "example", "version": "1.2", "file": wheel_name,
                                   "sha256": expected, "size": len(data)}]
    assert (output / "requirements.lock").read_text() == f"example==1.2 --hash=sha256:{expected}\n"

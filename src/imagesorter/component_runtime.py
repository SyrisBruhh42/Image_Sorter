"""Validated process boundary for installed codecs and inference providers."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .component_manager import (
    ComponentError,
    ComponentManager,
    clean_helper_environment,
)
from .model_assets import LABELS_SHA256, MODEL_SHA256
from .reader_sandbox import ReaderIsolationError, validate_reader_isolation

MAX_HEADER = 65536
MAX_PAYLOAD = 500 * 1024 * 1024
MAX_INPUT = 512 * 1024 * 1024
MAX_BATCH_FRAMES = 8
MAX_BATCH_BYTES = 64 * 1024 * 1024


def _validate_frame(metadata: dict, payload: bytes, expected_frame: int, *, inspect_only: bool = False) -> None:
    width, height, stride = (metadata.get(field) for field in ("width", "height", "stride"))
    if (not all(type(n) is int and n > 0 for n in (width, height, stride)) or
            width * height > 125000000 or stride != width * 4 or
            len(payload) != (0 if inspect_only else stride * height) or
            metadata.get("pixel_format") != "RGBA"):
        raise ComponentError("Invalid decoded image dimensions")
    if not inspect_only and hashlib.sha256(payload).hexdigest() != metadata.get("payload_sha256"):
        raise ComponentError("Decoded payload integrity failed")
    frame, count = metadata.get("frame"), metadata.get("frame_count")
    duration, loop = metadata.get("duration_ms"), metadata.get("loop_count")
    total_plays = metadata.get("total_plays")
    original = metadata.get("original_size")
    if (type(frame) is not int or type(count) is not int or not 0 <= frame < count <= 100000 or
            frame != expected_frame or type(duration) is not int or not 0 <= duration <= 3600000 or
            (loop is not None and (type(loop) is not int or not 0 <= loop <= 4294967295)) or
            (total_plays is not None and (type(total_plays) is not int or not 0 <= total_plays <= 4294967296)) or
            not isinstance(original, list) or len(original) != 2 or
            any(type(n) is not int or n <= 0 for n in original) or original[0] * original[1] > 125000000):
        raise ComponentError("Invalid frame identity or timing metadata")


def _validate_batch(metadata: dict, payload: bytes, requested: list[int]) -> None:
    frames = metadata.get("frames")
    if (not isinstance(frames, list) or len(frames) != len(requested) or
            not 1 <= len(frames) <= MAX_BATCH_FRAMES or len(payload) > MAX_BATCH_BYTES):
        raise ComponentError("Invalid frame batch count or allocation")
    offset, common = 0, None
    for expected, frame in zip(requested, frames, strict=True):
        if not isinstance(frame, dict):
            raise ComponentError("Invalid frame batch entry")
        length = frame.get("payload_length")
        if (type(length) is not int or length <= 0 or type(frame.get("payload_offset")) is not int or
                frame["payload_offset"] != offset or offset + length > len(payload)):
            raise ComponentError("Invalid or overlapping frame batch offsets")
        _validate_frame(frame, payload[offset:offset + length], expected)
        # TIFF pages can legitimately have different dimensions; each page has
        # its own bounded geometry, while the container count/loop stay stable.
        identity = (frame["frame_count"], frame["loop_count"], frame.get("total_plays"))
        if common is not None and identity != common:
            raise ComponentError("Inconsistent frame batch source metadata")
        common = identity
        offset += length
    if offset != len(payload):
        raise ComponentError("Unaccounted frame batch payload")


def component_job_command(arguments: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--component-job", *arguments]
    return [sys.executable, "-m", "imagesorter.component_jobs", *arguments]


def _policy_request(descriptor: dict) -> dict:
    return {name: descriptor[name] for name in ("reader_policy_version", "min_landlock_abi")
            if descriptor.get(name) is not None}


def _snapshot(source: Path, directory: Path) -> Path:
    if source.is_symlink() or not source.is_file():
        raise ComponentError("Capability input must be a regular file, not a symlink")
    target = directory / ("input" + source.suffix)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(source, flags)
    with os.fdopen(descriptor, "rb") as input_file:
        before = os.fstat(input_file.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ComponentError("Input was replaced with a non-regular file")
        if before.st_size > MAX_INPUT:
            raise ComponentError("Input exceeds the read-only snapshot limit")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            copied = 0
            for block in iter(lambda: input_file.read(1024 * 1024), b""):
                copied += len(block)
                if copied > MAX_INPUT:
                    raise ComponentError("Growing input exceeds snapshot limit")
                output.write(block)
            output.flush()
            os.fchmod(output.fileno(), 0o400)
        after = os.fstat(input_file.fileno())
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(after) or target.stat().st_size != before.st_size:
        raise ComponentError("Input changed while its read-only snapshot was created")
    return target


def _run(command: list[str], request: dict, *, cwd: Path, timeout: float) -> tuple[dict, bytes]:
    request_id = request.setdefault("request_id", uuid.uuid4().hex)
    request.setdefault("protocol_version", 1)
    encoded = json.dumps(request).encode() + b"\n"
    if len(encoded) > MAX_HEADER:
        raise ComponentError("Helper request exceeds header limit")
    payload_limit = MAX_BATCH_BYTES if request.get("action") == "decode_frames" else MAX_PAYLOAD
    with tempfile.TemporaryFile(dir=cwd) as output, tempfile.TemporaryFile(dir=cwd) as error:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=error,
                                   cwd=cwd, env=clean_helper_environment())
        try:
            deadline = time.monotonic() + timeout
            first = True
            while True:
                if (os.fstat(output.fileno()).st_size > payload_limit + MAX_HEADER or
                        os.fstat(error.fileno()).st_size > 1024 * 1024):
                    raise ComponentError("Helper output exceeds its bounded channel")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ComponentError("Optional helper exceeded its operation deadline")
                try:
                    process.communicate(encoded if first else None, timeout=min(.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    first = False
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            raise
        size = output.tell()
        if os.fstat(error.fileno()).st_size > 1024 * 1024:
            raise ComponentError("Helper output exceeds its bounded error channel")
        if hasattr(signal, "SIGALRM") and process.returncode == -signal.SIGALRM:
            raise ComponentError("Reader exceeded its bounded decode, model-load or inference deadline")
        if size > payload_limit + MAX_HEADER:
            raise ComponentError("Helper response exceeds payload bound")
        output.seek(0)
        header = output.readline(MAX_HEADER + 1)
        try:
            if len(header) > MAX_HEADER or not header.endswith(b"\n"):
                raise ValueError("Invalid helper header")
            metadata = json.loads(header)
            if not isinstance(metadata, dict):
                raise ValueError("Helper header must be an object")
        except ValueError as exc:
            error.seek(0)
            detail = error.read(2000).decode(errors="replace")
            outcome = f"exit status {process.returncode}"
            if process.returncode < 0:
                try:
                    outcome = f"terminated by {signal.Signals(-process.returncode).name}"
                except ValueError:
                    outcome = f"terminated by signal {-process.returncode}"
            raise ComponentError(f"Invalid helper response ({outcome}): {detail or 'no valid response was returned'}") from exc
        if (type(metadata.get("protocol_version")) is not int or metadata.get("protocol_version") != 1 or
                metadata.get("request_id") != request_id or
                metadata.get("component_id") != request["component_id"] or
                metadata.get("component_version") != request.get("component_version", "base") or
                type(metadata.get("generation", 0)) is not int or
                metadata.get("generation", 0) != request.get("generation", 0)):
            raise ComponentError("Helper result identity mismatch")
        if process.returncode or metadata.get("ok") is not True:
            raise ComponentError(metadata.get("error", "Optional helper failed"))
        if request.get("reader_policy_version"):
            try:
                validate_reader_isolation(metadata, policy_version=request["reader_policy_version"],
                                          minimum_abi=request["min_landlock_abi"])
            except ReaderIsolationError as exc:
                raise ComponentError(str(exc)) from exc
        length = metadata.get("payload_length")
        if type(length) is not int or not 0 <= length <= payload_limit:
            raise ComponentError("Invalid helper payload size")
        payload = output.read(length + 1)
        if len(payload) != length:
            raise ComponentError("Truncated or oversized helper payload")
        if request.get("action") in {"decode_frame", "inspect"}:
            _validate_frame(metadata, payload, request.get("frame", 0), inspect_only=request["action"] == "inspect")
        elif request.get("action") == "decode_frames":
            _validate_batch(metadata, payload, request["frames"])
        elif length:
            raise ComponentError("Unexpected payload for non-decoding request")
        return metadata, payload


def decode_component(filepath: str, component_id: str, *, frame: int = 0,
                     target_size: tuple[int, int] | None = None,
                     generation: int = 0, manager: ComponentManager | None = None) -> tuple[bytes, dict]:
    manager = manager or ComponentManager()
    with manager.acquire(component_id) as (installed, descriptor):
        with tempfile.TemporaryDirectory(prefix=".read-", dir=manager.root) as directory:
            work = Path(directory)
            snapshot = _snapshot(Path(filepath), work)
            metadata, payload = _run([str(installed / descriptor["entrypoint"])],
                {"component_id": component_id, "action": "decode_frame", "input_path": str(snapshot),
                 "component_version": descriptor["version"],
                 **_policy_request(descriptor),
                 "frame": frame, "target_size": target_size, "generation": generation}, cwd=work, timeout=30)
            metadata.update(component_version=descriptor["version"], component_sha256=descriptor["sha256"])
            return payload, metadata


def decode_frames_component(filepath: str, component_id: str, frames: list[int], *,
                            target_size: tuple[int, int] | None = None, generation: int = 0,
                            manager: ComponentManager | None = None) -> list[tuple[bytes, dict]]:
    if (not isinstance(frames, list) or not 1 <= len(frames) <= MAX_BATCH_FRAMES or
            any(type(frame) is not int or not 0 <= frame < 100000 for frame in frames) or
            len(set(frames)) != len(frames)):
        raise ComponentError("A batch requires 1 to 8 unique frame indices")
    manager = manager or ComponentManager()
    with manager.acquire(component_id) as (installed, descriptor):
        with tempfile.TemporaryDirectory(prefix=".read-", dir=manager.root) as directory:
            work = Path(directory)
            snapshot = _snapshot(Path(filepath), work)
            metadata, payload = _run([str(installed / descriptor["entrypoint"])],
                {"component_id": component_id, "action": "decode_frames", "input_path": str(snapshot),
                 "component_version": descriptor["version"], "frames": frames,
                 **_policy_request(descriptor),
                 "target_size": target_size, "generation": generation}, cwd=work, timeout=30)
            result = []
            for item in metadata["frames"]:
                offset, length = item["payload_offset"], item["payload_length"]
                identity = {key: metadata[key] for key in ("protocol_version", "request_id", "generation",
                            "component_id", "component_version", "ok")}
                if "reader_isolation" in metadata:
                    identity["reader_isolation"] = metadata["reader_isolation"]
                result.append((payload[offset:offset + length], dict(item, **identity,
                    component_sha256=descriptor["sha256"])))
            return result


def infer_component(filepath: str, model_dir: str, *, hardware: bool = True,
                    threshold: float = .5, manager: ComponentManager | None = None) -> dict:
    manager = manager or ComponentManager()
    requested_model = Path(model_dir).absolute()
    resolved_model = requested_model.resolve()
    if requested_model.is_relative_to(manager.root) and requested_model != resolved_model:
        raise ComponentError("Managed model selection may not traverse a symbolic link")
    request = {"action": "infer", "model_dir": str(resolved_model),
               "model_sha256": MODEL_SHA256, "labels_sha256": LABELS_SHA256,
               "threshold": threshold}
    with contextlib.ExitStack() as leases, tempfile.TemporaryDirectory(prefix=".infer-", dir=manager.root) as directory:
        model_version = "explicit-verified"
        if resolved_model.is_relative_to(manager.root):
            resolved_model, model_descriptor = leases.enter_context(manager.acquire_path("ai.mobilenet-v2", resolved_model))
            model_version = model_descriptor["version"]
        work = Path(directory)
        request["input_path"] = str(_snapshot(Path(filepath), work))
        # Hashing and loading refer to these private immutable snapshots, never a
        # mutable caller-supplied pathname between validation and model loading.
        model_snapshot = work / "model"
        model_snapshot.mkdir(mode=0o700)
        for name in ("mobilenetv2.onnx", "labels.txt"):
            snapshot = _snapshot(resolved_model / name, model_snapshot)
            snapshot.rename(model_snapshot / name)
        request["model_dir"] = str(model_snapshot)
        fallback = None
        if hardware and manager.active_path("provider.onnx-nvidia") is not None:
            try:
                with manager.acquire("provider.onnx-nvidia") as (installed, descriptor):
                    gpu_request = dict(request, component_id="provider.onnx-nvidia", component_version=descriptor["version"],
                                       **_policy_request(descriptor))
                    metadata, _ = _run([str(installed / descriptor["entrypoint"])], gpu_request,
                                       cwd=work, timeout=90)
                    metadata.update(component_version=descriptor["version"], component_sha256=descriptor["sha256"])
                    return _inference_receipt(metadata, resolved_model, manager, None, model_version=model_version)
            except (ComponentError, OSError) as exc:
                fallback = str(exc)
        elif hardware:
            fallback = "NVIDIA component is not enabled"
        cpu_request = dict(request, component_id="core.cpu", component_version="base")
        if sys.platform.startswith("linux"):
            cpu_request.update(reader_policy_version=1, min_landlock_abi=3)
        result, _ = _run(component_job_command(["helper", "--component-id", "core.cpu"]),
                         cpu_request, cwd=work, timeout=90)
        result["component_version"] = "base"
        return _inference_receipt(result, resolved_model, manager, fallback, model_version=model_version)


def _inference_receipt(result: dict, model: Path, manager: ComponentManager,
                       fallback: str | None, *, model_version: str | None = None) -> dict:
    scores = result.get("tags")
    if (not isinstance(scores, list) or len(scores) > 10 or
            any(not isinstance(item, dict) or not isinstance(item.get("tag"), str) or
                len(item["tag"]) > 1000 or type(item.get("score")) not in (int, float) or
                not 0 <= item["score"] <= 1 for item in scores)):
        raise ComponentError("Invalid inference labels or scores")
    active = manager.active_path("ai.mobilenet-v2")
    result.update(tags=[item["tag"] for item in scores], tag_scores=scores,
                  fallback_reason=fallback, model_sha256=MODEL_SHA256,
                  labels_sha256=LABELS_SHA256,
                  model_component_version=(model_version or (active.name if active and active.resolve() == model
                                           else "explicit-verified")))
    return result

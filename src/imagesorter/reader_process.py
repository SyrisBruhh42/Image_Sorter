"""Bounded subprocess execution for native readers and optional capabilities."""
from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .worker_protocol import decode, encode, worker_command, worker_environment


class ReaderCancelled(RuntimeError):
    pass


def read_pixel_output(path: Path, limit: int) -> bytes:
    """Never let a leaf's pathname turn its supervisor into a blocking reader."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return b""
    with os.fdopen(descriptor, "rb") as output:
        before = os.fstat(output.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Reader output must be a bounded regular file")
        # One bounded allocation avoids retaining a second full pixel payload
        # in a chunk list while joining large images in the supervisor.
        data = output.read(before.st_size + 1)
        after = os.fstat(output.fileno())
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after) or len(data) != before.st_size:
            raise ValueError("Reader output changed during collection")
    return data

def validate_reply(request, result, data):
    if result.get("request_id") != request.get("request_id"):
        raise ValueError("Reader reply identity mismatch")
    action = request.get("action")
    if action in {"decode", "decode_frames", "inspect", "infer"} and not re.fullmatch("[0-9a-f]{64}", result.get("input_sha256", "")):
        raise ValueError("Reader reply lacks input integrity")
    if action == "validate_model" and type(result.get("valid")) is not bool:
        raise ValueError("Invalid model validation reply")
    if action in {"decode", "decode_frames", "inspect"}:
        identity, decoder = result.get("source_identity"), result.get("decoder_identity")
        if (not isinstance(identity, list) or len(identity) != 5 or
                any(type(value) is not int for value in identity) or any(value < 0 for value in identity[:3])):
            raise ValueError("Invalid reader source identity")
        if (not isinstance(decoder, list) or len(decoder) != 2 or
                any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", value) for value in decoder)):
            raise ValueError("Invalid reader decoder identity")
        target = result.get("target_size")
        expected = request.get("target_size")
        if (tuple(target) if isinstance(target, (tuple, list)) else target) != (tuple(expected) if expected else None):
            raise ValueError("Reader preview identity mismatch")
    if action == "decode_frames":
        frames, expected = result.get("frames"), request.get("frames")
        if (not isinstance(frames, list) or not isinstance(expected, list) or
                not 1 <= len(expected) == len(frames) <= 8 or len(data) > 64 * 1024 * 1024 or
                any(type(frame) is not int for frame in expected) or len(set(expected)) != len(expected)):
            raise ValueError("Invalid reader frame batch")
        offset = 0
        for item, frame in zip(frames, expected, strict=True):
            if (not isinstance(item, dict) or type(item.get("offset")) is not int or item["offset"] != offset or
                    type(item.get("byte_length")) is not int or item["byte_length"] <= 0 or
                    offset + item["byte_length"] > len(data)):
                raise ValueError("Invalid frame batch offsets")
            if any(item.get(field) != result.get(field) for field in ("source_identity", "decoder_identity", "target_size")):
                raise ValueError("Frame batch contains inconsistent input or component identity")
            if any(item.get(field) != frames[0].get(field) for field in ("frame_count", "loop_count", "total_plays")):
                raise ValueError("Frame batch contains inconsistent animation semantics")
            end = offset + item["byte_length"]
            validate_reply({**request, "action": "decode", "frame": frame},
                           {**result, **item, "request_id": result.get("request_id")}, data[offset:end])
            offset = end
        if offset != len(data):
            raise ValueError("Frame batch contains unclaimed bytes")
    if action == "decode":
        width, height, stride = (result.get(field) for field in ("width", "height", "stride"))
        if (any(type(value) is not int or value <= 0 for value in (width, height, stride)) or
                stride != width * 4 or len(data) != stride * height or len(data) > 500 * 1024 * 1024):
            raise ValueError("Invalid decoded pixel payload")
        frame, count = result.get("frame"), result.get("frame_count")
        if (type(frame) is not int or frame != request.get("frame", 0) or type(count) is not int or
                not 0 <= frame < count <= 100000):
            raise ValueError("Invalid decoded frame identity/count")
        duration, loop = result.get("duration_ms"), result.get("loop_count")
        if (type(duration) is not int or not 0 <= duration <= 3600000 or
                (loop is not None and (type(loop) is not int or not 0 <= loop <= 4294967295))):
            raise ValueError("Invalid frame timing")
        if "total_plays" in result and (type(result["total_plays"]) is not int or not 0 <= result["total_plays"] <= 4294967296):
            raise ValueError("Invalid normalized animation play count")

def run_reader(request: dict, *, cancelled: Callable[[], bool] = lambda: False,
               timeout: float = 30.0, command: list[str] | None = None) -> tuple[dict, bytes]:
    from .paths import get_cache_dir
    directory = Path(tempfile.mkdtemp(prefix="reader-", dir=get_cache_dir()))
    proc = None
    output_stream = tempfile.TemporaryFile(dir=directory)
    error_stream = tempfile.TemporaryFile(dir=directory)
    try:
        proc = subprocess.Popen(command or worker_command("reader-job"), stdin=subprocess.PIPE,
                                stdout=output_stream, stderr=error_stream,
                                env=worker_environment(), start_new_session=True)
        deadline = time.monotonic() + timeout
        sent = False
        while True:
            if (os.fstat(output_stream.fileno()).st_size > 1024 * 1024 or
                    os.fstat(error_stream.fileno()).st_size > 1024 * 1024):
                raise ValueError("Reader status/error output exceeds its one-MiB channel budget")
            if cancelled() or time.monotonic() >= deadline:
                raise ReaderCancelled("Reader cancelled" if cancelled() else "Reader deadline exceeded")
            try:
                proc.communicate(input=None if sent else encode({**request, "output_directory": str(directory)}), timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                sent = True
        if (os.fstat(output_stream.fileno()).st_size > 1024 * 1024 or
                os.fstat(error_stream.fileno()).st_size > 1024 * 1024):
            raise ValueError("Reader status/error output exceeds its one-MiB channel budget")
        if proc.returncode != 0:
            error_stream.seek(max(0, error_stream.tell() - 2000))
            raise RuntimeError(error_stream.read(2000).decode(errors="replace") or "Reader failed")
        output_stream.seek(0)
        stdout = output_stream.read(1024 * 1024 + 1)
        result = decode(stdout.strip())
        if result.get("error"):
            raise RuntimeError(result["error"])
        payload_limit = 64 * 1024 * 1024 if request.get("action") == "decode_frames" else 500 * 1024 * 1024
        data = read_pixel_output(directory / "result.rgba", payload_limit)
        validate_reply(request, result, data)
        return result, data
    finally:
        group_stopped = True
        if proc and os.name == "posix":
            # Also reap descendants if their reader parent crashed first. Every
            # member of this dedicated group is read-only; mutators never join it.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                deadline = time.monotonic() + 0.8
                while time.monotonic() < deadline:
                    proc.poll()
                    os.killpg(proc.pid, 0)
                    time.sleep(0.01)
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc and proc.poll() is None:
            if os.name != "posix":
                proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    group_stopped = False
        if proc and os.name == "posix":
            try:
                os.killpg(proc.pid, 0)
                group_stopped = False
            except ProcessLookupError:
                pass
        if proc:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        output_stream.close()
        error_stream.close()
        # A kernel-stalled reader still owns its scratch data; preserve it.
        if group_stopped and (proc is None or proc.poll() is not None):
            shutil.rmtree(directory)

"""Private capability worker: original inputs are read-only snapshots."""
from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import sys
from pathlib import Path

from .worker_protocol import MAX_MESSAGE_BYTES, decode, encode

_LEAF_ROLE = False

def _snapshot(source: str, directory: Path, *, identity_out=None) -> tuple[Path, str]:
    original = Path(source).absolute()
    if original.is_symlink():
        raise ValueError("Reader input must not be a symbolic link")
    destination = directory / ("input" + original.suffix)
    digest = hashlib.sha256()
    descriptor = os.open(original, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as src, destination.open("xb") as dst:
        before = os.fstat(src.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Reader input must be a regular file")
        if before.st_size > 512 * 1024 * 1024:
            raise ValueError("Reader input exceeds the 512 MiB snapshot limit")
        copied = 0
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            copied += len(chunk)
            if copied > 512 * 1024 * 1024:
                raise ValueError("Reader input grew beyond its snapshot limit")
            digest.update(chunk)
            dst.write(chunk)
        after = os.fstat(src.fileno())
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after) or copied != before.st_size:
            raise ValueError("Reader input changed during snapshot")
        if identity_out is not None:
            identity_out["source_identity"] = list(identity(before))
    destination.chmod(0o400)
    return destination, digest.hexdigest()

def execute(request: dict) -> dict:
    action = request.get("action")
    directory = Path(request["output_directory"])
    if action == "validate_model":
        from .ai_tagger import is_model_and_labels_valid
        return {"valid": is_model_and_labels_valid(request.get("model_dir"))}
    if action == "hardware":
        from .hardware_scan import scan_hardware
        return {"hardware": scan_hardware()}
    identity = {}
    path, digest = _snapshot(request["filepath"], directory, identity_out=identity)
    if action == "infer":
        from .component_manager import ComponentManager
        from .component_runtime import infer_component
        model_dir = request.get("model_dir")
        if model_dir is None:
            selected = ComponentManager().active_path("ai.mobilenet-v2")
            if selected is None:
                raise ValueError("Optional tagging model is not installed and enabled; use Optional Components to install it explicitly")
            model_dir = str(selected)
        result = infer_component(str(path), model_dir, hardware=request.get("hardware", True), threshold=request.get("threshold", 0.5))
        return {**result, "input_sha256": digest}
    if action == "decode_frames":
        from .component_runtime import decode_frames_component
        rows = decode_frames_component(str(path), "viewer.animation-multipage", request["frames"],
                                       target_size=request.get("target_size"), generation=request.get("generation", 0))
        frames, offset = [], 0
        with (directory / "result.rgba").open("xb") as output:
            for pixels, metadata in rows:
                if offset + len(pixels) > 64 * 1024 * 1024:
                    raise ValueError("Frame batch exceeds its aggregate pixel budget")
                identity.update(decoder_identity=["viewer.animation-multipage", metadata["component_version"]],
                                target_size=request.get("target_size"))
                frames.append({**identity, **metadata, "offset": offset, "byte_length": len(pixels)})
                output.write(pixels)
                offset += len(pixels)
        return {**identity, "input_sha256": digest, "frames": frames}
    if action not in {"decode", "inspect"}:
        raise ValueError("Unknown reader action")
    from .component_manager import ComponentManager
    from .components import component_for_extension
    extension = path.suffix.lower().lstrip(".")
    component = component_for_extension(extension)
    component_id = component.component_id if component else None
    if extension in {"gif", "png", "apng", "webp", "tif", "tiff"}:
        component_id = "viewer.animation-multipage"
    active = ComponentManager().active_path(component_id) if component_id else None
    identity.update(decoder_identity=[component_id, active.name] if active else ["core.qt", "base"],
                    target_size=request.get("target_size"), frame=request.get("frame", 0))
    if active is not None:
        from .component_runtime import decode_component
        pixels, metadata = decode_component(str(path), component_id, frame=request.get("frame", 0),
                                            target_size=request.get("target_size") if action == "decode" else (1, 1),
                                            generation=request.get("generation", 0))
        identity["decoder_identity"] = [component_id, metadata["component_version"]]
        if action == "inspect":
            return {**identity, **metadata, "input_sha256": digest}
        (directory / "result.rgba").write_bytes(pixels)
        return {**identity, **metadata, "input_sha256": digest}
    if _LEAF_ROLE:
        from .reader_sandbox import restrict_leaf_reader
        identity["reader_isolation"] = restrict_leaf_reader(directory)
    if action == "inspect":
        from .image_decoding import inspect_image_header
        return {**identity, **inspect_image_header(path), "input_sha256": digest}
    from PyQt6.QtGui import QImage

    from .image_decoding import decode_image
    image, error = decode_image(path, target_size=request.get("target_size"), frame=request.get("frame", 0))
    if image is None:
        raise ValueError(error or "Decode failed")
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pointer = image.constBits()
    pointer.setsize(image.sizeInBytes())
    (directory / "result.rgba").write_bytes(bytes(pointer))
    return {**identity, "width": image.width(), "height": image.height(), "stride": image.bytesPerLine(),
            "input_sha256": digest, "frame": request.get("frame", 0), "frame_count": 1,
            "duration_ms": 0, "loop_count": 0, "total_plays": 1}

def main(_args=None) -> int:
    global _LEAF_ROLE
    _LEAF_ROLE = True
    try:
        if os.name == "posix":
            import resource
            # Bound per-file scratch/log growth in every descendant. Image
            # originals are never written by a reader or optional provider.
            resource.setrlimit(resource.RLIMIT_FSIZE, (600 * 1024 * 1024, 600 * 1024 * 1024))
        request = decode(sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 2).strip())
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(request)
        # Nested component helpers own their own request IDs; never let those
        # overwrite this supervisor boundary's caller identity.
        sys.stdout.buffer.write(encode({**result, "request_id": request.get("request_id")}))
        return 0
    except Exception as exc:
        sys.stdout.buffer.write(encode({"error": str(exc)}))
        return 0
    finally:
        _LEAF_ROLE = False

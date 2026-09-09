"""Standalone read-only capability process with bounded JSON/binary responses."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import signal
import sys
import tempfile
from pathlib import Path

PROTOCOL_VERSION = 1
MAX_HEADER = 65536
MAX_PIXELS = 125_000_000
MAX_PAYLOAD = 500 * 1024 * 1024
MAX_BATCH_FRAMES = 8
MAX_BATCH_BYTES = 64 * 1024 * 1024
_READER_PROCESS = False


def _arm_deadline(seconds):
    # These are disposable main-thread reader processes, never file writers.
    # Default OS signal handling also interrupts a stalled native extension.
    if _READER_PROCESS and hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL, seconds)


def _gpu_compute_probe() -> dict:
    """Run a tiny dynamic-input MatMul, without downloading a model or driver."""
    import numpy as np
    import onnxruntime as ort
    def integer(value):
        result = bytearray()
        while value > 127:
            result.append((value & 127) | 128)
            value >>= 7
        return bytes(result) + bytes([value])
    def number(field, value):
        return integer(field << 3) + integer(value)
    def data(field, value):
        value = value.encode() if isinstance(value, str) else value
        return integer(field << 3 | 2) + integer(len(value)) + value
    def tensor(name):
        shape = data(1, number(1, 2)) * 2
        tensor_type = number(1, 1) + data(2, shape)
        return data(1, name) + data(2, data(1, tensor_type))
    node = data(1, "A") + data(1, "B") + data(2, "C") + data(3, "activation_matmul") + data(4, "MatMul")
    graph = data(1, node) + data(2, "ImageSorter CUDA activation") + data(11, tensor("A")) + data(11, tensor("B")) + data(12, tensor("C"))
    model = number(1, 9) + data(7, graph) + data(8, number(2, 13))
    options = ort.SessionOptions()
    options.enable_profiling = True
    options.intra_op_num_threads = 2
    with tempfile.TemporaryDirectory(prefix=".cuda-probe-", dir=Path.cwd()) as directory:
        options.profile_file_prefix = str(Path(directory) / "probe")
        session = ort.InferenceSession(model, sess_options=options, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        expected = np.asarray([[1., 2.], [3., 4.]], dtype=np.float32)
        actual = session.run(None, {"A": expected, "B": np.eye(2, dtype=np.float32)})[0]
        profile = Path(session.end_profiling()).read_bytes()
        count = sum(item.get("args", {}).get("provider") == "CUDAExecutionProvider" for item in json.loads(profile))
        if not np.array_equal(actual, expected) or count == 0:
            raise ValueError("CUDA activation did not execute the expected compute node")
        return {"cuda_compute_events": count, "profile_sha256": hashlib.sha256(profile).hexdigest(),
                "actual_providers": session.get_providers()}


def probe(component_id: str) -> dict:
    result = {"protocol_version": 1, "component_id": component_id, "ready": True}
    if component_id == "codec.heif-avif":
        import pillow_heif
        from PIL import features
        pillow_heif.register_heif_opener()
        result.update(heif=pillow_heif.__version__, avif=features.check("avif"))
        result["ready"] = result["avif"]
    elif component_id == "codec.camera-raw":
        import rawpy
        result["libraw"] = list(rawpy.libraw_version)
    elif component_id == "viewer.animation-multipage":
        from PIL import Image, features
        Image.init()
        result["ready"] = features.check("webp")
    elif component_id == "provider.onnx-nvidia":
        import onnxruntime as ort
        if hasattr(ort, "preload_dlls"):
            with contextlib.redirect_stdout(sys.stderr):
                ort.preload_dlls(directory=str(sys._MEIPASS) if getattr(sys, "frozen", False) else "")
        result["providers"] = ort.get_available_providers()
        result["ready"] = "CUDAExecutionProvider" in result["providers"]
        if result["ready"]:
            result.update(_gpu_compute_probe())
    elif component_id != "core.cpu":
        raise ValueError("Unsupported component identity")
    return result


def decode(request: dict, component_id: str, *, arm_deadline: bool = True) -> tuple[dict, bytes]:
    if arm_deadline:
        _arm_deadline(30)
    from PIL import Image, ImageCms, ImageOps
    source = Path(request["input_path"])
    if not source.is_file():
        raise ValueError("Input must be a regular snapshot")
    frame = request.get("frame", 0)
    if not isinstance(frame, int) or not 0 <= frame <= 100000:
        raise ValueError("Invalid frame index")
    if component_id == "codec.camera-raw":
        import rawpy
        try:
            raw_handle = rawpy.imread(str(source))
        except rawpy.LibRawFileUnsupportedError as exc:
            raise ValueError(f"Unsupported camera/RAW variant for LibRaw {rawpy.libraw_version}: {exc}") from exc
        with raw_handle as raw:
            if raw.sizes.width * raw.sizes.height > MAX_PIXELS:
                raise ValueError("RAW allocation limit exceeded")
            if frame:
                raise ValueError("RAW has a single displayed frame")
            image = None
            target = request.get("target_size")
            if (isinstance(target, (list, tuple)) and len(target) == 2 and
                    all(type(n) is int and 0 < n <= 32768 for n in target)):
                try:
                    oriented_preview = False
                    preview = raw.extract_thumb()
                    if preview.format == rawpy.ThumbFormat.JPEG:
                        with Image.open(io.BytesIO(preview.data)) as thumbnail:
                            oriented_preview = thumbnail.getexif().get(274, 1) not in {0, 1}
                            image = ImageOps.exif_transpose(thumbnail).copy()
                    elif preview.format == rawpy.ThumbFormat.BITMAP:
                        image = Image.fromarray(preview.data)
                    if image is not None:
                        rotation = {3: Image.Transpose.ROTATE_180, 5: Image.Transpose.ROTATE_90,
                                    6: Image.Transpose.ROTATE_270}.get(raw.sizes.flip)
                        if rotation is not None and not oriented_preview:
                            image = image.transpose(rotation)
                        if image.width < target[0] or image.height < target[1]:
                            image = None
                except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError, OSError):
                    image = None
            if image is None:
                image = Image.fromarray(raw.postprocess(use_camera_wb=True,
                    output_color=rawpy.ColorSpace.sRGB, output_bps=8, no_auto_bright=True))
                image.info["preview_source"] = "camera-white-balance-srgb"
            else:
                image.info["preview_source"] = "embedded-camera-preview"
            size = [raw.sizes.width, raw.sizes.height]
            image.info["raw_original_size"] = size[::-1] if raw.sizes.flip in {5, 6} else size
        count, duration, loop, total_plays = 1, 0, None, 1
    else:
        if component_id == "codec.heif-avif":
            import pillow_heif
            pillow_heif.register_heif_opener()
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(source) as opened:
            detected_format = opened.format
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError("Image allocation limit exceeded")
            count = getattr(opened, "n_frames", 1)
            if frame >= count:
                raise ValueError("Frame exceeds frame count")
            if opened.format == "PNG" and getattr(opened, "is_animated", False):
                from imagesorter.apng_frames import decode_apng
                image, count, duration, loop = decode_apng(source, frame)
                image = ImageOps.exif_transpose(image)
            else:
                opened.seek(frame)
                opened.load()
                duration = max(10, min(3600000, int(opened.info.get("duration", 100)))) if count > 1 else 0
                declared_loop = opened.info.get("loop")
                loop = int(declared_loop) if declared_loop is not None else None
                if (loop is not None and not 0 <= loop <= 4294967295) or not 1 <= count <= 100000:
                    raise ValueError("Invalid frame count or loop metadata")
                image = ImageOps.exif_transpose(opened).copy()
            # GIF's extension counts repetitions after the initial play;
            # PNG acTL and WebP ANIM count total plays. Preserve the raw field.
            total_plays = (1 if loop is None else 0 if loop == 0 else
                           loop + 1 if detected_format == "GIF" else loop)
        icc = image.info.get("icc_profile")
        if icc:
            image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                              ImageCms.createProfile("sRGB"), outputMode="RGBA")
    original_size = image.info.get("raw_original_size", list(image.size))
    preview_source = image.info.get("preview_source", "decoded-raster")
    target = request.get("target_size")
    if target is not None:
        if (not isinstance(target, (list, tuple)) or len(target) != 2 or
                any(not isinstance(n, int) or not 0 < n <= 32768 for n in target)):
            raise ValueError("Invalid preview dimensions")
        image.thumbnail(tuple(target), Image.Resampling.LANCZOS)
    image = image.convert("RGBA")
    if image.width * image.height * 4 > MAX_PAYLOAD:
        raise ValueError("Decoded payload exceeds bound")
    payload = image.tobytes()
    return {"width": image.width, "height": image.height, "stride": image.width * 4,
            "pixel_format": "RGBA", "frame": frame, "frame_count": count,
            "duration_ms": duration, "loop_count": loop, "total_plays": total_plays, "original_size": original_size,
            "preview_source": preview_source,
            "payload_length": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest()}, payload


def infer(request: dict, component_id: str) -> tuple[dict, bytes]:
    _arm_deadline(60)
    import numpy as np
    import onnxruntime as ort

    from imagesorter.ai_preprocessing import preprocess_image
    model = Path(request["model_dir"]) / "mobilenetv2.onnx"
    labels_file = Path(request["model_dir"]) / "labels.txt"
    for path, digest in ((model, request["model_sha256"]), (labels_file, request["labels_sha256"])):
        observed = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                observed.update(block)
        if observed.hexdigest() != digest:
            raise ValueError("Model or labels checksum mismatch")
    labels = labels_file.read_text(encoding="utf-8").splitlines()
    tensor = preprocess_image(request["input_path"])
    gpu = component_id == "provider.onnx-nvidia"
    if gpu:
        probe(component_id)
    options = ort.SessionOptions()
    options.enable_profiling = gpu
    options.intra_op_num_threads = 2
    with tempfile.TemporaryDirectory(prefix=".inference-profile-", dir=Path.cwd()) as profile_dir:
        options.profile_file_prefix = str(Path(profile_dir) / "ort")
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if gpu else ["CPUExecutionProvider"]
        session = ort.InferenceSession(str(model), sess_options=options, providers=providers)
        _arm_deadline(30)
        values = np.asarray(session.run(None, {session.get_inputs()[0].name: tensor})[0]).reshape(-1)
        _arm_deadline(0)
        if not np.isfinite(values).all():
            raise ValueError("Inference produced non-finite values")
        if len(values) == len(labels) + 1:
            values = values[1:]
        if len(values) != len(labels):
            raise ValueError("Output shape does not match labels")
        if values.min() < 0 or values.max() > 1 or not np.isclose(values.sum(), 1., atol=.05):
            exponent = np.exp(values - values.max())
            values = exponent / exponent.sum()
        threshold = float(request.get("threshold", 0.0))
        if not 0 <= threshold <= 1:
            raise ValueError("Invalid threshold")
        tags = [{"tag": labels[int(index)], "score": float(values[index])}
                for index in np.argsort(values)[::-1][:10] if values[index] >= threshold]
        events, nodes, profile_digest = 0, [], None
        if gpu:
            profile_bytes = Path(session.end_profiling()).read_bytes()
            profile_digest = hashlib.sha256(profile_bytes).hexdigest()
            records = json.loads(profile_bytes)
            nodes = [{"name": item.get("name"), "provider": item.get("args", {}).get("provider")}
                     for item in records if item.get("args", {}).get("provider")]
            events = sum(item.get("args", {}).get("provider") == "CUDAExecutionProvider" for item in records)
            if events == 0:
                raise ValueError("No profiled CUDA compute execution; CPU fallback required")
        return {"tags": tags, "provider": providers[0], "cuda_compute_events": events,
                "actual_providers": session.get_providers(), "compute_nodes": nodes,
                "profile_sha256": profile_digest,
                "tensor_sha256": hashlib.sha256(tensor.tobytes()).hexdigest(), "payload_length": 0}, b""


def decode_frames(request: dict, component_id: str) -> tuple[dict, bytes]:
    """Amortize interpreter startup without an unbounded persistent decoder."""
    frames = request.get("frames")
    if (not isinstance(frames, list) or not 1 <= len(frames) <= MAX_BATCH_FRAMES or
            any(type(frame) is not int or not 0 <= frame < 100000 for frame in frames) or
            len(set(frames)) != len(frames)):
        raise ValueError("A batch requires 1 to 8 unique frame indices")
    _arm_deadline(30)  # The entire batch, not a fresh deadline per frame.
    results, chunks, offset = [], [], 0
    for frame in frames:
        metadata, pixels = decode(dict(request, frame=frame), component_id, arm_deadline=False)
        if offset + len(pixels) > MAX_BATCH_BYTES:
            raise ValueError("Frame batch exceeds its 64 MiB payload limit")
        results.append(dict(metadata, payload_offset=offset))
        chunks.append(pixels)
        offset += len(pixels)
    return {"frames": results, "payload_length": offset}, b"".join(chunks)


def main(argv: list[str] | None = None) -> int:
    global _READER_PROCESS
    _READER_PROCESS = True
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--component-id")
    args = parser.parse_args(argv)
    config = Path(sys.executable).parent / "component_config.json"
    component_id = args.component_id
    component_version = "base"
    if component_id is None and config.is_file():
        configuration = json.loads(config.read_text(encoding="utf-8"))
        component_id, component_version = configuration["component_id"], configuration["version"]
    request = {}
    try:
        from imagesorter.reader_sandbox import restrict_leaf_reader
        isolation = restrict_leaf_reader(Path.cwd(), gpu=component_id == "provider.onnx-nvidia")
        if args.probe:
            _arm_deadline(30)
            print(json.dumps(dict(probe(component_id), component_version=component_version, reader_isolation=isolation)))
            return 0
        line = sys.stdin.buffer.readline(MAX_HEADER + 1)
        if len(line) > MAX_HEADER or not line.endswith(b"\n"):
            raise ValueError("Invalid bounded request header")
        request = json.loads(line)
        if not isinstance(request, dict):
            request = {}
            raise ValueError("Request header must be an object")
        if (type(request.get("protocol_version")) is not int or request.get("protocol_version") != 1 or
                request.get("component_id") != component_id or
                request.get("component_version", "base") != component_version):
            raise ValueError("Protocol or component identity mismatch")
        if request.get("action") == "infer":
            result, payload = infer(request, component_id)
        elif request.get("action") == "decode_frames":
            result, payload = decode_frames(request, component_id)
        elif request.get("action") in {"decode_frame", "inspect"}:
            result, payload = decode(request, component_id)
            if request["action"] == "inspect":
                result["payload_length"], payload = 0, b""
        else:
            raise ValueError("Unsupported helper action")
        result.update(protocol_version=1, request_id=request.get("request_id"),
                      generation=request.get("generation", 0), component_id=component_id,
                      component_version=component_version, reader_isolation=isolation, ok=True)
        sys.stdout.buffer.write(json.dumps(result).encode() + b"\n" + payload)
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        print(json.dumps({"protocol_version": 1, "request_id": request.get("request_id"),
                          "component_id": component_id, "component_version": component_version,
                          "generation": request.get("generation", 0), "ok": False,
                          "error": str(exc), "payload_length": 0}))
        return 1
    finally:
        _arm_deadline(0)
        _READER_PROCESS = False


if __name__ == "__main__":
    raise SystemExit(main())

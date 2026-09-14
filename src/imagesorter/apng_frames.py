"""Bounded APNG composition with proper source-over alpha and disposal.

Pillow's PNG raster decoding is retained; composition is explicit because past
Pillow APNG paths apply source alpha twice when pasting onto an opaque canvas.
"""
from __future__ import annotations

import io
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PIXELS = 125_000_000
MAX_INPUT = 512 * 1024 * 1024


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def decode_apng(path, requested: int):
    """Return composited RGBA frame plus validated frame count/duration/loop."""
    from PIL import Image
    chunks, total = [], 0
    with open(path, "rb") as source:
        if source.read(8) != SIGNATURE:
            raise ValueError("Invalid APNG signature")
        while True:
            header = source.read(8)
            if len(header) != 8:
                raise ValueError("Truncated APNG chunk")
            length, kind = struct.unpack(">I4s", header)
            total += length + 12
            if total > MAX_INPUT:
                raise ValueError("APNG compressed input exceeds limit")
            content = source.read(length)
            checksum = source.read(4)
            if len(content) != length or len(checksum) != 4 or struct.unpack(">I", checksum)[0] != zlib.crc32(kind + content):
                raise ValueError("APNG chunk checksum or length mismatch")
            chunks.append((kind, content))
            if kind == b"IEND":
                if source.read(1):
                    raise ValueError("Trailing APNG data")
                break
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise ValueError("Missing APNG image header")
    image_header = chunks[0][1]
    width, height = struct.unpack(">II", image_header[:8])
    if not width or not height or width * height > MAX_PIXELS:
        raise ValueError("APNG canvas exceeds allocation limit")
    global_chunks, frames, frame = [], [], None
    expected_sequence, declared, loop = 0, None, 0
    for kind, content in chunks[1:]:
        if kind == b"acTL":
            if declared is not None or len(content) != 8:
                raise ValueError("Invalid APNG animation control")
            declared, loop = struct.unpack(">II", content)
            if not 1 <= declared <= 100000:
                raise ValueError("APNG frame count exceeds limit")
        elif kind == b"fcTL":
            if declared is None or len(content) != 26:
                raise ValueError("Invalid APNG frame control")
            sequence, fw, fh, x, y, numerator, denominator, dispose, blend = struct.unpack(">IIIIIHHBB", content)
            if (sequence != expected_sequence or not fw or not fh or x + fw > width or y + fh > height or
                    dispose > 2 or blend > 1):
                raise ValueError("Invalid APNG frame geometry, sequence or composition")
            expected_sequence += 1
            frame = {"width": fw, "height": fh, "x": x, "y": y, "dispose": dispose, "blend": blend,
                     "duration": max(10, min(3600000, round(1000 * numerator / (denominator or 100)))), "data": []}
            frames.append(frame)
            if len(frames) > declared:
                raise ValueError("APNG frame count disagrees with control")
        elif kind in {b"IDAT", b"fdAT"}:
            if kind == b"fdAT":
                if len(content) < 4 or struct.unpack(">I", content[:4])[0] != expected_sequence or frame is None:
                    raise ValueError("Invalid APNG frame data sequence")
                expected_sequence += 1
                content = content[4:]
            # IDAT preceding fcTL is the optional non-animation default image.
            if frame is not None:
                frame["data"].append(content)
        elif kind in {b"PLTE", b"tRNS", b"iCCP", b"sRGB", b"gAMA", b"cHRM", b"eXIf"}:
            global_chunks.append((kind, content))
        elif kind not in {b"IEND", b"tEXt", b"zTXt", b"iTXt", b"pHYs", b"bKGD", b"sBIT", b"tIME"} and not kind[0] & 32:
            raise ValueError("Unknown critical APNG chunk")
    if declared is None or len(frames) != declared or not 0 <= requested < declared:
        raise ValueError("APNG frame request/count mismatch")
    canvas = Image.new("RGBA", (width, height))
    for index, frame in enumerate(frames[:requested + 1]):
        if not frame["data"]:
            raise ValueError("APNG frame has no image data")
        header = struct.pack(">II", frame["width"], frame["height"]) + image_header[8:]
        png = SIGNATURE + _chunk(b"IHDR", header)
        png += b"".join(_chunk(kind, content) for kind, content in global_chunks)
        png += b"".join(_chunk(b"IDAT", data) for data in frame["data"]) + _chunk(b"IEND", b"")
        with Image.open(io.BytesIO(png)) as decoded:
            raster = decoded.convert("RGBA")
            canvas.info.update(decoded.info)
        previous = canvas.copy() if frame["dispose"] == 2 else None
        if frame["blend"] == 0:
            canvas.paste(raster, (frame["x"], frame["y"]))
        else:
            canvas.alpha_composite(raster, (frame["x"], frame["y"]))
        if index == requested:
            return canvas, declared, frame["duration"], loop
        if frame["dispose"] == 1:
            canvas.paste((0, 0, 0, 0), (frame["x"], frame["y"], frame["x"] + frame["width"], frame["y"] + frame["height"]))
        elif previous is not None:
            canvas = previous
    raise ValueError("No requested APNG frame")

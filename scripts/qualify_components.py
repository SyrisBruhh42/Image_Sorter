"""Exercise real local component archives against pinned disposable fixtures.

This records component-level evidence, not the KDE/artifact/power-loss matrix.
Network access occurs only with --download-fixtures; all image inputs are copies.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

RAW_FIXTURES = {
    "cr2": ("2102/nice/Canon - EOS 40D - sRAW2 (sRAW) (3:2).CR2", "ba644e7dd2abe74eca260e67f0206ff113bf0f62e710f8130611e964d6be5bf1"),
    "orf": ("5424/nice/Olympus - E-10 - 16bit (4:3).ORF", "2bfdade72439017a60a47aad1e5bbcb1aca36f2be7a21e94a4257a678fb6f4da"),
    "rw2": ("7008/nice/Panasonic - DMC-LX7 - 1:1.RW2", "d142a23aca836053ed53e9ce3cb3ed2d434541d734d71a94a6eefadcd08bd31b"),
    "nef": ("4282/nice/Nikon - Nikon COOLSCAN IV ED - uncompressed (4:3).nef", "268d9a98920a9f3ea3ebf6a2b9ff68b956df74ac0e46b980bee69e7ef3ebc172"),
    "raf": ("2726/nice/Fujifilm - FinePix S5000 - 4:3.RAF", "dabd5e74521a6980156be9fd4b88d0c37b0fe4d0e0e6f5c12db8cffff1b76297"),
    "dng": ("7317/nice/Blackmagic - Micro Cinema Camera - 12bit (16:9).dng", "4c65b8cda205087cfb94d8931811e53e15eb4df538ad67c1b4a3e76c1185b277"),
    "arw": ("1582/nice/Sony - ILCE-7S - 14bit 14bit compressed (3:2).ARW", "a35ebb2fbec929daa5beb20d1ce5c15a8aac7b1a7a231455387f3df8a7442e07"),
    "pef": ("2239/nice/Pentax - K10D - 12bit 12bit compressed (3:2).PEF", "e35ae4154a468be3154f5f462e884ba5941f010d3e8f23d347fbec14809f44d3"),
    "srw": ("3668/nice/Samsung - NX500 - 12bit 12bit normal compression (3:2).SRW", "156e43118811bcecb6fcc600b5f41ee1773c4f00b6a5b2dd72e177e35f6596ab"),
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def make_apng(path):
    """Known independent pixel oracle: restore-previous and alpha source-over."""
    width, height = 8, 8
    result = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    result += chunk(b"acTL", struct.pack(">II", 3, 2))
    sequence = 0
    frames = [(bytes((255, 0, 0, 255)), 0, 0),
              (bytes((0, 0, 255, 128)), 2, 1),
              (bytes((0, 255, 0, 128)), 0, 1)]
    for index, (pixel, dispose, blend) in enumerate(frames):
        control = struct.pack(">IIIIIHHBB", sequence, width, height, 0, 0, 1, 10, dispose, blend)
        result += chunk(b"fcTL", control)
        sequence += 1
        compressed = zlib.compress((b"\0" + pixel * width) * height)
        if index == 0:
            result += chunk(b"IDAT", compressed)
        else:
            result += chunk(b"fdAT", struct.pack(">I", sequence) + compressed)
            sequence += 1
    path.write_bytes(result + chunk(b"IEND", b""))


def generated_fixtures(directory):
    from PIL import Image
    directory.mkdir()
    frames = [Image.new("RGBA", (32, 24), color) for color in ("red", "blue", "green")]
    frames[0].save(directory / "animation.gif", save_all=True, append_images=frames[1:], duration=[100, 200, 300], loop=2, disposal=[1, 2, 3])
    frames[0].save(directory / "animation.webp", save_all=True, append_images=frames[1:], duration=[100, 200, 300], loop=2, lossless=True)
    frames[0].save(directory / "pages.tiff", save_all=True, append_images=frames[1:])
    frames[0].convert("RGB").save(directory / "inference.jpg")
    make_apng(directory / "blend.apng")
    return frames


def generate_codec_fixtures(directory):
    """Run explicitly with the isolated codec build environment, not base app."""
    import pillow_heif
    from PIL import Image, ImageCms
    pillow_heif.register_heif_opener()
    directory.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (32, 24), (255, 0, 0, 128))
    orientation = Image.Exif()
    orientation[274] = 6
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for extension in ("heif", "heic", "avif"):
        options = {"format": "AVIF" if extension == "avif" else "HEIF", "exif": orientation.tobytes(), "icc_profile": profile}
        if extension == "avif":
            options.update(quality=100, subsampling="4:4:4")
        else:
            options.update(quality=-1, chroma="444")
        image.save(directory / f"orientation-alpha.{extension}", **options)


def raw_fixtures(directory, download):
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for extension, (relative, digest) in RAW_FIXTURES.items():
        target = directory / f"fixture.{extension}"
        url = "https://raw.pixls.us/getfile.php/" + urllib.parse.quote(relative, safe="/")
        if not target.exists() and download:
            with urllib.request.urlopen(url, timeout=30) as response, target.open("xb") as output:
                shutil.copyfileobj(response, output)
        if not target.is_file() or sha(target) != digest:
            raise ValueError(f"Missing or corrupt pinned fixture: {target}")
        records.append({"path": str(target), "sha256": digest, "url": url, "license": "CC0-1.0",
                        "license_evidence": "https://raw.pixls.us/json/getrepository.php?set=all"})
    return records


def update_fixture(archive, descriptor, directory):
    """Build an explicitly test-only update of the same binary and a new version config."""
    updated = copy.deepcopy(descriptor)
    updated["version"] += ".qualification-update"
    target = directory / (descriptor["id"] + "-update.tar.gz")
    with archive.open("rb") as source, tarfile.open(fileobj=source, mode="r:gz") as original, \
            target.open("xb") as output, gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as package:
        for member in original:
            stream = original.extractfile(member)
            if member.name == "component_config.json":
                content = json.loads(stream.read())
                content["version"] = updated["version"]
                encoded = (json.dumps(content, sort_keys=True, indent=2) + "\n").encode()
                member.size = len(encoded)
                stream = io.BytesIO(encoded)
                updated["files"][member.name].update(size=len(encoded), sha256=hashlib.sha256(encoded).hexdigest())
            with stream:
                package.addfile(member, stream)
    updated.update(sha256=sha(target), archive_size=target.stat().st_size,
                   installed_size=sum(item["size"] for item in updated["files"].values()))
    return target, updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-fixtures", type=Path)
    parser.add_argument("--codec-fixtures", type=Path)
    parser.add_argument("--download-fixtures", action="store_true")
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--full-lifecycle", action="store_true", help="test same-binary version updates, rollback, leases and removal")
    args = parser.parse_args()

    from imagesorter.component_manager import (
        ComponentBusy,
        ComponentError,
        ComponentManager,
    )
    from imagesorter.component_runtime import decode_component, infer_component
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    manager = ComponentManager(output / "components", args.packs / "component_catalog.json")
    fixtures = output / "fixtures"
    generated_fixtures(fixtures)
    cases = []

    def check(name, function):
        started = time.monotonic()
        try:
            detail = function()
            case = {"id": name, "status": "passed", "detail": detail}
        except Exception as exc:
            case = {"id": name, "status": "failed", "error": str(exc)}
        case["elapsed_seconds"] = time.monotonic() - started
        cases.append(case)
        print(json.dumps(case), flush=True)
        (output / "component-evidence.json").write_text(json.dumps({"schema_version": 1,
            "catalog_sha256": sha(args.packs / "component_catalog.json"), "cases": cases,
            "complete": False}, indent=2) + "\n")

    def install(cid):
        item = manager.catalog[cid]
        archive = args.packs / Path(urllib.parse.urlparse(item["url"]).path).name
        path = manager.install(cid, archive)
        manager.verify(cid)
        manager.enable(cid, False)
        assert manager.active_path(cid) is None
        manager.enable(cid)
        return {"version": item["version"], "sha256": item["sha256"], "path": str(path)}

    for cid in manager.catalog:
        check(f"install-verify-disable-enable:{cid}", lambda cid=cid: install(cid))

    def frames_test(name):
        path = fixtures / name
        before = sha(path)
        frame_colors = []
        for frame in range(3):
            pixels, metadata = decode_component(str(path), "viewer.animation-multipage", frame=frame, manager=manager)
            assert metadata["frame"] == frame and metadata["frame_count"] == 3
            frame_colors.append(tuple(pixels[:4]))
        expected = [(255, 0, 0, 255), (0, 0, 255, 255), (0, 128, 0, 255)]
        if name.endswith("apng"):
            expected = [(255, 0, 0, 255), (127, 0, 128, 255), (127, 128, 0, 255)]
        assert frame_colors == expected, (frame_colors, expected)
        assert before == sha(path)
        return {"fixture_sha256": before, "pixel_oracle": frame_colors}

    for name in ("animation.gif", "animation.webp", "pages.tiff", "blend.apng"):
        check(f"frame-disposal-blending:{name}", lambda name=name: frames_test(name))
    if args.codec_fixtures:
        for extension in ("heif", "heic", "avif"):
            def codec(extension=extension):
                path = args.codec_fixtures / f"orientation-alpha.{extension}"
                before = sha(path)
                pixels, metadata = decode_component(str(path), "codec.heif-avif", manager=manager)
                assert (metadata["width"], metadata["height"]) == (24, 32), metadata
                assert abs(pixels[0] - 255) <= 2 and pixels[1] <= 2 and pixels[2] <= 2
                assert pixels[3] == 128 and sha(path) == before
                return {"fixture_sha256": before, "dimensions": [24, 32], "alpha": pixels[3], "color_profile": "sRGB"}
            check(f"orientation-alpha-color:{extension}", codec)

    def inference(hardware):
        model = manager.active_path("ai.mobilenet-v2")
        assert model is not None
        result = infer_component(str(fixtures / "inference.jpg"), str(model), hardware=hardware, threshold=0., manager=manager)
        assert len(result["tags"]) == 10
        if hardware:
            assert result["provider"] == "CUDAExecutionProvider" and result["cuda_compute_events"] > 0, result
        return result

    check("actual-cpu-inference", lambda: inference(False))
    if args.require_cuda:
        check("actual-cuda-inference", lambda: inference(True))
    manager.enable("provider.onnx-nvidia", False) if "provider.onnx-nvidia" in manager.catalog else None
    def fallback():
        result = infer_component(str(fixtures / "inference.jpg"), str(manager.active_path("ai.mobilenet-v2")), hardware=True, threshold=0., manager=manager)
        assert result["provider"] == "CPUExecutionProvider" and result["fallback_reason"]
        return result
    check("explicit-cpu-fallback", fallback)
    if args.require_cuda:
        def provider_failure():
            import imagesorter.component_runtime as runtime
            manager.enable("provider.onnx-nvidia")
            previous = os.environ.get("CUDA_VISIBLE_DEVICES")
            run, calls = runtime._run, []
            def tracked(command, request, **kwargs):
                calls.append(request["component_id"])
                return run(command, request, **kwargs)
            try:
                os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
                runtime._run = tracked
                result = infer_component(str(fixtures / "inference.jpg"), str(manager.active_path("ai.mobilenet-v2")),
                                         hardware=True, threshold=0., manager=manager)
                assert result["provider"] == "CPUExecutionProvider" and result["fallback_reason"]
                assert calls == ["provider.onnx-nvidia", "core.cpu"], calls
                return {"receipt": result, "attempts": calls, "fault": "CUDA_VISIBLE_DEVICES=-1 in isolated readers"}
            finally:
                runtime._run = run
                if previous is None:
                    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
                else:
                    os.environ["CUDA_VISIBLE_DEVICES"] = previous
        check("failed-cuda-exactly-one-cpu-fallback", provider_failure)
    if args.raw_fixtures:
        try:
            raw = raw_fixtures(args.raw_fixtures, args.download_fixtures)
        except Exception as exc:
            def report_failure(error=exc):
                raise error
            check("raw-fixture-acquisition", report_failure)
            raw = []
        for item in raw:
            def decode_raw(item=item):
                pixels, metadata = decode_component(item["path"], "codec.camera-raw", target_size=(800, 800), manager=manager)
                assert pixels and metadata["frame_count"] == 1 and sha(item["path"]) == item["sha256"]
                return {**item, "width": metadata["width"], "height": metadata["height"],
                        "preview_source": metadata.get("preview_source"), "payload_sha256": metadata["payload_sha256"]}
            check(f"raw:{Path(item['path']).suffix}", decode_raw)
    if args.full_lifecycle:
        updates = output / "test-only-updates"
        updates.mkdir()
        original_catalog = list(manager.catalog.values())
        def lifecycle(cid):
            original = manager.catalog[cid]
            archive = args.packs / Path(urllib.parse.urlparse(original["url"]).path).name
            update_archive, updated = update_fixture(archive, original, updates)
            test_catalog = updates / (cid + "-catalog.json")
            test_catalog.write_text(json.dumps({"schema_version": 1, "components": [updated, *original_catalog]}, indent=2))
            test_manager = ComponentManager(manager.root, test_catalog)
            active = test_manager.active_path(cid)
            cancel = [False]
            try:
                test_manager.install(cid, update_archive, cancelled=lambda: cancel[0],
                                     progress=lambda value: cancel.__setitem__(0, value["state"] == "extracting"))
            except InterruptedError:
                pass
            else:
                raise AssertionError("Interrupted update incorrectly completed")
            assert test_manager.active_path(cid) == active
            corrupt = updates / (cid + "-corrupt.tar.gz")
            corrupt.write_bytes(b"intentionally corrupt update fixture")
            try:
                test_manager.install(cid, corrupt)
            except ComponentError:
                pass
            else:
                raise AssertionError("Corrupt update incorrectly completed")
            assert test_manager.active_path(cid) == active
            test_manager.install(cid, update_archive)
            assert test_manager.active_path(cid).name == updated["version"]
            test_manager.verify(cid)
            if cid in {"ai.mobilenet-v2", "provider.onnx-nvidia"}:
                used = infer_component(str(fixtures / "inference.jpg"), str(test_manager.active_path("ai.mobilenet-v2")),
                                       hardware=cid == "provider.onnx-nvidia", threshold=0., manager=test_manager)
                assert used["tags"]
            else:
                selected = (fixtures / "animation.gif" if cid == "viewer.animation-multipage" else
                            args.codec_fixtures / "orientation-alpha.heif" if cid == "codec.heif-avif" else
                            args.raw_fixtures / "fixture.cr2")
                pixels, used = decode_component(str(selected), cid, target_size=(800, 800), manager=test_manager)
                assert pixels and used["component_version"] == updated["version"]
            test_manager.rollback(cid)
            assert test_manager.active_path(cid).name == original["version"]
            test_manager.verify(cid)
            with test_manager.acquire(cid):
                try:
                    test_manager.remove(cid)
                except ComponentBusy:
                    pass
                else:
                    raise AssertionError("Removal ignored active reader lease")
            test_manager.remove(cid)
            assert test_manager.active_path(cid) is None
            assert not list((test_manager.root / cid).iterdir())
            manager.install(cid, archive)
            return {"original_version": original["version"], "original_sha256": original["sha256"],
                    "update_version": updated["version"], "update_sha256": updated["sha256"],
                    "test_catalog_sha256": sha(test_catalog), "same_binary_configuration_only_update": True,
                    "updated_version_use": used,
                    "asserted": ["cancel-preserves-active", "corrupt-preserves-active", "activate", "verify",
                                 "rollback", "lease-blocks-remove", "remove-all-versions", "reinstall-original"]}
        for cid in list(manager.catalog):
            check(f"complete-lifecycle:{cid}", lambda cid=cid: lifecycle(cid))
        check("verified-offline-legacy-model-import", lambda: {
            "path": str(manager.import_legacy_model(args.packs / "ai.mobilenet-v2/payload"))})
    return 0 if all(case["status"] == "passed" for case in cases) else 1


if __name__ == "__main__":
    sys.exit(main())

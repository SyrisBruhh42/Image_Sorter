"""Independent real RAW-pack decoding/lifecycle qualification in disposable storage.

All nine pinned fixture originals remain untouched. This is component evidence,
not a KDE/native application, reader-containment, or final clean-source approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import qualify_components as fixture_tools

RECIPE = Path(__file__).read_bytes()
FIXTURE_RECIPE = Path(fixture_tools.__file__).read_bytes()
COMPONENT = "codec.camera-raw"


def fingerprint(path: Path) -> dict:
    info = path.stat()
    return {
        "sha256": fixture_tools.sha(path),
        "size": info.st_size,
        "mode": info.st_mode,
        "mtime_ns": info.st_mtime_ns,
    }


def main() -> int:
    if not __debug__:
        raise SystemExit(
            "Qualification requires assertions enabled; do not use optimized Python"
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--raw-fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    from imagesorter.component_manager import (
        ComponentBusy,
        ComponentError,
        ComponentManager,
        clean_helper_environment,
    )
    from imagesorter.component_runtime import decode_component

    archive, catalog = args.archive.resolve(), args.catalog.resolve()
    if len(args.sha256) != 64 or fixture_tools.sha(archive) != args.sha256:
        raise ValueError("Pack does not match the requested immutable SHA-256")
    records = fixture_tools.raw_fixtures(args.raw_fixtures.resolve(), False)
    originals = {
        record["path"]: fingerprint(Path(record["path"])) for record in records
    }
    output = args.output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    (output / "qualify_raw_pack.py").write_bytes(RECIPE)
    (output / "qualify_components.py").write_bytes(FIXTURE_RECIPE)
    (output / "component_catalog.json").write_bytes(catalog.read_bytes())
    catalog = output / "component_catalog.json"
    client_sources = {}
    for name, loaded in sorted(sys.modules.items()):
        filename = getattr(loaded, "__file__", None)
        if name.startswith("imagesorter") and filename and filename.endswith(".py"):
            path = Path(filename).resolve()
            data = path.read_bytes()
            target = output / "client-source" / (name.replace(".", "/") + ".py")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            client_sources[str(path)] = hashlib.sha256(data).hexdigest()
    manager = ComponentManager(output / "profile/components", catalog)
    descriptor = manager.catalog[COMPONENT]
    if descriptor["sha256"] != args.sha256:
        raise ValueError(
            "Catalog's selected RAW descriptor differs from requested archive"
        )
    fixtures = output / "fixtures"
    fixtures.mkdir()
    for record in records:
        shutil.copyfile(record["path"], fixtures / Path(record["path"]).name)
    cases = []
    report = {
        "schema_version": 1,
        "kind": "raw-component-qualification",
        "component_id": COMPONENT,
        "component_version": descriptor["version"],
        "archive": {"path": str(archive), "sha256": args.sha256},
        "catalog": {"path": str(catalog), "sha256": fixture_tools.sha(catalog)},
        "recipe_sha256": hashlib.sha256(RECIPE).hexdigest(),
        "fixture_recipe_sha256": hashlib.sha256(FIXTURE_RECIPE).hexdigest(),
        "client_sources": client_sources,
        "original_fixtures_before": originals,
        "effective_profile_root": str(manager.root),
        "started_unix": time.time(),
        "scope": "nine real RAW previews and full raw rendering plus isolated install/update/rollback/lease/remove lifecycle",
        "not_claimed": [
            "KDE native UI",
            "reader OS write containment",
            "final clean-source artifact qualification",
            "distribution clearance",
        ],
        "cases": cases,
        "complete": False,
        "passed": False,
    }

    def save():
        (output / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )

    def case(name, action):
        started = time.monotonic()
        try:
            detail = action()
            row = {"id": name, "status": "passed", "detail": detail}
        except Exception as exc:
            row = {
                "id": name,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        row["elapsed_seconds"] = time.monotonic() - started
        cases.append(row)
        save()
        print(json.dumps(row), flush=True)

    def install():
        path = manager.install(COMPONENT, archive)
        manager.verify(COMPONENT)
        result = subprocess.run(
            [str(path / descriptor["entrypoint"]), "--probe"],
            cwd=output,
            env=clean_helper_environment(),
            timeout=30,
            check=True,
            capture_output=True,
        )
        assert len(result.stdout) < 65536 and len(result.stderr) < 1048576
        probe = json.loads(result.stdout)
        assert probe["libraw"] == [0, 22, 2] and probe["ready"] is True
        (output / "helper-probe.json").write_bytes(result.stdout)
        manager.enable(COMPONENT, False)
        assert manager.active_path(COMPONENT) is None
        try:
            decode_component(str(fixtures / "fixture.cr2"), COMPONENT, manager=manager)
        except ComponentError:
            pass
        else:
            raise AssertionError("Disabled RAW component was used")
        manager.enable(COMPONENT)
        restored = ComponentManager(manager.root, catalog)
        restored.verify(COMPONENT)
        assert restored.active_path(COMPONENT) == path
        return {
            "path": str(path),
            "disable_refuses_decode": True,
            "new_manager_reconciles_active": True,
            "probe": probe,
        }

    case("install-verify-disable-enable-reopen", install)

    def decode(extension, *, full=False):
        path = fixtures / f"fixture.{extension}"
        before = fingerprint(path)
        payload, detail = decode_component(
            str(path),
            COMPONENT,
            target_size=None if full else (800, 800),
            generation=list(fixture_tools.RAW_FIXTURES).index(extension) + 1,
            manager=manager,
        )
        assert detail["component_sha256"] == args.sha256
        assert detail["component_version"] == descriptor["version"]
        assert detail["frame_count"] == 1 and detail["frame"] == 0
        assert detail["pixel_format"] == "RGBA"
        assert len(payload) == detail["width"] * detail["height"] * 4
        assert hashlib.sha256(payload).hexdigest() == detail["payload_sha256"]
        assert all(alpha == 255 for alpha in payload[3::4])
        assert fingerprint(path) == before
        if full:
            assert detail["preview_source"] == "camera-white-balance-srgb"
            assert [detail["width"], detail["height"]] == detail["original_size"]
        else:
            assert 0 < detail["width"] <= 800 and 0 < detail["height"] <= 800
            repeated, second = decode_component(
                str(path), COMPONENT, target_size=(800, 800), manager=manager
            )
            assert (
                repeated == payload
                and second["payload_sha256"] == detail["payload_sha256"]
            )
        return {
            "fixture": {"path": str(path), **before},
            "metadata": detail,
            "deterministic_repeat": not full,
            "original_unchanged": True,
        }

    for extension in fixture_tools.RAW_FIXTURES:
        case("preview:" + extension, lambda extension=extension: decode(extension))
        case(
            "full-raw-render:" + extension,
            lambda extension=extension: decode(extension, full=True),
        )

    def invalid_input():
        path = fixtures / "invalid.cr2"
        path.write_bytes(b"This is deliberately not a supported RAW image.\n" * 256)
        before = fingerprint(path)
        try:
            decode_component(
                str(path), COMPONENT, target_size=(800, 800), manager=manager
            )
        except ComponentError as exc:
            assert "unsupported" in str(exc).lower()
            message = str(exc)
        else:
            raise AssertionError("Invalid RAW was accepted")
        assert fingerprint(path) == before
        return {"error": message, "unchanged": True}

    case("invalid-raw-clear-error", invalid_input)

    def lifecycle():
        updates = output / "test-only-updates"
        updates.mkdir()
        update_archive, updated = fixture_tools.update_fixture(
            archive, descriptor, updates
        )
        test_catalog = updates / "catalog.json"
        test_catalog.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "components": [updated, *manager.catalog.values()],
                },
                indent=2,
            )
        )
        testing = ComponentManager(manager.root, test_catalog)
        original = testing.active_path(COMPONENT)
        cancel = [False]
        try:
            testing.install(
                COMPONENT,
                update_archive,
                cancelled=lambda: cancel[0],
                progress=lambda value: cancel.__setitem__(
                    0, value["state"] == "extracting"
                ),
            )
        except InterruptedError:
            pass
        else:
            raise AssertionError("Cancelled update completed")
        assert testing.active_path(COMPONENT) == original
        corrupt = updates / "corrupt.tar.gz"
        corrupt.write_bytes(b"deliberately corrupt update")
        try:
            testing.install(COMPONENT, corrupt)
        except ComponentError:
            pass
        else:
            raise AssertionError("Corrupt update completed")
        assert testing.active_path(COMPONENT) == original
        testing.install(COMPONENT, update_archive)
        testing.verify(COMPONENT)
        assert testing.active_path(COMPONENT).name == updated["version"]
        _, used = decode_component(
            str(fixtures / "fixture.cr2"),
            COMPONENT,
            target_size=(800, 800),
            manager=testing,
        )
        assert (
            used["component_version"] == updated["version"]
            and used["component_sha256"] == updated["sha256"]
        )
        testing = ComponentManager(manager.root, test_catalog)
        testing.rollback(COMPONENT)
        assert testing.active_path(COMPONENT) == original
        testing.verify(COMPONENT)
        with testing.acquire(COMPONENT):
            try:
                testing.remove(COMPONENT)
            except ComponentBusy:
                pass
            else:
                raise AssertionError("Removal ignored an active reader lease")
        testing.remove(COMPONENT)
        assert testing.active_path(COMPONENT) is None
        assert not list((testing.root / COMPONENT).iterdir())
        # Installation selects the catalogue's newest descriptor; switch back
        # from the explicitly test-only update catalogue before reinstalling.
        restored = ComponentManager(manager.root, catalog)
        restored.install(COMPONENT, archive)
        restored.verify(COMPONENT)
        assert restored.active_path(COMPONENT).name == descriptor["version"]
        _, reinstalled = decode_component(
            str(fixtures / "fixture.cr2"),
            COMPONENT,
            target_size=(800, 800),
            manager=restored,
        )
        assert reinstalled["component_sha256"] == args.sha256
        return {
            "update_archive": {
                "path": str(update_archive),
                "sha256": updated["sha256"],
            },
            "test_only_same_binary_config_update": True,
            "new_version_used": used,
            "asserted": [
                "cancel-preserves-active",
                "corrupt-preserves-active",
                "update-activate",
                "new-version-decode",
                "new-manager-rollback",
                "lease-refuses-remove",
                "remove-all-versions",
                "reinstall-original-decode",
            ],
        }

    case("full-component-lifecycle", lifecycle)
    after = {path: fingerprint(Path(path)) for path in originals}
    report["original_fixtures_after"] = after
    report["original_fixtures_unchanged"] = after == originals
    report["client_sources_unchanged"] = all(
        fixture_tools.sha(Path(path)) == digest
        for path, digest in client_sources.items()
    )
    report["archive_unchanged"] = fixture_tools.sha(archive) == args.sha256
    report["finished_unix"] = time.time()
    report["complete"] = len(cases) == 21
    report["passed"] = (
        report["complete"]
        and report["original_fixtures_unchanged"]
        and report["client_sources_unchanged"]
        and report["archive_unchanged"]
        and all(row["status"] == "passed" for row in cases)
    )
    save()
    print(
        json.dumps(
            {
                "report": str(output / "report.json"),
                "passed": report["passed"],
                "cases": len(cases),
            }
        ),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

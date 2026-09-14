# Contributing

Image Sorter is Linux-first while preserving portable interfaces. Keep pull
requests focused, describe the user-visible behavior, and include regression tests
for changed contracts.

## Local setup

```bash
./scripts/jules_setup.sh
source .venv/bin/activate
./scripts/test_headless.sh -m "not packaging" -q tests
ruff check src tests scripts build_desktop.py
pip check
```

Tests must isolate all four XDG roots and operate only on temporary image fixtures.
Never point an automated test at a real photo library. Network access is disabled
for the ordinary suite; networked model verification belongs in the explicitly
optional integration job.

## Change requirements

- Preserve source files, sidecars, and provenance on every failure path.
- Add exactly one terminal result for each asynchronous request or operation.
- Keep optional model/codec/provider downloads opt-in and outside the package.
- Do not set process-global Qt, multiprocessing, or telemetry state during import.
- Report platform tests honestly. Offscreen or Xvfb results are not proof of native
  KDE, Windows, or macOS behavior.
- Update the README, architecture notes, and acceptance matrix when a support claim
  changes.

Run the packaging-marked test separately when changing the spec, build script,
resources, or entry point:

```bash
./scripts/test_headless.sh -m packaging -q tests/test_packaging.py
```

Release artifacts are accepted only when CI creates and verifies the wheel,
PyInstaller onedir build, AppImage, manifest, and checksums from the same source
SHA.

# Reproducible build inputs

`linux-x86_64-py312-build.lock` pins the complete tested Python environment for
the Ubuntu 24.04 / CPython 3.12 / Linux x86_64 build, including build and test tools.
It is **not** a runtime-only SBOM and must not be used on other platforms. The
application's runtime requirements remain in `pyproject.toml`.

Recreate it in a new private virtual environment, never in the running application:

```sh
python3.12 -m venv /absolute/private/build-environment
/absolute/private/build-environment/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements/locks/linux-x86_64-py312-build.lock
/absolute/private/build-environment/bin/python -m pip check
```

For offline builds, preserve the wheel inventory and use `--no-index
--find-links /absolute/preserved/wheels` with the same lock. Each lock entry
selects exactly the wheel captured by the build-input receipt; a different ABI
wheel must fail hash verification, not silently replace it.

Python, the host compiler and system libraries, build recipes, corresponding
source, final native-library inventories, and artifact hashes require separate
build/licensing receipts. A successful locked install alone does not qualify a
delivery. Optional packs have separate, complete locks and runtimes; GPU and codec
packages are not installed in the base environment. Changing any input requires
fresh qualification and an explicit catalog/build update.

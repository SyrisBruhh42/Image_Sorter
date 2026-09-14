# Developer & Agent Guidelines — Image Sorter

## Architecture & Code Layout
- Core application source code resides under `src/imagesorter/`.
- Tests reside under `tests/`.
- Entry points: `run_app.py` (PyInstaller / root launcher) and `imagesorter.main:main` (`imagesorter` CLI).
- Package imports within `src/imagesorter/*.py` use relative imports (e.g., `from .paths import get_config_dir`).
- Editable installs are preferred; repository test helpers also set `PYTHONPATH=src`.

## Execution & Test Environment
- **Environment Setup:** Execute `./scripts/jules_setup.sh` to initialize the `.venv` virtual environment and verify runtime dependencies.
- **Headless Testing:** Always use `./scripts/test_headless.sh` or set `PYTHONPATH=src QT_QPA_PLATFORM=offscreen` when executing `pytest`.
- **Test Commands:**
  - Standard Non-Packaging Suite: `./scripts/test_headless.sh -m "not packaging" -q tests/`
  - Packaging Smoke Test: `./scripts/test_headless.sh -m packaging -q tests/`
  - Ruff Linter: `source .venv/bin/activate && ruff check src tests scripts build_desktop.py`
  - Pip Dependency Check: `source .venv/bin/activate && pip check`

## Safety, Quality & Test Conventions
- **XDG Isolation:** Tests modifying application state or paths MUST use isolated temporary directories via `tmp_path` or `mktemp -d` and set `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME`.
- **Temporary Image Fixtures:** Never operate on non-temporary target images during tests. Create throwaway test image fixtures using `Pillow` or `pytest` `tmp_path` fixtures.
- **Original-File Preservation:** Destructive file operations must preserve file provenance and support transactional undo/rollback without modifying original source files outside designated temp directories.
- **Accurate Failure States:** Never suppress test errors, mock away failures unfaithfully, or alter test logic to disguise real product defects or missing functionality.
- **Honest Platform Evidence:** Explicitly report test platform configurations, headless Qt behavior, and exact command output in task documentation without exaggerating platform capabilities (e.g. native Plasma integration).

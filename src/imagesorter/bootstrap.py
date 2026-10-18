"""Resolve profile identity and worker role before application imports."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def configure_profile(path: str) -> Path:
    root = Path(path)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("--profile-root requires an absolute non-symlink directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = root.resolve(strict=True)
    os.environ["IMAGESORTER_PROFILE_ROOT"] = str(root)
    for category in ("CONFIG", "DATA", "CACHE", "STATE"):
        directory = root / category.lower()
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise ValueError("Profile directories must not be symbolic links")
        os.environ[f"XDG_{category}_HOME"] = str(directory)
    return root


def configure_runtime_environment() -> bool:
    """Contain application-controlled library state before native imports.

    This changes only this process and its children, not the desktop profile.
    OS-managed sockets and native OS caches still require platform observation.
    """
    from .paths import (
        _ensure_dir_or_fallback,
        get_cache_dir,
        get_config_dir,
        get_data_dir,
        get_logs_dir,
        get_portable_root,
        inherit_profile_environment,
    )
    inherit_profile_environment(os.environ)
    if not os.environ.get("IMAGESORTER_PROFILE_ROOT") and get_portable_root() is None:
        return False
    config, data, cache, logs = get_config_dir(), get_data_dir(), get_cache_dir(), get_logs_dir()
    scratch = _ensure_dir_or_fallback(cache / "temporary", "temporary")
    shaders = _ensure_dir_or_fallback(cache / "shaders", "shaders")
    # Qt's native file picker ignores these paths on some platforms. Isolated
    # profiles use our own picker; these also cover libraries honoring XDG/temp.
    os.environ.update({
        "XDG_CONFIG_HOME": str(config), "XDG_DATA_HOME": str(data),
        "XDG_CACHE_HOME": str(cache), "XDG_STATE_HOME": str(logs),
        "APPDATA": str(config), "LOCALAPPDATA": str(data),
        "TMPDIR": str(scratch), "TMP": str(scratch), "TEMP": str(scratch),
        "CUDA_CACHE_PATH": str(shaders), "__GL_SHADER_DISK_CACHE_PATH": str(shaders),
        "MESA_SHADER_CACHE_DIR": str(shaders), "QT_SHADER_CACHE_PATH": str(shaders),
    })
    # tempfile may have cached its location before a CLI profile was selected.
    tempfile.tempdir = str(scratch)
    return True

def entry(argv: list[str] | None = None) -> int:
    import multiprocessing
    multiprocessing.freeze_support()
    args = list(sys.argv if argv is None else argv)
    if "--profile-root" in args[1:]:
        index = args.index("--profile-root", 1)
        if index + 1 >= len(args):
            raise ValueError("--profile-root requires an absolute directory")
        configure_profile(args[index + 1])
        del args[index:index + 2]
    elif os.environ.get("IMAGESORTER_PROFILE_ROOT"):
        configure_profile(os.environ["IMAGESORTER_PROFILE_ROOT"])
    configure_runtime_environment()
    for flag, module in (("--reader-job", "reader_job"), ("--mutation-service", "mutation_service"), ("--component-job", "component_jobs"), ("--native-fixture-job", "native_scenarios")):
        if flag in args[1:]:
            import importlib
            index = args.index(flag, 1)
            return int(importlib.import_module(f"imagesorter.{module}").main(args[index + 1:]) or 0)
    from .main import main
    return main(args)

if __name__ == "__main__":
    raise SystemExit(entry())

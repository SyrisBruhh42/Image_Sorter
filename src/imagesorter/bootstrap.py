"""Resolve the explicit profile and worker role before application imports."""
from __future__ import annotations

import os
import sys
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
    for flag, module in (("--reader-job", "reader_job"), ("--mutation-service", "mutation_service"), ("--component-job", "component_jobs"), ("--native-fixture-job", "native_scenarios")):
        if flag in args[1:]:
            import importlib
            index = args.index(flag, 1)
            return int(importlib.import_module(f"imagesorter.{module}").main(args[index + 1:]) or 0)
    from .main import main
    return main(args)

if __name__ == "__main__":
    raise SystemExit(entry())

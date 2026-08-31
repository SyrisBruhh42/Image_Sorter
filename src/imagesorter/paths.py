from __future__ import annotations

import os
import sys
import tempfile
import warnings
from pathlib import Path


def get_app_dir() -> Path:
    """
    Determines the application root directory.

    Returns:
        Path: The root directory of the application executable or script.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller binary
        return Path(sys.executable).resolve().parent
    else:
        # Script execution
        main_script = sys.argv[0] if sys.argv and sys.argv[0] else __file__
        return Path(main_script).resolve().parent


def get_resource_dir() -> Path:
    """
    Determines the directory for bundled static assets/resources.

    Supports PyInstaller single-file bundle directory (_MEIPASS) when available,
    otherwise falls back to get_app_dir().

    Returns:
        Path: Path to static assets or package resources.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return get_app_dir()


def is_portable_mode() -> bool:
    """
    Checks if the application is running in portable mode.

    Portable mode is activated ONLY if 'portable.flag' exists
    adjacent to the executable or main script root directory.

    Returns:
        bool: True if portable mode is active, False otherwise.
    """
    return (get_app_dir() / "portable.flag").exists()


def _get_valid_env_path(var_name: str) -> Path | None:
    """
    Retrieves an environment variable value only if set, non-empty, and an absolute path.

    Args:
        var_name (str): Name of environment variable.

    Returns:
        Optional[Path]: Path object if variable is non-empty absolute path, else None.
    """
    val = os.environ.get(var_name)
    if val and os.path.isabs(val):
        return Path(val)
    return None


def _ensure_dir_or_fallback(target_dir: Path, category: str) -> Path:
    """
    Ensures that target_dir exists. If an OSError occurs, falls back to
    <tempdir>/ImageSorter/<category>. Emits a warning if preferred directory fails.

    Args:
        target_dir (Path): The preferred directory path.
        category (str): Category name ("config", "data", "cache", "logs").

    Returns:
        Path: Created directory path or fallback path.
    """
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir
    except OSError as e:
        warnings.warn(
            f"Failed to create preferred directory {target_dir}: {e}. "
            f"Falling back to temporary directory for category '{category}'.",
            RuntimeWarning,
            stacklevel=2,
        )
        fallback_dir = Path(tempfile.gettempdir()) / "ImageSorter" / category
        try:
            fallback_dir.mkdir(parents=True, exist_ok=True)
        except OSError as fallback_err:
            warnings.warn(
                f"Failed to create fallback directory {fallback_dir}: {fallback_err}.",
                RuntimeWarning,
                stacklevel=2,
            )
        return fallback_dir


def get_config_dir() -> Path:
    """
    Gets the configuration directory according to OS standards or Portable Mode.

    Returns:
        Path: Path to the configuration directory.
    """
    app_dir = get_app_dir()
    if is_portable_mode():
        config_dir = app_dir / "config"
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata and os.path.isabs(appdata):
            config_dir = Path(appdata) / "ImageSorter"
        else:
            config_dir = Path.home() / "AppData" / "Roaming" / "ImageSorter"
    elif sys.platform == "darwin":
        config_dir = Path.home() / "Library" / "Application Support" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_config = _get_valid_env_path("XDG_CONFIG_HOME")
        if xdg_config:
            config_dir = xdg_config / "ImageSorter"
        else:
            config_dir = Path.home() / ".config" / "ImageSorter"

    return _ensure_dir_or_fallback(config_dir, "config")


def get_data_dir() -> Path:
    """
    Gets the data directory according to OS standards or Portable Mode.

    Returns:
        Path: Path to the application data directory.
    """
    app_dir = get_app_dir()
    if is_portable_mode():
        data_dir = app_dir / "data"
    elif sys.platform.startswith("win"):
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata and os.path.isabs(local_appdata):
            data_dir = Path(local_appdata) / "ImageSorter" / "Data"
        else:
            data_dir = Path.home() / "AppData" / "Local" / "ImageSorter" / "Data"
    elif sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_data = _get_valid_env_path("XDG_DATA_HOME")
        if xdg_data:
            data_dir = xdg_data / "ImageSorter"
        else:
            data_dir = Path.home() / ".local" / "share" / "ImageSorter"

    return _ensure_dir_or_fallback(data_dir, "data")


def get_cache_dir() -> Path:
    """
    Gets the cache directory according to OS standards or Portable Mode.

    Returns:
        Path: Path to the cache directory.
    """
    app_dir = get_app_dir()
    if is_portable_mode():
        cache_dir = app_dir / "cache"
    elif sys.platform.startswith("win"):
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata and os.path.isabs(local_appdata):
            cache_dir = Path(local_appdata) / "ImageSorter" / "Cache"
        else:
            cache_dir = Path.home() / "AppData" / "Local" / "ImageSorter" / "Cache"
    elif sys.platform == "darwin":
        cache_dir = Path.home() / "Library" / "Caches" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_cache = _get_valid_env_path("XDG_CACHE_HOME")
        if xdg_cache:
            cache_dir = xdg_cache / "ImageSorter"
        else:
            cache_dir = Path.home() / ".cache" / "ImageSorter"

    return _ensure_dir_or_fallback(cache_dir, "cache")


def get_logs_dir() -> Path:
    """
    Gets the logs directory according to OS standards or Portable Mode.

    Returns:
        Path: Path to the log files directory.
    """
    app_dir = get_app_dir()
    if is_portable_mode():
        logs_dir = app_dir / "logs"
    elif sys.platform.startswith("win"):
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata and os.path.isabs(local_appdata):
            logs_dir = Path(local_appdata) / "ImageSorter" / "Logs"
        else:
            logs_dir = Path.home() / "AppData" / "Local" / "ImageSorter" / "Logs"
    elif sys.platform == "darwin":
        logs_dir = Path.home() / "Library" / "Logs" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_state = _get_valid_env_path("XDG_STATE_HOME")
        if xdg_state:
            logs_dir = xdg_state / "ImageSorter" / "logs"
        else:
            logs_dir = Path.home() / ".local" / "state" / "ImageSorter" / "logs"

    return _ensure_dir_or_fallback(logs_dir, "logs")


def get_settings_path() -> Path:
    """
    Gets the absolute path to the settings.json file.

    Returns:
        Path: Path to settings.json.
    """
    if is_portable_mode():
        return get_app_dir() / "settings.json"
    return get_config_dir() / "settings.json"

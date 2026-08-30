import os
import sys
from pathlib import Path
from typing import Union


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


def is_portable_mode() -> bool:
    """
    Checks if the application is running in portable mode.

    Portable mode is activated if 'portable.flag' or 'settings.json' exists
    adjacent to the executable or main script root directory.

    Returns:
        bool: True if portable mode is active, False otherwise.
    """
    app_dir = get_app_dir()
    portable_flag = app_dir / "portable.flag"
    local_settings = app_dir / "settings.json"
    return portable_flag.exists() or local_settings.exists()


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
        if appdata:
            config_dir = Path(appdata) / "ImageSorter"
        else:
            config_dir = Path.home() / "AppData" / "Roaming" / "ImageSorter"
    elif sys.platform == "darwin":
        config_dir = Path.home() / "Library" / "Application Support" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            config_dir = Path(xdg_config) / "ImageSorter"
        else:
            config_dir = Path.home() / ".config" / "ImageSorter"

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


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
        if local_appdata:
            data_dir = Path(local_appdata) / "ImageSorter" / "Data"
        else:
            data_dir = Path.home() / "AppData" / "Local" / "ImageSorter" / "Data"
    elif sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            data_dir = Path(xdg_data) / "ImageSorter"
        else:
            data_dir = Path.home() / ".local" / "share" / "ImageSorter"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


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
        if local_appdata:
            cache_dir = Path(local_appdata) / "ImageSorter" / "Cache"
        else:
            cache_dir = Path.home() / "AppData" / "Local" / "ImageSorter" / "Cache"
    elif sys.platform == "darwin":
        cache_dir = Path.home() / "Library" / "Caches" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            cache_dir = Path(xdg_cache) / "ImageSorter"
        else:
            cache_dir = Path.home() / ".cache" / "ImageSorter"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


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
        if local_appdata:
            logs_dir = Path(local_appdata) / "ImageSorter" / "Logs"
        else:
            logs_dir = Path.home() / "AppData" / "Local" / "ImageSorter" / "Logs"
    elif sys.platform == "darwin":
        logs_dir = Path.home() / "Library" / "Logs" / "ImageSorter"
    else:
        # Linux / POSIX XDG Spec
        xdg_state = os.environ.get("XDG_STATE_HOME")
        if xdg_state:
            logs_dir = Path(xdg_state) / "ImageSorter" / "logs"
        else:
            logs_dir = Path.home() / ".local" / "state" / "ImageSorter" / "logs"

    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_settings_path() -> Path:
    """
    Gets the absolute path to the settings.json file.

    Returns:
        Path: Path to settings.json.
    """
    if is_portable_mode():
        return get_app_dir() / "settings.json"
    return get_config_dir() / "settings.json"

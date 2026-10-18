from __future__ import annotations

import os
import stat
import sys
import tempfile
import uuid
import warnings
from collections.abc import MutableMapping
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
        return Path(sys._MEIPASS).resolve() / "imagesorter" / "resources"
    return Path(__file__).resolve().parent / "resources"


def _package_path(value: str, label: str, *, directory: bool) -> Path:
    """Reject unavailable or redirected package metadata before profile selection."""
    path = Path(value)
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise OSError("Expected an existing canonical absolute path")
        mode = path.stat().st_mode
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            raise OSError("Package path has the wrong file type")
    except (OSError, RuntimeError, ValueError) as error:
        raise OSError(f"Invalid {label} package identity: {value!r}") from error
    return path


def get_package_storage_root() -> Path:
    """Locate a package-adjacent portable.flag without changing the code root.

    AppImage runtime metadata selects the external artifact's directory; an
    extracted AppRun supplies its AppDir. A macOS bundle uses its containing
    directory. These paths are discovery locations, not an enabled profile.
    Metadata checks establish layout consistency, not package authenticity.
    """
    if not getattr(sys, "frozen", False):
        return get_app_dir()
    executable = Path(sys.executable)
    if sys.platform.startswith("linux") and ("APPIMAGE" in os.environ or "APPDIR" in os.environ):
        if "APPDIR" not in os.environ:
            raise OSError("Invalid AppImage package identity: APPDIR is missing")
        appdir = _package_path(os.environ["APPDIR"], "APPDIR", directory=True)
        launcher = _package_path(str(appdir / "AppRun"), "AppRun", directory=False)
        binary = _package_path(str(executable), "executable", directory=False)
        if binary != appdir / "usr" / "bin" / "ImageSorter" or not os.access(launcher, os.X_OK):
            raise OSError("Invalid APPDIR package identity: executable or AppRun does not match its layout")
        if "APPIMAGE" not in os.environ:
            return appdir
        artifact = _package_path(os.environ["APPIMAGE"], "APPIMAGE", directory=False)
        # A supplied ordinary file must not redirect the selected profile.
        # Full artifact authentication remains the separate release gate.
        fd = os.open(artifact, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("Invalid APPIMAGE package identity: artifact is not a regular file")
            header = os.read(fd, 11)
        finally:
            os.close(fd)
        if header[:4] != b"\x7fELF" or header[8:11] != b"AI\x02" or artifact.is_relative_to(appdir):
            raise OSError("Invalid APPIMAGE package identity: expected an external type2 AppImage")
        return artifact.parent
    if (sys.platform == "darwin" and executable.parent.name == "MacOS"
            and executable.parent.parent.name == "Contents"
            and executable.parent.parent.parent.suffix == ".app"):
        binary = _package_path(str(executable), "macOS executable", directory=False)
        return binary.parent.parent.parent.parent
    return get_app_dir()


def get_portable_root() -> Path | None:
    """Resolve the portable storage identity, independent of a worker's code path.

    The launcher discovers portable.flag; descendants inherit its canonical root.
    An invalid inherited identity must never select a different/native profile.
    An explicit profile takes precedence and retains its separate path layout.
    """
    if os.environ.get("IMAGESORTER_PROFILE_ROOT"):
        return None
    if "IMAGESORTER_PORTABLE_ROOT" in os.environ:
        root = Path(os.environ["IMAGESORTER_PORTABLE_ROOT"])
        try:
            if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
                raise OSError("Portable profile root must be an existing canonical directory")
        except (OSError, RuntimeError) as error:
            raise OSError(f"Portable profile identity is unavailable: {root}") from error
        return root
    root = get_package_storage_root()
    marker = root / "portable.flag"
    try:
        marker_mode = marker.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(marker_mode):
        raise OSError(f"Portable profile marker must be a regular non-symlink file: {marker}")
    return root


def is_portable_mode() -> bool:
    """Whether portable.flag or an inherited portable storage identity is active."""
    return get_portable_root() is not None


def inherit_profile_environment(environment: MutableMapping[str, str]) -> None:
    """Pin the current portable root in a copy of this process's environment."""
    root = get_portable_root()
    if root is not None:
        environment["IMAGESORTER_PORTABLE_ROOT"] = str(root)
    else:
        environment.pop("IMAGESORTER_PORTABLE_ROOT", None)


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


def _test_directory_writable(d: Path) -> bool:
    """Tests actual writability of directory d using a temporary test file."""
    test_file = d / f".write-test-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        fd = os.open(test_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except (OSError, PermissionError):
        return False
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except OSError:
            pass


def _isolated_profile() -> tuple[Path, str] | None:
    """Return the selected storage boundary without changing its layout."""
    if explicit_profile := os.environ.get("IMAGESORTER_PROFILE_ROOT"):
        return Path(explicit_profile), "Explicit"
    if root := get_portable_root():
        return root, "Portable"
    return None


def _validate_profile_path(target: Path, profile: tuple[Path, str] | None) -> None:
    if profile is None:
        return
    root, label = profile
    try:
        contained = target.resolve().is_relative_to(root.resolve())
    except RuntimeError as error:
        # Older supported Python versions report symlink loops as RuntimeError.
        raise OSError(f"{label} profile path cannot be resolved: {target}") from error
    if not contained:
        raise OSError(f"{label} profile path escapes root: {target}")


def _ensure_dir_or_fallback(target_dir: Path, category: str) -> Path:
    """
    Ensures that target_dir exists and is writable. If an OSError occurs or directory
    is unwritable, ordinary native mode falls back to a user-isolated directory:
    <tempdir>/imagesorter-<uid>/<category>. Emits a warning if preferred directory fails.
    Explicit and portable profiles refuse fallback and paths outside their root.

    Args:
        target_dir (Path): The preferred directory path.
        category (str): Category name ("config", "data", "cache", "logs").

    Returns:
        Path: Created and verified directory path or fallback path.
    """
    profile = _isolated_profile()
    _validate_profile_path(target_dir, profile)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if _test_directory_writable(target_dir):
            return target_dir
        raise OSError(f"Directory {target_dir} is not writable.")
    except OSError as e:
        if profile is not None:
            raise OSError(f"{profile[1]} profile is unavailable: {target_dir}") from e
        warnings.warn(
            f"Failed to verify or write to preferred directory {target_dir}: {e}. "
            f"Falling back to temporary directory for category '{category}'.",
            RuntimeWarning,
            stacklevel=2,
        )
        uid = os.getuid() if hasattr(os, "getuid") else os.getlogin()
        fallback_dir = Path(tempfile.gettempdir()) / f"imagesorter-{uid}" / category
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
    if profile := os.environ.get("IMAGESORTER_PROFILE_ROOT"):
        return _ensure_dir_or_fallback(Path(profile) / "config" / "ImageSorter", "config")
    if portable_root := get_portable_root():
        config_dir = portable_root / "config"
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
    if profile := os.environ.get("IMAGESORTER_PROFILE_ROOT"):
        return _ensure_dir_or_fallback(Path(profile) / "data" / "ImageSorter", "data")
    if portable_root := get_portable_root():
        data_dir = portable_root / "data"
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
    if profile := os.environ.get("IMAGESORTER_PROFILE_ROOT"):
        return _ensure_dir_or_fallback(Path(profile) / "cache" / "ImageSorter", "cache")
    if portable_root := get_portable_root():
        cache_dir = portable_root / "cache"
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
    if profile := os.environ.get("IMAGESORTER_PROFILE_ROOT"):
        return _ensure_dir_or_fallback(Path(profile) / "state" / "ImageSorter" / "logs", "logs")
    if portable_root := get_portable_root():
        logs_dir = portable_root / "logs"
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
    if portable_root := get_portable_root():
        app_dir = _ensure_dir_or_fallback(portable_root, "config")
        settings_path = app_dir / "settings.json"
        _validate_profile_path(settings_path, (app_dir, "Portable"))
        return settings_path
    return get_config_dir() / "settings.json"


def get_components_dir() -> Path:
    """Return the versioned, user-writable root for optional components."""
    return _ensure_dir_or_fallback(get_data_dir() / "components", "components")


def get_component_cache_dir() -> Path:
    """Return the staging root for incomplete optional-component downloads."""
    return _ensure_dir_or_fallback(
        get_cache_dir() / "component-downloads", "component-downloads"
    )

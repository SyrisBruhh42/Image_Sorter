import os
import sys
import platform
from typing import Optional

class PathManager:
    """
    Central path resolution engine.
    Zero-trust environment path resolver for both raw scripts and frozen PyInstaller binaries.
    """

    @staticmethod
    def is_frozen() -> bool:
        """Determines if the application is running as a frozen PyInstaller bundle."""
        return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')

    @staticmethod
    def get_base_dir() -> str:
        """
        Returns the directory containing the executable or main script.
        """
        if PathManager.is_frozen():
            return os.path.dirname(sys.executable)
        else:
            return os.path.dirname(os.path.abspath(sys.argv[0]))

    @staticmethod
    def is_portable_mode() -> bool:
        """
        Checks if the application should run in portable mode.
        True if 'portable.flag' or 'settings.json' exists in the base directory.
        """
        base_dir: str = PathManager.get_base_dir()
        if os.path.exists(os.path.join(base_dir, "portable.flag")):
            return True
        if os.path.exists(os.path.join(base_dir, "settings.json")):
            return True
        return False

    @staticmethod
    def get_app_data_dir() -> str:
        """
        Resolves the appropriate directory for persistent state.
        Uses portable directory if applicable, otherwise OS-specific AppData.
        """
        if PathManager.is_portable_mode():
            return PathManager.get_base_dir()

        system: str = platform.system()
        app_name: str = "ImageSorter"

        if system == "Windows":
            local_app_data: Optional[str] = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                return os.path.join(local_app_data, app_name)
            else:
                return os.path.join(os.path.expanduser("~"), "AppData", "Local", app_name)
        elif system == "Darwin":
            return os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_name)
        else: # Linux and others
            data_home: Optional[str] = os.environ.get("XDG_DATA_HOME")
            if data_home:
                return os.path.join(data_home, app_name)
            else:
                return os.path.join(os.path.expanduser("~"), ".local", "share", app_name)

    @staticmethod
    def get_bundled_assets_dir() -> str:
        """
        Returns the directory containing bundled read-only assets.
        If frozen, this is sys._MEIPASS. Otherwise, it's the directory of the script.
        """
        if PathManager.is_frozen():
            return getattr(sys, '_MEIPASS')
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _sanitize_path(path: str) -> str:
        """Normalizes and absolute-ifies a given path."""
        return os.path.abspath(os.path.normpath(path))

    @staticmethod
    def get_settings_path() -> str:
        """Returns the sanitized path to the settings file."""
        app_dir: str = PathManager.get_app_data_dir()
        os.makedirs(app_dir, exist_ok=True)
        return PathManager._sanitize_path(os.path.join(app_dir, "settings.json"))

    @staticmethod
    def get_log_path() -> str:
        """Returns the sanitized path to the log file."""
        app_dir: str = PathManager.get_app_data_dir()
        os.makedirs(app_dir, exist_ok=True)
        return PathManager._sanitize_path(os.path.join(app_dir, "imagesorter.log"))

    @staticmethod
    def get_models_dir() -> str:
        """Returns the sanitized path to the downloaded models directory."""
        app_dir: str = PathManager.get_app_data_dir()
        models_dir: str = os.path.join(app_dir, "models")
        os.makedirs(models_dir, exist_ok=True)
        return PathManager._sanitize_path(models_dir)

# Initialize global instance for easy access if needed, though mostly using static methods
paths = PathManager()

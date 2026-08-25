import json
import os
import tempfile
import time
import threading
from typing import Any, Dict, Optional
from src.logger import logger
from src.paths import PathManager

DEFAULT_SETTINGS: Dict[str, Any] = {
    "directories": {
        "source": "",
        "trash": ""
    },
    "hotkeys": {
        # Format: "key": {"folder": "path/to/folder", "action": "move|copy"}
    },
    "ui": {
        "fullscreen": False,
        "show_tags": True,
        "tooltips_enabled": True
    },
    "metadata": {
        "write_exif": True,
        "write_sidecar": False
    },
    "ai_tagger": {
        "enabled": False,
        "model_path": "models/model.onnx",
        "threshold": 0.5
    },
    "advanced": {
        "hardware_acceleration": True
    }
}

class SettingsManager:
    """
    Manages loading, saving, and accessing application settings.
    Implements atomic writes for zero-trust file integrity and antifragility against corruption.
    Uses PathManager for strict environment resolution.
    """

    def __init__(self, filepath: Optional[str] = None) -> None:
        """
        Initializes the SettingsManager.

        Args:
            filepath (Optional[str]): Override path to the settings JSON file. Defaults to PathManager resolution.
        """
        self.filepath: str = filepath if filepath else PathManager.get_settings_path()
        self.settings: Dict[str, Any] = DEFAULT_SETTINGS.copy()
        self._lock: threading.RLock = threading.RLock()
        self.load()

    def _backup_corrupt_file(self) -> None:
        """Creates a forensic copy of a corrupted settings file."""
        timestamp: str = str(int(time.time()))
        backup_path: str = f"{self.filepath}.corrupt.{timestamp}.bak"
        try:
            with open(self.filepath, 'r', encoding='utf-8') as src, open(backup_path, 'w', encoding='utf-8') as dst:
                dst.write(src.read())
            logger.error(f"Created forensic backup of corrupted settings at {backup_path}")
        except Exception as e:
            logger.error(f"Failed to create backup of corrupted settings: {e}")

    def load(self) -> None:
        """
        Loads settings from the JSON file, merging with default settings to ensure all required keys exist.
        Self-heals if corruption is detected by backing up and rewriting defaults.
        """
        with self._lock:
            if not os.path.exists(self.filepath):
                self.settings = DEFAULT_SETTINGS.copy()
                self.save()
                return

            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded: Any = json.load(f)

                    if not isinstance(loaded, dict):
                        raise ValueError("Root settings structure must be a JSON object (dict).")

                    # Self-Healing Merge
                    for key, val in DEFAULT_SETTINGS.items():
                        if key not in loaded:
                            loaded[key] = val
                        elif isinstance(val, dict) and isinstance(loaded[key], dict):
                             for sub_key, sub_val in val.items():
                                 if sub_key not in loaded[key]:
                                     loaded[key][sub_key] = sub_val

                    self.settings = loaded
                logger.info(f"Settings loaded successfully from {self.filepath}")

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Settings corruption detected: {e}. Initiating self-healing protocol.")
                self._backup_corrupt_file()

                # We could try to salvage valid sections here, but if the file is completely
                # mangled JSON decode will fail before we get dict access. If it decoded but
                # root is not dict, we reset. For simplicity, we fallback to fresh defaults.

                # Re-initialize clean settings and overwrite corrupted file
                self.settings = DEFAULT_SETTINGS.copy()
                self.save()

            except OSError as e:
                logger.error(f"Failed to read settings file {self.filepath}: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error loading settings: {e}")

    def save(self) -> None:
        """
        Saves the current settings to the JSON file using an atomic write process
        (write to temporary file, then atomic rename) to prevent file corruption during crashes.
        """
        with self._lock:
            try:
                dir_name: str = os.path.dirname(self.filepath) or "."
                os.makedirs(dir_name, exist_ok=True)
                fd: int
                temp_path: str
                fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="settings_", suffix=".tmp")

                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self.settings, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno()) # Ensure data is written to disk

                # Atomically replace the old settings file with the new one
                os.replace(temp_path, self.filepath)
                logger.debug(f"Settings saved atomically to {self.filepath}")

            except OSError as e:
                 logger.error(f"File system error saving settings to {self.filepath}: {e}")
                 if 'temp_path' in locals() and os.path.exists(temp_path):
                     try:
                         os.remove(temp_path)
                     except OSError:
                         pass
            except Exception as e:
                logger.error(f"Unexpected error saving settings: {e}")
                if 'temp_path' in locals() and os.path.exists(temp_path):
                     try:
                         os.remove(temp_path)
                     except OSError:
                         pass

    def get(self, section: str, key: Optional[str] = None) -> Any:
        """
        Retrieves a setting value.

        Args:
            section (str): The top-level category of the setting.
            key (Optional[str]): The specific setting key. If None, returns the entire section.

        Returns:
            Any: The value of the setting, or None if not found.
        """
        with self._lock:
            if key:
                return self.settings.get(section, {}).get(key)
            return self.settings.get(section)

    def set(self, section: str, key: str, value: Any) -> None:
        """
        Sets a specific setting value and immediately saves to disk.

        Args:
            section (str): The top-level category.
            key (str): The specific setting key.
            value (Any): The value to set.
        """
        with self._lock:
            if section not in self.settings:
                self.settings[section] = {}
            self.settings[section][key] = value
            self.save()

    def update_section(self, section: str, data: Dict[str, Any]) -> None:
        """
        Replaces an entire section of settings and saves to disk.

        Args:
            section (str): The top-level category.
            data (Dict[str, Any]): The new dictionary for the section.
        """
        with self._lock:
            if not isinstance(data, dict):
                logger.warning(f"Attempted to update section {section} with non-dictionary data.")
                return

            self.settings[section] = data
            self.save()

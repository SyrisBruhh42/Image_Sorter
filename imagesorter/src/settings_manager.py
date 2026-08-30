import json
import os
import tempfile
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from src.logger import logger
from src.paths import get_settings_path

DEFAULT_SETTINGS: Dict[str, Any] = {
    "directories": {
        "source": "",
        "trash": ""
    },
    "hotkeys": {},
    "ui": {
        "fullscreen": False,
        "show_tags": True,
        "tooltips_enabled": True,
        "theme": "Dark",
        "font_size": 24
    },
    "metadata": {
        "write_exif": True,
        "write_sidecar": False
    },
    "ai_tagger": {
        "enabled": False,
        "model_path": "models/mobilenetv2.onnx",
        "threshold": 0.5
    },
    "advanced": {
        "hardware_acceleration": True,
        "worker_threads": 2
    }
}


class SettingsManager:
    """
    Manages loading, saving, and accessing application settings.
    Implements thread-safe access with RLock, atomic writes for zero-trust file integrity,
    and self-healing backup on file corruption.
    """

    def __init__(self, filepath: Optional[str] = None) -> None:
        """
        Initializes the SettingsManager.

        Args:
            filepath (Optional[str]): Path to the settings JSON file. If None, resolved via paths.py.
        """
        self._lock = threading.RLock()
        if filepath is not None:
            self.filepath = os.path.normpath(filepath)
        else:
            self.filepath = str(get_settings_path())

        self.settings: Dict[str, Any] = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.load()

    def load(self) -> None:
        """
        Loads settings from JSON file, deep merging with default settings.
        If file is corrupt, backs it up to settings.corrupt.<timestamp>.bak and restores default settings.
        """
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)

                    if not isinstance(loaded, dict):
                        raise ValueError("Settings file must contain a JSON object.")

                    self.settings = self._deep_merge(DEFAULT_SETTINGS, loaded)
                    logger.info(f"Settings loaded successfully from {self.filepath}")

                except (json.JSONDecodeError, ValueError) as e:
                    timestamp = int(time.time())
                    bak_path = f"{self.filepath}.corrupt.{timestamp}.bak"
                    logger.error(f"Settings file corrupted: {e}. Backing up to {bak_path} and resetting to default.")
                    try:
                        os.replace(self.filepath, bak_path)
                    except OSError as backup_err:
                        logger.error(f"Failed to backup corrupt settings file: {backup_err}")
                    self.settings = json.loads(json.dumps(DEFAULT_SETTINGS))
                    self.save()

                except OSError as e:
                    logger.error(f"Failed to read settings file {self.filepath}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error loading settings: {e}")
            else:
                self.save()

    def _deep_merge(self, default_dict: Dict[str, Any], user_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Deeply merges user settings into default settings without mutating default_dict."""
        result = json.loads(json.dumps(default_dict))
        for key, value in user_dict.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def save(self) -> None:
        """
        Saves current settings using an atomic write process (tempfile + flush + fsync + os.replace).
        """
        with self._lock:
            temp_path: Optional[str] = None
            try:
                dir_name = os.path.dirname(self.filepath) or "."
                os.makedirs(dir_name, exist_ok=True)

                fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="settings_", suffix=".tmp")
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self.settings, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(temp_path, self.filepath)
                logger.debug(f"Settings saved atomically to {self.filepath}")

            except OSError as e:
                logger.error(f"File system error saving settings to {self.filepath}: {e}")
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
            except Exception as e:
                logger.error(f"Unexpected error saving settings: {e}")
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

    def get(self, section: str, key: Optional[str] = None) -> Any:
        """Retrieves a setting value safely under lock."""
        with self._lock:
            if key:
                sec = self.settings.get(section, {})
                return sec.get(key) if isinstance(sec, dict) else None
            return self.settings.get(section)

    def set(self, section: str, key: str, value: Any) -> None:
        """Sets a setting value under lock and saves to disk."""
        with self._lock:
            if section not in self.settings or not isinstance(self.settings[section], dict):
                self.settings[section] = {}
            self.settings[section][key] = value
            self.save()

    def update_section(self, section: str, data: Dict[str, Any]) -> None:
        """Replaces an entire section of settings and saves to disk."""
        with self._lock:
            if not isinstance(data, dict):
                logger.warning(f"Attempted to update section {section} with non-dictionary data.")
                return
            self.settings[section] = data
            self.save()

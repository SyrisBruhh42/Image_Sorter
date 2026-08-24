import json
import os
import tempfile
import shutil
from typing import Any, Dict, Optional
from src.logger import logger

SETTINGS_FILE = "settings.json"

DEFAULT_SETTINGS = {
    "directories": {
        "source": "",
        "trash": ""
    },
    "hotkeys": {
        # Format: "key": {"folder": "path/to/folder", "action": "move|copy"}
    },
    "ui": {
        "fullscreen": False,
        "show_tags": True
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
    """

    def __init__(self, filepath: str = SETTINGS_FILE) -> None:
        """
        Initializes the SettingsManager.

        Args:
            filepath (str): Path to the settings JSON file.
        """
        self.filepath: str = filepath
        self.settings: Dict[str, Any] = DEFAULT_SETTINGS.copy()
        self.load()

    def load(self) -> None:
        """
        Loads settings from the JSON file, merging with default settings to ensure all required keys exist.
        Handles errors gracefully to prevent application crashes on invalid config.
        """
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    if not isinstance(loaded, dict):
                        raise ValueError("Settings file must contain a JSON object.")

                    # Merge to ensure new keys exist
                    for key, val in DEFAULT_SETTINGS.items():
                        if key not in loaded:
                            loaded[key] = val
                        elif isinstance(val, dict) and isinstance(loaded[key], dict):
                            # Deep merge for nested dictionaries (e.g., ui, directories)
                             for sub_key, sub_val in val.items():
                                 if sub_key not in loaded[key]:
                                     loaded[key][sub_key] = sub_val

                    self.settings = loaded
                logger.info(f"Settings loaded successfully from {self.filepath}")
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Settings file corrupted or invalid format: {e}. Using default settings.")
            except OSError as e:
                logger.error(f"Failed to read settings file {self.filepath}: {e}")
            except Exception as e:
                 logger.error(f"Unexpected error loading settings: {e}")

    def save(self) -> None:
        """
        Saves the current settings to the JSON file using an atomic write process
        (write to temporary file, then atomic rename) to prevent file corruption during crashes.
        """
        try:
            # Create a temporary file in the same directory to ensure atomic rename works
            dir_name = os.path.dirname(self.filepath) or "."
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
        if not isinstance(data, dict):
            logger.warning(f"Attempted to update section {section} with non-dictionary data.")
            return

        self.settings[section] = data
        self.save()

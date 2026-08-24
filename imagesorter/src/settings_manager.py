import json
import os

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
    def __init__(self, filepath=SETTINGS_FILE):
        self.filepath = filepath
        self.settings = DEFAULT_SETTINGS.copy()
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # Simple merge to ensure new keys exist
                    for key, val in DEFAULT_SETTINGS.items():
                        if key not in loaded:
                            loaded[key] = val
                    self.settings = loaded
            except Exception as e:
                print(f"Error loading settings: {e}")

    def save(self):
        import tempfile
        try:
            fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.filepath)), text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
            os.replace(temp_path, self.filepath)
        except Exception as e:
            import logging
            logging.error(f"Error saving settings atomically: {e}")

    def get(self, section, key=None):
        if key:
            return self.settings.get(section, {}).get(key)
        return self.settings.get(section)

    def set(self, section, key, value):
        if section not in self.settings:
            self.settings[section] = {}
        self.settings[section][key] = value
        self.save()

    def update_section(self, section, data):
        self.settings[section] = data
        self.save()

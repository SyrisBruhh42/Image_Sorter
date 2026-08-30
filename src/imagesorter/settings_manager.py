import json
import math
import os
import tempfile
import time
import uuid
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from .logger import logger
from .paths import get_settings_path

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


def _is_valid_path_str(val: Any) -> bool:
    """Checks if a value is a string without control characters."""
    if not isinstance(val, str):
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in val)


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
        Loads settings from JSON file, validates and repairs settings, preserving unknown fields.
        If file is corrupt, backs it up to settings.json.corrupt.<time_ns>.<uuid>.bak safely and restores defaults.
        """
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)

                    if not isinstance(loaded, dict):
                        raise ValueError("Settings file must contain a JSON object.")

                    repaired_settings, was_repaired = self._validate_and_repair(loaded)
                    self.settings = repaired_settings
                    logger.info(f"Settings loaded successfully from {self.filepath}")
                    if was_repaired:
                        self.save()

                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(f"Settings file corrupted: {e}.")
                    backup_success = self._backup_corrupt_file()
                    self.settings = json.loads(json.dumps(DEFAULT_SETTINGS))
                    if backup_success:
                        self.save()

                except OSError as e:
                    logger.error(f"Failed to read settings file {self.filepath}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error loading settings: {e}")
            else:
                self.save()

    def _backup_corrupt_file(self) -> bool:
        """
        Creates a collision-safe backup of the corrupt settings file.
        Returns True if backup succeeded, False if all backup attempts failed.
        """
        dir_name = os.path.dirname(self.filepath) or "."
        base_name = os.path.basename(self.filepath)

        for _ in range(10):
            time_ns = time.time_ns()
            uid = uuid.uuid4().hex[:8]
            bak_name = f"{base_name}.corrupt.{time_ns}.{uid}.bak"
            bak_path = os.path.join(dir_name, bak_name)

            if os.path.exists(bak_path):
                continue

            try:
                os.replace(self.filepath, bak_path)
                logger.error(f"Corrupt settings backed up to {bak_path}")
                return True
            except OSError:
                # Fallback to exclusive file creation copy
                try:
                    fd = os.open(bak_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, 'wb') as dst:
                        with open(self.filepath, 'rb') as src:
                            dst.write(src.read())
                            dst.flush()
                            os.fsync(dst.fileno())
                    try:
                        os.remove(self.filepath)
                    except OSError:
                        pass
                    logger.error(f"Corrupt settings copied to {bak_path}")
                    return True
                except OSError:
                    continue

        logger.error(f"Failed to backup corrupt settings file {self.filepath} after multiple attempts.")
        return False

    def _validate_and_repair(self, user_dict: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        """
        Validates user_dict against schema rules, replacing invalid sections/fields with safe defaults,
        clamping numeric values, and preserving unknown fields.
        Returns (repaired_dict, was_modified).
        """
        was_modified = False
        result = json.loads(json.dumps(DEFAULT_SETTINGS))

        # Copy unknown root keys
        for key, val in user_dict.items():
            if key not in DEFAULT_SETTINGS:
                result[key] = val

        # 1. Directories
        dirs_user = user_dict.get("directories")
        if isinstance(dirs_user, dict):
            for k, v in dirs_user.items():
                if k not in ["source", "trash"]:
                    result["directories"][k] = v
            # source
            src_val = dirs_user.get("source")
            if _is_valid_path_str(src_val):
                norm_src = os.path.normpath(src_val) if src_val else ""
                result["directories"]["source"] = norm_src
                if norm_src != src_val:
                    was_modified = True
            else:
                if src_val != "":
                    was_modified = True
                result["directories"]["source"] = ""
            # trash
            tr_val = dirs_user.get("trash")
            if _is_valid_path_str(tr_val):
                norm_tr = os.path.normpath(tr_val) if tr_val else ""
                result["directories"]["trash"] = norm_tr
                if norm_tr != tr_val:
                    was_modified = True
            else:
                if tr_val != "":
                    was_modified = True
                result["directories"]["trash"] = ""
        else:
            if "directories" in user_dict:
                was_modified = True

        # 2. UI
        ui_user = user_dict.get("ui")
        if isinstance(ui_user, dict):
            for k, v in ui_user.items():
                if k not in ["fullscreen", "show_tags", "tooltips_enabled", "theme", "font_size"]:
                    result["ui"][k] = v
            # fullscreen
            fs = ui_user.get("fullscreen")
            if isinstance(fs, bool):
                result["ui"]["fullscreen"] = fs
            else:
                if "fullscreen" in ui_user:
                    was_modified = True
            # show_tags
            st = ui_user.get("show_tags")
            if isinstance(st, bool):
                result["ui"]["show_tags"] = st
            else:
                if "show_tags" in ui_user:
                    was_modified = True
            # tooltips_enabled
            te = ui_user.get("tooltips_enabled")
            if isinstance(te, bool):
                result["ui"]["tooltips_enabled"] = te
            else:
                if "tooltips_enabled" in ui_user:
                    was_modified = True
            # theme
            th = ui_user.get("theme")
            if isinstance(th, str):
                result["ui"]["theme"] = th
            else:
                if "theme" in ui_user:
                    was_modified = True
            # font_size (12-72, strict int)
            fsz = ui_user.get("font_size")
            if isinstance(fsz, int) and not isinstance(fsz, bool):
                clamped_fsz = max(12, min(72, fsz))
                result["ui"]["font_size"] = clamped_fsz
                if clamped_fsz != fsz:
                    was_modified = True
            else:
                if "font_size" in ui_user:
                    was_modified = True
        else:
            if "ui" in user_dict:
                was_modified = True

        # 3. Metadata
        meta_user = user_dict.get("metadata")
        if isinstance(meta_user, dict):
            for k, v in meta_user.items():
                if k not in ["write_exif", "write_sidecar"]:
                    result["metadata"][k] = v
            we = meta_user.get("write_exif")
            if isinstance(we, bool):
                result["metadata"]["write_exif"] = we
            else:
                if "write_exif" in meta_user:
                    was_modified = True
            ws = meta_user.get("write_sidecar")
            if isinstance(ws, bool):
                result["metadata"]["write_sidecar"] = ws
            else:
                if "write_sidecar" in meta_user:
                    was_modified = True
        else:
            if "metadata" in user_dict:
                was_modified = True

        # 4. AI Tagger
        ai_user = user_dict.get("ai_tagger")
        if isinstance(ai_user, dict):
            for k, v in ai_user.items():
                if k not in ["enabled", "model_path", "threshold"]:
                    result["ai_tagger"][k] = v
            en = ai_user.get("enabled")
            if isinstance(en, bool):
                result["ai_tagger"]["enabled"] = en
            else:
                if "enabled" in ai_user:
                    was_modified = True
            mp = ai_user.get("model_path")
            if _is_valid_path_str(mp):
                norm_mp = os.path.normpath(mp) if mp else ""
                result["ai_tagger"]["model_path"] = norm_mp
                if norm_mp != mp:
                    was_modified = True
            else:
                if "model_path" in ai_user:
                    was_modified = True
            th = ai_user.get("threshold")
            if isinstance(th, (int, float)) and not isinstance(th, bool) and math.isfinite(float(th)):
                clamped_th = max(0.0, min(1.0, float(th)))
                result["ai_tagger"]["threshold"] = clamped_th
                if clamped_th != th:
                    was_modified = True
            else:
                if "threshold" in ai_user:
                    was_modified = True
        else:
            if "ai_tagger" in user_dict:
                was_modified = True

        # 5. Advanced
        adv_user = user_dict.get("advanced")
        if isinstance(adv_user, dict):
            for k, v in adv_user.items():
                if k not in ["hardware_acceleration", "worker_threads", "cache_size_mb"]:
                    result["advanced"][k] = v
            ha = adv_user.get("hardware_acceleration")
            if isinstance(ha, bool):
                result["advanced"]["hardware_acceleration"] = ha
            else:
                if "hardware_acceleration" in adv_user:
                    was_modified = True
            wt = adv_user.get("worker_threads")
            if isinstance(wt, int) and not isinstance(wt, bool):
                clamped_wt = max(1, min(32, wt))
                result["advanced"]["worker_threads"] = clamped_wt
                if clamped_wt != wt:
                    was_modified = True
            else:
                if "worker_threads" in adv_user:
                    was_modified = True

            if "cache_size_mb" in adv_user:
                cs = adv_user.get("cache_size_mb")
                if isinstance(cs, int) and not isinstance(cs, bool) and cs >= 1:
                    result["advanced"]["cache_size_mb"] = cs
                else:
                    was_modified = True
        else:
            if "advanced" in user_dict:
                was_modified = True

        # 6. Hotkeys
        hk_user = user_dict.get("hotkeys")
        norm_hotkeys: Dict[str, Dict[str, Any]] = {}
        if isinstance(hk_user, dict):
            for raw_k, hk_item in hk_user.items():
                if not isinstance(raw_k, str):
                    was_modified = True
                    continue
                k_clean = raw_k.strip()
                if not k_clean or k_clean in norm_hotkeys:
                    was_modified = True
                    continue

                if not isinstance(hk_item, dict):
                    was_modified = True
                    continue

                item_dict = dict(hk_item)
                # action
                act = item_dict.get("action")
                if not isinstance(act, str):
                    item_dict["action"] = "move"
                    was_modified = True
                # folder
                fld = item_dict.get("folder")
                if _is_valid_path_str(fld):
                    norm_fld = os.path.normpath(fld) if fld else ""
                    item_dict["folder"] = norm_fld
                    if norm_fld != fld:
                        was_modified = True
                else:
                    if "folder" in item_dict and item_dict["folder"] != "":
                        was_modified = True
                    item_dict["folder"] = ""
                # auto_advance
                aa = item_dict.get("auto_advance")
                if not isinstance(aa, bool):
                    item_dict["auto_advance"] = True
                    was_modified = True

                norm_hotkeys[k_clean] = item_dict
            result["hotkeys"] = norm_hotkeys
        else:
            result["hotkeys"] = {}
            if "hotkeys" in user_dict:
                was_modified = True

        return result, was_modified

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

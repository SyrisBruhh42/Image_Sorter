import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
import tempfile
import json
from pathlib import Path
from src.settings_manager import SettingsManager
from src.logger import logger

@pytest.fixture
def mock_temp_dir(tmp_path):
    """Fixture to provide a temporary directory."""
    return tmp_path

@pytest.fixture
def mock_images(tmp_path):
    """Fixture to create fake image files."""
    images = []
    for ext in ["jpg", "png", "webp"]:
        img_path = tmp_path / f"test_image.{ext}"
        img_path.write_bytes(b"fake image data")
        images.append(str(img_path))
    return images

@pytest.fixture
def read_only_dir(tmp_path):
    """Fixture to create a read-only directory."""
    ro_dir = tmp_path / "read_only"
    ro_dir.mkdir()
    os.chmod(str(ro_dir), 0o444)
    yield ro_dir
    os.chmod(str(ro_dir), 0o777)

@pytest.fixture
def mock_settings(tmp_path):
    """Fixture to create an isolated SettingsManager."""
    settings_file = tmp_path / "settings.json"
    return SettingsManager(filepath=str(settings_file))

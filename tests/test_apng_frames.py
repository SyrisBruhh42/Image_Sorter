from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from imagesorter.apng_frames import decode_apng

_spec = importlib.util.spec_from_file_location("component_qualification", Path(__file__).parents[1] / "scripts/qualify_components.py")
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)


def test_source_over_keeps_opaque_background_and_restores_previous(tmp_path):
    path = tmp_path / "fixture.apng"
    _fixtures.make_apng(path)
    expected = [(255, 0, 0, 255), (127, 0, 128, 255), (127, 128, 0, 255)]
    for index, color in enumerate(expected):
        image, count, duration, loop = decode_apng(path, index)
        assert image.getpixel((0, 0)) == color
        assert image.getpixel((7, 7)) == color
        assert count == 3 and duration == 100 and loop == 2


def test_apng_crc_failure_and_invalid_frame_are_rejected(tmp_path):
    path = tmp_path / "fixture.apng"
    _fixtures.make_apng(path)
    with pytest.raises(ValueError, match="request/count"):
        decode_apng(path, 3)
    data = bytearray(path.read_bytes())
    data[-5] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValueError, match="checksum"):
        decode_apng(path, 0)

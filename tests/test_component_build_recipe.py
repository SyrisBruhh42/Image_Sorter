"""Exclude unused console/GUI code before native helper dependency analysis."""
from pathlib import Path

import pytest

from scripts.build_component_packs import COLLECT, helper_freeze_command


@pytest.mark.parametrize("component_id", sorted(COLLECT))
def test_only_noninteractive_isolated_helper_dependencies(component_id):
    command = helper_freeze_command("/runtime/python", Path("/job"), Path("/captured"), component_id)
    excluded = [command[n + 1] for n, item in enumerate(command) if item == "--exclude-module"]
    assert excluded == ["PyQt6", "readline"]
    assert command[-1] == "/captured/src/imagesorter/component_worker.py"
    assert [command[n + 1] for n, item in enumerate(command) if item == "--collect-all"] == COLLECT[component_id]


def test_model_data_pack_has_no_executable_environment():
    with pytest.raises(ValueError, match="Only executable"):
        helper_freeze_command("unused", Path("/job"), Path("/captured"), "ai.mobilenet-v2")

from __future__ import annotations

from imagesorter.components import (
    CATALOG,
    ComponentType,
    component_for_extension,
    component_install_dir,
    component_staging_dir,
    unsupported_format_message,
)


def test_catalog_has_stable_unique_ids_and_distinct_types():
    identifiers = [item.component_id for item in CATALOG]
    assert len(identifiers) == len(set(identifiers))
    assert {item.component_type for item in CATALOG} == set(ComponentType)
    assert all(item.optional for item in CATALOG)


def test_component_paths_separate_versions_from_incomplete_downloads(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    installed = component_install_dir("codec.heif-avif", "1.2.3")
    staging = component_staging_dir("codec.heif-avif")

    assert str(installed).startswith(str(tmp_path / "data"))
    assert str(staging).startswith(str(tmp_path / "cache"))
    assert installed.name == "1.2.3"
    assert installed != staging


def test_optional_format_diagnostic_names_the_required_pack():
    descriptor = component_for_extension(".heic")
    assert descriptor is not None
    assert descriptor.component_id == "codec.heif-avif"
    message = unsupported_format_message("heic")
    assert "optional" in message
    assert descriptor.display_name in message

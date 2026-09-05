import json
from pathlib import Path

import allin1_sdk.native_toolchain_settings as preferences
from allin1_sdk.story_axle_runtime_builder import NativeAxleToolchainSettings


def test_native_toolchain_settings_are_private_per_user(monkeypatch, tmp_path) -> None:
    state = tmp_path / "Local State" / "ALLIN1-SDK"
    monkeypatch.setattr(preferences, "user_data_root", lambda: state)

    assert preferences.native_toolchain_settings_path() == (
        state / "story-axle-toolchain.json"
    )
    assert preferences.load_native_toolchain_settings() == (
        NativeAxleToolchainSettings()
    )


def test_native_toolchain_settings_round_trip_manual_paths_with_spaces(
    tmp_path,
) -> None:
    destination = tmp_path / "private" / "toolchain.json"
    settings = NativeAxleToolchainSettings(
        mode="manual",
        cmake_path=Path(r"C:\Program Files\CMake\bin\cmake.exe"),
        ctest_path=Path(r"C:\Program Files\CMake\bin\ctest.exe"),
        visual_studio_path=Path(
            r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools"
        ),
    )

    written = preferences.save_native_toolchain_settings(settings, destination)

    assert written == destination
    assert preferences.load_native_toolchain_settings(destination) == settings
    payload = json.loads(destination.read_text("utf-8"))
    assert payload == {
        "schema_version": 1,
        "mode": "manual",
        "cmake_path": str(settings.cmake_path),
        "ctest_path": str(settings.ctest_path),
        "visual_studio_path": str(settings.visual_studio_path),
    }
    assert not list(destination.parent.glob("*.tmp"))


def test_invalid_explicit_paths_are_preserved_for_actionable_preflight(
    tmp_path,
) -> None:
    destination = tmp_path / "toolchain.json"
    destination.write_text(
        json.dumps({
            "schema_version": 1,
            "mode": "manual",
            "cmake_path": r"C:\Missing Tools\cmake.exe",
            "ctest_path": r"C:\Missing Tools\ctest.exe",
            "visual_studio_path": r"C:\Missing Build Tools",
        }),
        encoding="utf-8",
    )

    loaded = preferences.load_native_toolchain_settings(destination)

    assert loaded.mode == "manual"
    assert str(loaded.cmake_path) == r"C:\Missing Tools\cmake.exe"
    assert str(loaded.ctest_path) == r"C:\Missing Tools\ctest.exe"
    assert str(loaded.visual_studio_path) == r"C:\Missing Build Tools"


def test_corrupt_settings_fall_back_and_reset_clears_overrides(tmp_path) -> None:
    destination = tmp_path / "toolchain.json"
    destination.write_text("not-json", encoding="utf-8")
    assert preferences.load_native_toolchain_settings(destination) == (
        NativeAxleToolchainSettings()
    )

    preferences.save_native_toolchain_settings(
        NativeAxleToolchainSettings(
            mode="manual", cmake_path=Path("missing-cmake.exe"),
        ),
        destination,
    )
    reset = preferences.reset_native_toolchain_settings(destination)

    assert reset == NativeAxleToolchainSettings()
    assert preferences.load_native_toolchain_settings(destination) == reset

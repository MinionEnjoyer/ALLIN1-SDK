from __future__ import annotations

from pathlib import Path

from allin1_sdk.ui_foundation import fitted_geometry, shell_status_presentation


def test_fitted_geometry_centers_preferred_window_inside_desktop():
    width, height, x, y = fitted_geometry(1320, 840, 1920, 1080)

    assert (width, height) == (1320, 840)
    assert x == 300
    assert y == 92
    assert x + width <= 1920
    assert y + height <= 1080 - 56


def test_fitted_geometry_clamps_to_small_desktop_without_overflow():
    width, height, x, y = fitted_geometry(1320, 840, 800, 600)

    assert (width, height) == (752, 520)
    assert (x, y) == (24, 12)
    assert x + width <= 800
    assert y + height <= 600 - 56


def test_fitted_geometry_never_exceeds_extremely_small_reported_screen():
    width, height, x, y = fitted_geometry(1320, 840, 40, 40)

    assert 1 <= width <= 40
    assert 1 <= height <= 40
    assert x >= 0
    assert y >= 0
    assert x + width <= 40
    assert y + height <= 40


def test_shell_status_presentation_distinguishes_progress_and_outcomes():
    assert shell_status_presentation("Inspecting package RPFs…").tone == "busy"
    assert shell_status_presentation("Package RPF reports written").tone == "success"
    assert shell_status_presentation("Package-folder audit failed").tone == "error"
    assert shell_status_presentation("No packages match filters").tone == "warning"
    assert shell_status_presentation(
        "Compiled 2 vehicles: 0 errors, 0 warnings",
    ).tone == "success"
    assert shell_status_presentation(
        "Compiled 2 vehicles: 1 error, 0 warnings",
    ).tone == "error"
    assert shell_status_presentation(
        "Compiled 12 vehicles: 10 errors, 0 warnings",
    ).tone == "error"


def test_sdk_windows_use_shared_screen_safe_placement():
    source_root = Path(__file__).parents[1] / "src" / "allin1_sdk"
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        if path.name == "ui_foundation.py":
            continue
        source = path.read_text(encoding="utf-8")
        if ".geometry(" in source or ".minsize(" in source:
            offenders.append(path.name)

    assert offenders == [], (
        "SDK windows must use ui_foundation.place_window instead of hard-coded "
        f"geometry/minimum pairs: {', '.join(sorted(offenders))}"
    )

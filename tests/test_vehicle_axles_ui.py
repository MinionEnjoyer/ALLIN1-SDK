from __future__ import annotations

import tkinter as tk
from types import SimpleNamespace

from dataclasses import dataclass, replace

import pytest

from allin1_sdk.app import _configure_style
from allin1_sdk.axle_steering_geometry import (
    AxleSteeringGain,
    SteeringGeometrySolution,
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)
from allin1_sdk.axle_configurator import (
    AXLE_SUPPORT_RUNTIME_VERSION,
    AXLE_SUPPORT_SCHEMA_VERSION,
    EXPORT_FIVEM_RUNTIME,
    EXPORT_STOCK_METADATA,
    PRESET_STEER_DRIVE_REAR,
    apply_axle_support_weights,
    detect_axle_configuration,
    set_steering_command_polarity,
)
from allin1_sdk.axle_runtime_bundler import story_native_runtime_configuration
from allin1_sdk.vehicle_axles_ui import (
    StoryControllerBuildOptions,
    VehicleAxlesPanel,
    _edit_axle_controls,
    _format_steering_gain,
    _guided_physical_layout_configuration,
    _has_unreviewed_physical_layout,
    _native_story_export_ready,
    _requires_selective_steering_runtime,
    _steering_solution_summary,
)


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    _configure_style(root)
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)


def _three_axle_bones() -> tuple[Bone, ...]:
    return (
        Bone("wheel_lf", (-1.0, 4.0, 0.0)),
        Bone("wheel_rf", (1.0, 4.0, 0.0)),
        Bone("wheel_lm1", (-1.0, 0.0, 0.0)),
        Bone("wheel_rm1", (1.0, 0.0, 0.0)),
        Bone("wheel_lr", (-1.0, -2.0, 0.0)),
        Bone("wheel_rr", (1.0, -2.0, 0.0)),
    )


def test_signed_steering_gain_format_is_compact_and_unambiguous() -> None:
    assert _format_steering_gain(1.0) == "+1.00"
    assert _format_steering_gain(-0.219) == "-0.22"
    assert _format_steering_gain(0.0) == "0.00"


def test_geometry_summary_shows_pivot_and_every_physical_axle_gain() -> None:
    solution = SteeringGeometrySolution(
        pivot_longitudinal_position=-2.123121,
        pivot_source="derived_fixed_axles",
        pivot_axle_orders=(2,),
        reference_axle_order=1,
        reference_lock_degrees=35.0,
        turn_radius=8.286,
        bone_position_sha256="0" * 64,
        axles=(
            AxleSteeringGain(1, 3.67867, 5.801791, 35.0, 1.0, "same"),
            AxleSteeringGain(2, -2.123121, 0.0, 0.0, 0.0, "fixed"),
            AxleSteeringGain(3, -3.254378, -1.131257, -7.77, -0.22, "counter"),
        ),
    )

    assert _steering_solution_summary(solution) == (
        "Pivot Y -2.123 (fixed axle) · A1 +1.00 · A2 0.00 · A3 -0.22"
    )


def test_signed_or_scaled_gain_requires_selective_runtime() -> None:
    legacy = SimpleNamespace(axles=(
        SimpleNamespace(steered=True, steering_gain=1.0),
        SimpleNamespace(steered=False, steering_gain=0.0),
    ))
    signed = SimpleNamespace(axles=(
        *legacy.axles,
        SimpleNamespace(steered=True, steering_gain=-0.22),
    ))

    assert not _requires_selective_steering_runtime(legacy)
    assert _requires_selective_steering_runtime(signed)


def test_steering_role_edit_safely_invalidates_old_geometry() -> None:
    bones = _three_axle_bones()
    base = detect_axle_configuration(
        "fixture_bus", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    signed = apply_steering_geometry_to_configuration(
        base, solve_automatic_steering_geometry(base, bones),
    )
    signed = replace(signed, minimum_runtime_version="3.1.0")
    assert signed.schema_version == 2
    assert signed.axles[2].steering_gain < 0.0

    edited, invalidated = _edit_axle_controls(
        signed,
        2,
        steered=False,
        powered=signed.axles[2].powered,
        service_brake=signed.axles[2].service_brake,
        handbrake=signed.axles[2].handbrake,
    )

    assert invalidated
    assert edited.schema_version == 1
    assert edited.minimum_runtime_version == "3.1.0"
    assert edited.steering_calculation is None
    assert [axle.steering_gain for axle in edited.axles] == [1.0, 0.0, 0.0]


def test_nonsteering_row_edit_preserves_signed_geometry_evidence() -> None:
    bones = _three_axle_bones()
    base = detect_axle_configuration(
        "fixture_bus", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    signed = apply_steering_geometry_to_configuration(
        base, solve_automatic_steering_geometry(base, bones),
    )

    edited, invalidated = _edit_axle_controls(
        signed,
        1,
        steered=signed.axles[1].steered,
        powered=not signed.axles[1].powered,
        service_brake=signed.axles[1].service_brake,
        handbrake=signed.axles[1].handbrake,
    )

    assert not invalidated
    assert edited.schema_version == 2
    assert edited.steering_calculation == signed.steering_calculation
    assert [axle.steering_gain for axle in edited.axles] == [
        axle.steering_gain for axle in signed.axles
    ]


def test_steering_role_edit_preserves_schema_three_support_weights() -> None:
    bones = _three_axle_bones()
    base = detect_axle_configuration(
        "support_fixture", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    supported = apply_axle_support_weights(
        base, {1: 1.10, 2: 0.95, 3: 0.95},
    )

    edited, invalidated = _edit_axle_controls(
        supported,
        2,
        steered=False,
        powered=supported.axles[2].powered,
        service_brake=supported.axles[2].service_brake,
        handbrake=supported.axles[2].handbrake,
    )

    assert invalidated
    assert edited.schema_version == AXLE_SUPPORT_SCHEMA_VERSION
    assert edited.minimum_runtime_version == AXLE_SUPPORT_RUNTIME_VERSION
    assert edited.steering_calculation is None
    assert [
        axle.suspension.support_weight for axle in edited.axles
        if axle.suspension is not None
    ] == [1.10, 0.95, 0.95]


def _remapped_bus_bones() -> tuple[Bone, ...]:
    return (
        Bone("wheel_lm1", (-1.0, 4.4533, 0.0)),
        Bone("wheel_rm1", (1.0, 4.4533, 0.0)),
        Bone("wheel_lf", (-1.0, -4.0748, 0.0)),
        Bone("wheel_rf", (1.0, -4.0748, 0.0)),
        Bone("wheel_lr", (-1.0, -5.4140, 0.0)),
        Bone("wheel_rr", (1.0, -5.4140, 0.0)),
    )


def test_guided_setup_preserves_detected_bus_order_and_builds_rear_steer() -> None:
    wheel_bones = _remapped_bus_bones()
    detected = detect_axle_configuration("example_transit_bus", wheel_bones)

    assert _has_unreviewed_physical_layout(detected)
    configured, solution = _guided_physical_layout_configuration(
        detected, wheel_bones,
    )

    assert not _has_unreviewed_physical_layout(configured)
    assert configured.intentional_layout_override is not None
    assert configured.export_mode == EXPORT_FIVEM_RUNTIME
    assert configured.preset == PRESET_STEER_DRIVE_REAR
    assert [
        (item.left_bone, item.right_bone) for item in configured.axles
    ] == [
        ("wheel_lm1", "wheel_rm1"),
        ("wheel_lf", "wheel_rf"),
        ("wheel_lr", "wheel_rr"),
    ]
    assert [(item.steered, item.powered) for item in configured.axles] == [
        (True, False), (False, True), (True, False),
    ]
    assert configured.axles[0].steering_gain > 0.0
    assert configured.axles[1].steering_gain == 0.0
    assert configured.axles[2].steering_gain < 0.0
    assert solution.axles[0].physical_order == 1


def test_guided_bus_exports_controller_ready_native_story_contract() -> None:
    wheel_bones = _remapped_bus_bones()
    detected = detect_axle_configuration(
        "example_transit_bus", wheel_bones, target="story-legacy",
    )
    configured, _solution = _guided_physical_layout_configuration(
        detected, wheel_bones,
    )
    configured = apply_axle_support_weights(
        configured, {1: 0.95, 2: 1.10, 3: 0.95},
    )
    configured = set_steering_command_polarity(configured, "inverted")

    payload = story_native_runtime_configuration(configured, bones=wheel_bones)

    assert payload["schemaVersion"] == 4
    assert payload["modelName"] == "example_transit_bus"
    assert payload["expectedWheelCount"] == 6
    assert payload["compatibility"] == {"story-legacy": True}
    assert [row["wheelIndices"] for row in payload["axles"]] == [
        [4, 5], [0, 1], [2, 3],
    ]
    assert payload["steeringCommandPolarity"] == "inverted"
    assert [row["powered"] for row in payload["axles"]] == [
        False, True, False,
    ]
    assert [row["suspension"]["supportWeight"] for row in payload["axles"]] == [
        0.95, 1.10, 0.95,
    ]
    assert [row["steeringGain"] for row in payload["axles"]] == pytest.approx(
        [1.0, 0.0, -0.17928062909474354], abs=1.0e-9,
    )
    calculation = payload["steeringCalculation"]
    assert calculation["runtimeRecompute"] is True
    assert calculation["referenceSelection"] == "farthest_steered_axle"
    assert calculation["referenceAxleOrder"] == 0
    assert calculation["pivotAxleOrders"] == [1]
    assert calculation["physicalBonePairs"] == [
        ["wheel_lm1", "wheel_rm1"],
        ["wheel_lf", "wheel_rf"],
        ["wheel_lr", "wheel_rr"],
    ]


def test_native_story_export_availability_matches_serializer_contract() -> None:
    bones = _three_axle_bones()
    story = detect_axle_configuration(
        "story_bus", bones, export_mode=EXPORT_FIVEM_RUNTIME,
        target="story-legacy",
    )
    fivem = detect_axle_configuration(
        "fivem_bus", bones, export_mode=EXPORT_FIVEM_RUNTIME,
        target="fivem-legacy",
    )
    stock = detect_axle_configuration(
        "stock_bus", bones, export_mode=EXPORT_STOCK_METADATA,
        target="story-legacy",
    )

    assert _native_story_export_ready(story) is True
    assert _native_story_export_ready(fivem) is False
    assert _native_story_export_ready(stock) is False
    assert _native_story_export_ready(None) is False


def test_panel_load_apply_export_and_clear_lifecycle(tk_root) -> None:
    applied = []
    exported = []
    panel = VehicleAxlesPanel(
        tk_root,
        on_apply=applied.append,
        on_undo=lambda: None,
        on_redo=lambda: None,
        on_export=exported.append,
    )
    try:
        assert panel.configuration() is None
        assert panel.snapshot() == ""
        assert panel.detect_button.instate(["disabled"])

        bones = _three_axle_bones()
        panel.load(
            "fixture_bus",
            None,
            bones=bones,
            editable=True,
            handling_flags=0,
            drive_bias_front=0.5,
        )

        draft = panel.configuration()
        assert draft is not None
        assert draft.vehicle_model == "fixture_bus"
        assert len(panel.tree.get_children()) == 3
        assert panel.tree.selection() == ("0",)
        assert panel.target_key() == "story-legacy"
        assert '"vehicle_model": "fixture_bus"' in panel.snapshot()
        assert panel.detect_button.instate(["!disabled"])

        panel._apply()
        panel._export()
        assert applied == [draft]
        assert exported == [draft]

        panel._layout_actions(280)
        assert panel._action_layout == "narrow"
        assert panel.apply_button.cget("text") == "Apply"
        panel._layout_actions(500)
        assert panel._action_layout == "wide"
        assert panel.apply_button.cget("text") == "Apply + validate"
        assert panel._scroll_editor(SimpleNamespace(num=4, delta=0)) == "break"
        assert panel._scroll_editor(SimpleNamespace(num=0, delta=-120)) == "break"
        assert panel._scroll_editor(SimpleNamespace(num=0, delta=0)) == "break"

        panel.clear()
        assert panel.configuration() is None
        assert panel.snapshot() == ""
        assert not panel.tree.get_children()
        assert panel.detect_button.instate(["disabled"])
        assert panel.status.get() == "Select a vehicle to inspect its wheel skeleton."
    finally:
        panel.destroy()


def test_panel_builds_story_controller_from_compact_inline_form(
    tmp_path, tk_root,
) -> None:
    captured = []
    panel = VehicleAxlesPanel(
        tk_root,
        on_apply=lambda _configuration: None,
        on_undo=lambda: None,
        on_redo=lambda: None,
        on_export=lambda _configuration: None,
        on_build_controller=lambda configuration, bones, options: captured.append(
            (configuration, bones, options)
        ),
    )
    try:
        bones = _three_axle_bones()
        configuration = detect_axle_configuration(
            "fixture_bus", bones,
            preset=PRESET_STEER_DRIVE_REAR,
            export_mode=EXPORT_FIVEM_RUNTIME,
            target="story-legacy",
        )
        panel.load(
            "fixture_bus", configuration, bones=bones, editable=True,
        )

        assert not panel.controller_builder.winfo_manager()
        panel._toggle_controller_builder()
        tk_root.update_idletasks()
        assert panel.controller_builder.winfo_manager() == "pack"
        assert panel.controller_build_button.instate(["!disabled"])

        output = tmp_path / "controller-candidate"
        panel.controller_edition.set("Enhanced")
        panel.controller_configuration_directory.set(
            "scripts/ExampleTransitPack/VehicleSettings",
        )
        panel.controller_log_file.set(
            "scripts/ExampleTransitPack/Axles.log",
        )
        panel.controller_output_directory.set(str(output))
        panel._build_story_controller()

        assert len(captured) == 1
        sent_configuration, sent_bones, options = captured[0]
        assert sent_configuration == configuration
        assert sent_bones == bones
        assert options == StoryControllerBuildOptions(
            targets=("story-enhanced",),
            configuration_directory=(
                "scripts/ExampleTransitPack/VehicleSettings"
            ),
            log_file="scripts/ExampleTransitPack/Axles.log",
            output_directory=output.resolve(),
        )
        assert panel.controller_build_button.instate(["disabled"])
        assert "Preparing" in panel.controller_build_status.get()

        panel.controller_build_progress("Running native tests…")
        assert panel.controller_build_status.get() == "Running native tests…"
        panel.controller_build_finished(
            True, "Validated runtime 4.4.0; in-game acceptance is required.",
        )
        assert panel.controller_build_button.instate(["!disabled"])
        assert "Validated runtime 4.4.0" in panel.controller_build_status.get()

        panel._toggle_controller_builder()
        assert not panel.controller_builder.winfo_manager()
    finally:
        panel.destroy()

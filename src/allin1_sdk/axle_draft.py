"""Pure axle draft transformations, independent of any desktop toolkit.

The panel edits a draft only.  Its host owns persistence and therefore keeps
the same revision, validation, and undo/redo boundary as every other vehicle
authoring operation.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from allin1_sdk.axle_configurator import (
    AXLE_SCHEMA_VERSION,
    AXLE_SUPPORT_SCHEMA_VERSION,
    EXPORT_FIVEM_RUNTIME,
    PRESET_CUSTOM,
    PRESET_FRONT_STEER,
    PRESET_STEER_DRIVE_REAR,
    STEERING_COMMAND_POLARITY_INVERTED,
    STEERING_COMMAND_POLARITY_NORMAL,
    STEERING_POLARITY_SCHEMA_VERSION,
    AxleConfiguration,
    apply_axle_preset,
    apply_intentional_layout_override,
    clear_intentional_layout_override,
    requires_signed_steering_gain,
)
from allin1_sdk.axle_steering_geometry import (
    SteeringGeometrySolution,
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)
from allin1_sdk.native_assets import NativeModelBone


def _native_story_export_ready(config: AxleConfiguration | None) -> bool:
    """Return whether the current draft matches the native Story serializer."""

    if config is None or config.export_mode != EXPORT_FIVEM_RUNTIME:
        return False
    story_targets = [
        target for target, enabled in config.compatibility
        if enabled and target in {"story-legacy", "story-enhanced"}
    ]
    return len(story_targets) == 1


def _format_steering_gain(gain: float) -> str:
    """Return the compact signed form used by the resolved-axle table."""

    value = float(gain)
    return "0.00" if abs(value) < 0.0005 else f"{value:+.2f}"


def _steering_solution_summary(
    solution: SteeringGeometrySolution, polarity: str = STEERING_COMMAND_POLARITY_NORMAL,
) -> str:
    """Summarize one geometry proposal without obscuring the axle editor."""

    source = {
        "explicit": "manual pivot",
        "selected_fixed_axles": "selected fixed axle",
        "derived_fixed_axles": "fixed axle",
    }.get(solution.pivot_source, solution.pivot_source.replace("_", " "))
    multiplier = -1.0 if polarity == STEERING_COMMAND_POLARITY_INVERTED else 1.0
    gains = " · ".join(
        f"A{item.physical_order} {_format_steering_gain(item.steering_gain)}"
        + (
            f" → {_format_steering_gain(item.steering_gain * multiplier)}"
            if multiplier < 0.0 else ""
        )
        for item in solution.axles
    )
    return (
        f"Pivot Y {solution.pivot_longitudinal_position:.3f} ({source}) · "
        f"{gains}"
    )


def _current_gain_summary(config: AxleConfiguration) -> str:
    base = " · ".join(
        f"A{axle.physical_order} {_format_steering_gain(axle.steering_gain)}"
        for axle in config.axles
    )
    if config.steering_command_polarity == STEERING_COMMAND_POLARITY_INVERTED:
        effective = " · ".join(
            f"A{axle.physical_order} {_format_steering_gain(-axle.steering_gain)}"
            for axle in config.axles
        )
        return f"Base gains · {base} · inverted effective · {effective}"
    return f"Base steering gains · {base} · normal polarity"


def _requires_selective_steering_runtime(config: AxleConfiguration) -> bool:
    """Return whether signed/scaled gains exceed legacy boolean steering."""

    return requires_signed_steering_gain(config)


def _edit_axle_controls(
    config: AxleConfiguration,
    index: int,
    *,
    steered: bool,
    powered: bool,
    service_brake: bool,
    handbrake: bool,
) -> tuple[AxleConfiguration, bool]:
    """Apply one editor row and invalidate geometry only when its role changes.

    Automatic steering evidence describes a specific set of steered/fixed
    axles.  Changing that set makes the old pivot/reference evidence stale, so
    the draft returns to safe schema-1 boolean steering until the author runs
    Calculate steering again.  Drive and brake edits do not affect steering
    geometry and therefore preserve signed gains and their evidence.
    """

    rows = list(config.axles)
    if not 0 <= index < len(rows):
        raise IndexError("Axle editor row is outside the configured axle array")
    steering_changed = bool(steered) != rows[index].steered
    rows[index] = replace(
        rows[index],
        steered=bool(steered),
        steering_gain=(1.0 if steered else 0.0)
        if steering_changed else rows[index].steering_gain,
        powered=bool(powered),
        service_brake=bool(service_brake),
        handbrake=bool(handbrake),
    )
    if steering_changed:
        rows = [
            replace(row, steering_gain=1.0 if row.steered else 0.0)
            for row in rows
        ]
        support_enabled = bool(rows) and all(
            row.suspension is not None for row in rows
        )
        return (
            replace(
                config,
                schema_version=(
                    STEERING_POLARITY_SCHEMA_VERSION
                    if config.steering_command_polarity
                    == STEERING_COMMAND_POLARITY_INVERTED
                    else AXLE_SUPPORT_SCHEMA_VERSION
                    if support_enabled else AXLE_SCHEMA_VERSION
                ),
                preset=PRESET_CUSTOM,
                axles=tuple(rows),
                steering_calculation=None,
            ),
            True,
        )
    return replace(config, preset=PRESET_CUSTOM, axles=tuple(rows)), False


def _physical_pairs(config: AxleConfiguration) -> tuple[tuple[str, str], ...]:
    return tuple(
        (item.left_bone, item.right_bone)
        for item in sorted(config.axles, key=lambda value: value.physical_order)
    )


def _has_unreviewed_physical_layout(config: AxleConfiguration) -> bool:
    """Return whether detected wheel positions disagree with canonical roles."""

    if config.intentional_layout_override is not None:
        return False
    canonical = _physical_pairs(clear_intentional_layout_override(config))
    return _physical_pairs(config) != canonical


def _guided_physical_layout_configuration(
    config: AxleConfiguration,
    bones: Iterable[NativeModelBone],
) -> tuple[AxleConfiguration, SteeringGeometrySolution]:
    """Build the safe one-click draft for a spatially remapped skeleton.

    Three-axle visual-instancing layouts receive the common steer/drive/
    counter-steer behavior. Other supported layouts receive ordinary physical
    front steering. In both cases, signed geometry is calculated only after
    the exact physical order has been fingerprinted.
    """

    bone_rows = tuple(bones)
    if not _has_unreviewed_physical_layout(config):
        raise ValueError("The detected skeleton does not require a physical-order override")
    remapped = apply_intentional_layout_override(
        config,
        bone_rows,
        physical_bone_pairs=_physical_pairs(config),
        reason=(
            "Workbench-guided physical order for intentional GTA wheel-mesh "
            "family instancing"
        ),
    )
    behavior = (
        PRESET_STEER_DRIVE_REAR
        if len(remapped.axles) == 3 else PRESET_FRONT_STEER
    )
    remapped = replace(
        apply_axle_preset(remapped, behavior),
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    solution = solve_automatic_steering_geometry(remapped, bone_rows)
    configured = replace(
        apply_steering_geometry_to_configuration(remapped, solution),
        preset=behavior,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    return configured, solution

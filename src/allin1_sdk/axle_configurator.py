"""Vehicle axle detection, validation, metadata flags, and FiveM export.

This module deliberately keeps skeleton roles, drawable instancing, steering,
and drive state as separate values.  It never renames or moves a wheel bone.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


# Schema 1 is the legacy boolean-steering contract still consumed by the
# published runtimes.  Schema 2 is emitted selectively only after an author
# creates a non-legacy signed steering solution.  Keeping the legacy constant
# stable prevents unrelated detected/prefab configurations from being
# needlessly promoted before a schema-2 runtime is available.
AXLE_SCHEMA_VERSION = 1
LATEST_AXLE_SCHEMA_VERSION = 2
SIGNED_STEERING_RUNTIME_VERSION = "2.0.0"
STEERING_GAIN_EPSILON = 1.0e-9

STEERING_CALCULATION_AUTOMATIC = "automatic_geometry"
STEERING_CALCULATION_MANUAL = "manual"
STEERING_CALCULATION_MODES = (
    STEERING_CALCULATION_AUTOMATIC,
    STEERING_CALCULATION_MANUAL,
)
STEERING_GEOMETRY_ALGORITHM_VERSION = 1
STEERING_PIVOT_SOURCES = (
    "explicit",
    "selected_fixed_axles",
    "derived_fixed_axles",
)


def _semantic_version_core(value: str, label: str) -> tuple[int, int, int]:
    """Return the exact numeric version used by the native axle contract.

    Axle configuration files deliberately use only ``major.minor.patch``.
    Accepting a prerelease suffix here would let Python treat (for example)
    ``2.0.0-alpha`` as satisfying the stable ``2.0.0`` runtime while the
    native parser rejects the same value.
    """

    match = re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)",
        str(value).strip(),
    )
    if match is None:
        raise ValueError(f"{label} must use semantic major.minor.patch form")
    return tuple(int(match.group(index)) for index in (1, 2, 3))

EXPORT_STOCK_METADATA = "stock_metadata"
EXPORT_FIVEM_RUNTIME = "fivem_runtime"
EXPORT_MODES = (EXPORT_STOCK_METADATA, EXPORT_FIVEM_RUNTIME)

VISUAL_FRONT = "front"
VISUAL_SHARED_MIDDLE_REAR = "shared_middle_rear"
VISUAL_FAMILIES = (VISUAL_FRONT, VISUAL_SHARED_MIDDLE_REAR)

PRESET_STANDARD = "Standard Two Axle"
PRESET_FRONT_STEER = "Front-Wheel Steering"
PRESET_REAR_STEER = "Rear-Wheel Steering"
PRESET_ALL_STEER = "All-Wheel Steering"
PRESET_STEER_DRIVE_REAR = "Steer → Drive → Rear Steer"
PRESET_CUSTOM = "Custom"
AXLE_PRESETS = (
    PRESET_STANDARD,
    PRESET_FRONT_STEER,
    PRESET_REAR_STEER,
    PRESET_ALL_STEER,
    PRESET_STEER_DRIVE_REAR,
    PRESET_CUSTOM,
)

HF_STEER_REARWHEELS = 0x20
HF_HANDBRAKE_REARWHEELSTEER = 0x40
HF_STEER_ALL_WHEELS = 0x80
STEERING_HANDLING_MASK = (
    HF_STEER_REARWHEELS
    | HF_HANDBRAKE_REARWHEELSTEER
    | HF_STEER_ALL_WHEELS
)
FLAG_IS_STEERED = 0x08
FLAG_IS_DRIVEN = 0x10

CANONICAL_WHEEL_PAIRS = (
    ("front", "wheel_lf", "wheel_rf"),
    ("middle", "wheel_lm1", "wheel_rm1"),
    ("middle", "wheel_lm2", "wheel_rm2"),
    ("middle", "wheel_lm3", "wheel_rm3"),
    ("rear", "wheel_lr", "wheel_rr"),
)
CANONICAL_WHEEL_BONES = frozenset(
    name for _role, left, right in CANONICAL_WHEEL_PAIRS for name in (left, right)
)
MINIMUM_AXLE_PAIRS = 2
MAXIMUM_AXLE_PAIRS = 5


def _dense_canonical_pairs(axle_count: int) -> tuple[tuple[str, str], ...]:
    """Return GTA's only supported dense 2-5 axle semantic sequence."""

    if not MINIMUM_AXLE_PAIRS <= axle_count <= MAXIMUM_AXLE_PAIRS:
        raise ValueError("Axle configuration must contain 2-5 physical axle pairs")
    middle_count = axle_count - 2
    return tuple(
        (left, right)
        for _role, left, right in (
            CANONICAL_WHEEL_PAIRS[: 1 + middle_count]
            + CANONICAL_WHEEL_PAIRS[-1:]
        )
    )

TARGET_CANONICAL_PAIR_ORDER: dict[str, tuple[tuple[str, str], ...]] = {
    # Keep target knowledge in this one resolver.  Current validated FiveM and
    # Workbench Story adapters expose the same semantic ordering, but callers
    # do not derive indices from an axle row number and a future target can
    # provide a different rule without migrating vehicle configurations.
    target: tuple((left, right) for _role, left, right in CANONICAL_WHEEL_PAIRS)
    for target in (
        "fivem-legacy", "fivem-enhanced", "story-legacy", "story-enhanced",
    )
}

RUNTIME_REQUIRED_MESSAGE = (
    "This steering pattern requires a selective axle runtime configuration. "
    "Stock handling.meta would also steer the middle axle."
)
SHARED_VISUAL_WARNING = (
    "GTA supports only front and shared middle/rear wheel-template families. "
    "Use ordinary bone-bound add-on geometry for axle-specific differences."
)


class BoneLike(Protocol):
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]


@dataclass(frozen=True)
class AxleFinding:
    severity: str
    code: str
    message: str
    axle: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AxleAddonGeometry:
    asset: str
    bone: str
    is_wheel_mesh: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AxleAddonGeometry":
        asset = _identifier_path(payload.get("asset"), "Add-on geometry asset")
        bone = _bone_name(payload.get("bone"), "Add-on geometry bone")
        is_wheel_mesh = payload.get("is_wheel_mesh", False)
        if not isinstance(is_wheel_mesh, bool):
            raise ValueError("Add-on geometry Is Wheel Mesh state must be a boolean")
        return cls(asset=asset, bone=bone, is_wheel_mesh=is_wheel_mesh)


@dataclass(frozen=True)
class VehicleAxle:
    physical_order: int
    logical_role: str
    left_bone: str
    right_bone: str
    left_runtime_index: int
    right_runtime_index: int
    steered: bool
    powered: bool
    service_brake: bool = True
    handbrake: bool = False
    visual_family: str = VISUAL_SHARED_MIDDLE_REAR
    addon_geometry: tuple[AxleAddonGeometry, ...] = ()
    steering_gain: float | None = None

    def __post_init__(self) -> None:
        # Schema-1 drafts predate signed steering gain. Preserve their exact
        # behavior: every steered axle was full same-phase and fixed axles did
        # not steer. New geometry authoring writes an explicit [-1, 1] value.
        gain = self.steering_gain
        if gain is None:
            gain = 1.0 if self.steered else 0.0
        if (
            isinstance(gain, bool) or not isinstance(gain, (int, float))
            or not math.isfinite(float(gain)) or not -1.0 <= float(gain) <= 1.0
        ):
            raise ValueError("Axle steering gain must be a finite number from -1 to 1")
        if not self.steered and abs(float(gain)) > STEERING_GAIN_EPSILON:
            raise ValueError("A non-steered axle must use zero steering gain")
        object.__setattr__(self, "steering_gain", float(gain))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VehicleAxle":
        role = str(payload.get("logical_role", "")).strip().casefold()
        if role not in {"front", "middle", "rear"}:
            raise ValueError("Axle logical role must be Front, Middle, or Rear")
        visual = str(payload.get("visual_family", "")).strip().casefold()
        if visual not in VISUAL_FAMILIES:
            raise ValueError("Axle visual family must be front or shared_middle_rear")
        order = _positive_int(payload.get("physical_order"), "Axle physical order")
        left_index = _nonnegative_int(
            payload.get("left_runtime_index"), "Left runtime wheel index",
        )
        right_index = _nonnegative_int(
            payload.get("right_runtime_index"), "Right runtime wheel index",
        )
        states = {}
        for key, default in (
            ("steered", False), ("powered", True),
            ("service_brake", True), ("handbrake", False),
        ):
            value = payload.get(key, default)
            if not isinstance(value, bool):
                raise ValueError(f"Axle {key.replace('_', ' ')} state must be a boolean")
            states[key] = value
        gain = payload.get("steering_gain", payload.get("steeringGain"))
        addons = payload.get("addon_geometry", ())
        if not isinstance(addons, (list, tuple)):
            raise ValueError("Axle add-on geometry must be a list")
        return cls(
            physical_order=order,
            logical_role=role,
            left_bone=_bone_name(payload.get("left_bone"), "Left wheel bone"),
            right_bone=_bone_name(payload.get("right_bone"), "Right wheel bone"),
            left_runtime_index=left_index,
            right_runtime_index=right_index,
            visual_family=visual,
            addon_geometry=tuple(AxleAddonGeometry.from_dict(item) for item in addons),
            steering_gain=gain,
            **states,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["addon_geometry"] = [asdict(item) for item in self.addon_geometry]
        return payload


@dataclass(frozen=True)
class RuntimeReapplicationPolicy:
    on_entity_created: bool = True
    on_network_ownership: bool = True
    after_repair: bool = True
    on_resource_restart: bool = True
    recovery_check_ms: int = 1500

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "RuntimeReapplicationPolicy":
        values = dict(payload or {})
        allowed = {
            "on_entity_created", "on_network_ownership", "after_repair",
            "on_resource_restart", "recovery_check_ms",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError("Unsupported runtime policy fields: " + ", ".join(unknown))
        for key in allowed - {"recovery_check_ms"}:
            value = values.get(key, True)
            if not isinstance(value, bool):
                raise ValueError(f"Runtime policy {key} must be a boolean")
            values[key] = value
        interval = values.get("recovery_check_ms", 1500)
        if (
            isinstance(interval, bool) or not isinstance(interval, int)
            or not 500 <= interval <= 60_000
        ):
            raise ValueError("Runtime recovery interval must be 500-60000 milliseconds")
        values["recovery_check_ms"] = interval
        return cls(**values)


@dataclass(frozen=True)
class SteeringCalculationProvenance:
    """Evidence attached to a schema-2 signed steering configuration.

    The SHA-256 is deliberately limited to canonical wheel-bone names and
    resolved XYZ positions.  Meshes, tyre packages, materials, and GTA's two
    visual wheel-template families cannot affect steering phase.
    """

    mode: str
    bone_position_sha256: str
    algorithm_version: int = STEERING_GEOMETRY_ALGORITHM_VERSION
    pivot_longitudinal_position: float | None = None
    pivot_source: str = ""
    pivot_axle_orders: tuple[int, ...] = ()
    reference_axle_order: int | None = None
    reference_lock_degrees: float | None = None
    pair_position_tolerance: float | None = None
    position_epsilon: float | None = None

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().casefold()
        if mode not in STEERING_CALCULATION_MODES:
            raise ValueError("Steering calculation mode must be automatic_geometry or manual")
        object.__setattr__(self, "mode", mode)
        digest = str(self.bone_position_sha256).strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Steering calculation bone-position SHA-256 is invalid")
        object.__setattr__(self, "bone_position_sha256", digest)
        if (
            isinstance(self.algorithm_version, bool)
            or not isinstance(self.algorithm_version, int)
            or self.algorithm_version != STEERING_GEOMETRY_ALGORITHM_VERSION
        ):
            raise ValueError("Steering geometry algorithm version is unsupported")

        if mode == STEERING_CALCULATION_MANUAL:
            if (
                self.pivot_longitudinal_position is not None
                or self.pivot_source
                or self.pivot_axle_orders
                or self.reference_axle_order is not None
                or self.reference_lock_degrees is not None
                or self.pair_position_tolerance is not None
                or self.position_epsilon is not None
            ):
                raise ValueError("Manual steering provenance cannot contain automatic geometry fields")
            return

        values = (
            self.pivot_longitudinal_position,
            self.reference_lock_degrees,
            self.pair_position_tolerance,
            self.position_epsilon,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("Automatic steering provenance requires finite pivot and lock values")
        if not 1.0 <= float(self.reference_lock_degrees) <= 80.0:
            raise ValueError("Automatic steering reference lock must be between 1 and 80 degrees")
        if (
            float(self.pair_position_tolerance) <= 0.0
            or float(self.position_epsilon) <= 0.0
        ):
            raise ValueError(
                "Automatic steering tolerances must be greater than zero"
            )
        if self.pivot_source not in STEERING_PIVOT_SOURCES:
            raise ValueError("Automatic steering pivot source is invalid")
        if (
            isinstance(self.reference_axle_order, bool)
            or not isinstance(self.reference_axle_order, int)
            or self.reference_axle_order < 1
        ):
            raise ValueError("Automatic steering reference axle order must be positive")
        if any(
            isinstance(order, bool) or not isinstance(order, int) or order < 1
            for order in self.pivot_axle_orders
        ) or len(self.pivot_axle_orders) != len(set(self.pivot_axle_orders)):
            raise ValueError("Automatic steering pivot axle orders must be unique positive integers")
        if self.pivot_source == "explicit" and self.pivot_axle_orders:
            raise ValueError("An explicit steering pivot cannot also name pivot axles")
        if self.pivot_source != "explicit" and not self.pivot_axle_orders:
            raise ValueError("A derived steering pivot must name its fixed pivot axles")

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any],
    ) -> "SteeringCalculationProvenance":
        values = dict(payload)
        runtime_orders = "pivotAxleOrders" in values
        runtime_reference = "referenceAxleOrder" in values
        aliases = {
            "algorithmVersion": "algorithm_version",
            "bonePositionSha256": "bone_position_sha256",
            "pivotLongitudinalPosition": "pivot_longitudinal_position",
            "pivotSource": "pivot_source",
            "pivotAxleOrders": "pivot_axle_orders",
            "referenceAxleOrder": "reference_axle_order",
            "referenceLockDegrees": "reference_lock_degrees",
            "pairPositionTolerance": "pair_position_tolerance",
            "positionEpsilon": "position_epsilon",
        }
        for authored, canonical in aliases.items():
            if authored in values:
                if canonical in values:
                    raise ValueError(f"Steering calculation repeats {canonical}")
                values[canonical] = values.pop(authored)
        allowed = {
            "mode", "algorithm_version", "bone_position_sha256",
            "pivot_longitudinal_position", "pivot_source", "pivot_axle_orders",
            "reference_axle_order", "reference_lock_degrees",
            "pair_position_tolerance", "position_epsilon",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(
                "Unsupported steering calculation fields: " + ", ".join(unknown)
            )
        mode = str(values.get("mode", "")).strip()
        if mode.casefold() == "automaticgeometry":
            values["mode"] = STEERING_CALCULATION_AUTOMATIC
        raw_orders = values.get("pivot_axle_orders", ())
        if not isinstance(raw_orders, (list, tuple)):
            raise ValueError("Steering calculation pivot axle orders must be an array")
        values["pivot_axle_orders"] = tuple(
            order + 1 if runtime_orders and isinstance(order, int)
            and not isinstance(order, bool) else order
            for order in raw_orders
        )
        if runtime_reference:
            reference = values.get("reference_axle_order")
            if isinstance(reference, int) and not isinstance(reference, bool):
                values["reference_axle_order"] = reference + 1
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "algorithm_version": self.algorithm_version,
            "bone_position_sha256": self.bone_position_sha256,
        }
        if self.mode == STEERING_CALCULATION_AUTOMATIC:
            payload.update({
                "pivot_longitudinal_position": self.pivot_longitudinal_position,
                "pivot_source": self.pivot_source,
                "pivot_axle_orders": list(self.pivot_axle_orders),
                "reference_axle_order": self.reference_axle_order,
                "reference_lock_degrees": self.reference_lock_degrees,
                "pair_position_tolerance": self.pair_position_tolerance,
                "position_epsilon": self.position_epsilon,
            })
        return payload


@dataclass(frozen=True)
class AxleConfiguration:
    schema_version: int
    vehicle_model: str
    preset: str
    export_mode: str
    axles: tuple[VehicleAxle, ...]
    runtime_reapplication: RuntimeReapplicationPolicy = field(
        default_factory=RuntimeReapplicationPolicy,
    )
    configuration_id: str = ""
    model_hash: str = ""
    minimum_runtime_version: str = "1.0.0"
    compatibility: tuple[tuple[str, bool], ...] = (
        ("fivem-legacy", True),
        ("fivem-enhanced", False),
        ("story-legacy", False),
        ("story-enhanced", False),
    )
    handbrake_rear_steering: bool = False
    steering_calculation: SteeringCalculationProvenance | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version not in {AXLE_SCHEMA_VERSION, LATEST_AXLE_SCHEMA_VERSION}
        ):
            raise ValueError(f"Unsupported axle configuration schema: {self.schema_version}")
        runtime_core = _semantic_version_core(
            self.minimum_runtime_version, "Minimum axle runtime version",
        )
        nonlegacy = any(
            abs(float(axle.steering_gain) - (1.0 if axle.steered else 0.0))
            > STEERING_GAIN_EPSILON
            for axle in self.axles
        )
        if self.schema_version == AXLE_SCHEMA_VERSION:
            if self.steering_calculation is not None:
                raise ValueError("Schema-1 axle configurations cannot contain steering provenance")
            if nonlegacy:
                raise ValueError("Signed steering gain requires axle configuration schema 2")
        else:
            if self.steering_calculation is None:
                raise ValueError("Schema-2 axle configurations require steering calculation evidence")
            if not nonlegacy:
                raise ValueError("Schema 2 is reserved for non-legacy signed steering gain")
            if runtime_core < _semantic_version_core(
                SIGNED_STEERING_RUNTIME_VERSION, "Signed steering runtime version",
            ):
                raise ValueError(
                    "Schema-2 signed steering requires minimum runtime version "
                    f"{SIGNED_STEERING_RUNTIME_VERSION} or newer"
                )

    @property
    def expected_wheel_count(self) -> int:
        return len(self.axles) * 2

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AxleConfiguration":
        migrated = migrate_axle_configuration(payload)
        model = _model_name(migrated.get("vehicle_model"))
        preset = str(migrated.get("preset", PRESET_CUSTOM)).strip()
        if preset not in AXLE_PRESETS:
            raise ValueError("Unsupported axle preset")
        mode = str(migrated.get("export_mode", EXPORT_STOCK_METADATA)).strip().casefold()
        if mode not in EXPORT_MODES:
            raise ValueError("Axle export mode must be stock_metadata or fivem_runtime")
        raw_axles = migrated.get("axles")
        if not isinstance(raw_axles, list) or not (
            MINIMUM_AXLE_PAIRS <= len(raw_axles) <= MAXIMUM_AXLE_PAIRS
        ):
            raise ValueError("Axle configuration must contain 2-5 physical axle pairs")
        axles = tuple(VehicleAxle.from_dict(item) for item in raw_axles)
        configuration_id = _configuration_id(
            migrated.get("configuration_id") or f"{model}-axles"
        )
        model_hash = _model_hash(migrated.get("model_hash") or joaat_hex(model))
        if model_hash != joaat_hex(model):
            raise ValueError("Vehicle model hash does not match its GTA joaat model identifier")
        runtime_version = str(
            migrated.get("minimum_runtime_version", "1.0.0")
        ).strip()
        _semantic_version_core(runtime_version, "Minimum axle runtime version")
        compatibility = _compatibility_values(migrated.get("compatibility"))
        handbrake_steering = migrated.get("handbrake_rear_steering", False)
        if not isinstance(handbrake_steering, bool):
            raise ValueError("Handbrake rear steering state must be a boolean")
        schema_version = migrated.get("schema_version")
        if not isinstance(schema_version, int):
            raise ValueError("Axle configuration schema must be an integer")
        raw_calculation = migrated.get("steering_calculation")
        if raw_calculation is not None and not isinstance(raw_calculation, Mapping):
            raise ValueError("Steering calculation evidence must be an object")
        calculation = (
            SteeringCalculationProvenance.from_dict(raw_calculation)
            if isinstance(raw_calculation, Mapping) else None
        )
        if schema_version == LATEST_AXLE_SCHEMA_VERSION and calculation is None:
            raise ValueError("Schema-2 axle configurations require steering calculation evidence")
        return cls(
            schema_version=schema_version,
            vehicle_model=model,
            preset=preset,
            export_mode=mode,
            axles=axles,
            runtime_reapplication=RuntimeReapplicationPolicy.from_dict(
                migrated.get("runtime_reapplication")
                if isinstance(migrated.get("runtime_reapplication"), Mapping) else None
            ),
            configuration_id=configuration_id,
            model_hash=model_hash,
            minimum_runtime_version=runtime_version,
            compatibility=compatibility,
            handbrake_rear_steering=handbrake_steering,
            steering_calculation=calculation,
        )

    def to_dict(self) -> dict[str, Any]:
        axles: list[dict[str, Any]] = []
        for item in self.axles:
            row = item.to_dict()
            if self.schema_version == AXLE_SCHEMA_VERSION:
                row.pop("steering_gain", None)
            axles.append(row)
        payload = {
            "schema_version": self.schema_version,
            "vehicle_model": self.vehicle_model,
            "configuration_id": self.configuration_id or f"{self.vehicle_model}-axles",
            "model_hash": self.model_hash or joaat_hex(self.vehicle_model),
            "minimum_runtime_version": self.minimum_runtime_version,
            "preset": self.preset,
            "export_mode": self.export_mode,
            "expected_wheel_count": self.expected_wheel_count,
            "axles": axles,
            "runtime_reapplication": asdict(self.runtime_reapplication),
            "compatibility": dict(self.compatibility),
            "handbrake_rear_steering": self.handbrake_rear_steering,
        }
        if self.schema_version == LATEST_AXLE_SCHEMA_VERSION:
            assert self.steering_calculation is not None
            payload["steering_calculation"] = self.steering_calculation.to_dict()
        return payload


@dataclass(frozen=True)
class StockMetadataResult:
    original_flags: int
    updated_flags: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SteeringDiagnosticWheel:
    axle_order: int
    logical_role: str
    bone: str
    wheel_index: int
    steered: bool
    powered: bool
    visual_family: str
    configured_steering_gain: float
    configured_phase: str


@dataclass(frozen=True)
class SteeringDiagnostic:
    requested_input: float
    vehicle_steering_angle: float
    wheels: tuple[SteeringDiagnosticWheel, ...]
    outcome: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_input": self.requested_input,
            "vehicle_steering_angle": self.vehicle_steering_angle,
            "wheels": [asdict(item) for item in self.wheels],
            "outcome": self.outcome,
        }


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _model_name(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text or len(text) > 96 or not all(ch.isalnum() or ch == "_" for ch in text):
        raise ValueError("Vehicle model must use 1-96 letters, numbers, or underscores")
    return text


def _configuration_id(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,95}", text):
        raise ValueError(
            "Axle configuration id must use 2-96 lowercase letters, numbers, dots, dashes, or underscores"
        )
    return text


def _model_hash(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not re.fullmatch(r"0X[0-9A-F]{8}", text):
        raise ValueError("Vehicle model hash must be 0x plus eight hexadecimal digits")
    return "0x" + text[2:]


def joaat_hex(value: str) -> str:
    result = 0
    for byte in value.casefold().encode("utf-8"):
        result = (result + byte) & 0xFFFFFFFF
        result = (result + (result << 10)) & 0xFFFFFFFF
        result ^= result >> 6
    result = (result + (result << 3)) & 0xFFFFFFFF
    result ^= result >> 11
    result = (result + (result << 15)) & 0xFFFFFFFF
    return f"0x{result & 0xFFFFFFFF:08X}"


def _compatibility_values(value: Any) -> tuple[tuple[str, bool], ...]:
    targets = tuple(TARGET_CANONICAL_PAIR_ORDER)
    if value is None:
        payload: Mapping[str, Any] = {"fivem-legacy": True}
    elif isinstance(value, Mapping):
        payload = value
    else:
        raise ValueError("Axle target compatibility must be an object")
    aliases = {
        "fivemlegacy": "fivem-legacy", "fivemenhanced": "fivem-enhanced",
        "storylegacy": "story-legacy", "storyenhanced": "story-enhanced",
    }
    normalized = {
        aliases.get(str(key).replace("_", "").replace("-", "").casefold(), str(key).casefold()): value
        for key, value in payload.items()
    }
    unknown = sorted(set(normalized) - set(targets))
    if unknown:
        raise ValueError("Unsupported axle compatibility targets: " + ", ".join(unknown))
    result = []
    for target in targets:
        supported = normalized.get(target, False)
        if not isinstance(supported, bool):
            raise ValueError(f"Axle compatibility for {target} must be a boolean")
        result.append((target, supported))
    return tuple(result)


def _bone_name(value: Any, label: str) -> str:
    text = str(value or "").strip().casefold()
    if not text or len(text) > 96 or not all(ch.isalnum() or ch == "_" for ch in text):
        raise ValueError(f"{label} must use 1-96 letters, numbers, or underscores")
    return text


def _identifier_path(value: Any, label: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    parts = text.split("/")
    if (
        not text or len(text) > 512 or text.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"{label} must be a safe relative asset path")
    return text


def migrate_axle_configuration(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize legacy/v2 spellings without silently promoting the schema."""
    data = dict(payload)
    # Accept the documented cross-runtime spelling at the import boundary;
    # SDK reports remain consistent with the repository's snake_case schemas.
    aliases = {
        "schemaVersion": "schema_version",
        "configurationId": "configuration_id",
        "modelName": "vehicle_model",
        "modelHash": "model_hash",
        "minimumRuntimeVersion": "minimum_runtime_version",
        "steeringCalculation": "steering_calculation",
    }
    for authored, canonical in aliases.items():
        if authored in data and canonical not in data:
            data[canonical] = data.pop(authored)
    schema = data.get("schema_version", 0)
    if schema in {0, None}:
        schema = AXLE_SCHEMA_VERSION
    if (
        isinstance(schema, bool)
        or not isinstance(schema, int)
        or schema not in {AXLE_SCHEMA_VERSION, LATEST_AXLE_SCHEMA_VERSION}
    ):
        raise ValueError(f"Unsupported axle configuration schema: {schema}")
    if "model" in data and "vehicle_model" not in data:
        data["vehicle_model"] = data.pop("model")
    migrated_axles = []
    for raw in data.get("axles", ()):
        if not isinstance(raw, Mapping):
            raise ValueError("Legacy axle configuration contains an invalid axle")
        item = dict(raw)
        authored_order = item.pop("order", None)
        if "physical_order" not in item and authored_order is not None:
            item["physical_order"] = (
                authored_order + 1
                if isinstance(authored_order, int)
                and not isinstance(authored_order, bool)
                and authored_order >= 0
                else authored_order
            )
        item.setdefault("left_bone", item.pop("leftBone", item.pop("left", "")))
        item.setdefault("right_bone", item.pop("rightBone", item.pop("right", "")))
        left_bone = str(item.get("left_bone", "")).strip().casefold()
        canonical_role = next((
            role for role, left, right in CANONICAL_WHEEL_PAIRS
            if left == left_bone
            and right == str(item.get("right_bone", "")).strip().casefold()
        ), "middle")
        item.setdefault("logical_role", item.pop("role", canonical_role))
        wheel_indices = item.pop("wheelIndices", None)
        if isinstance(wheel_indices, (list, tuple)) and len(wheel_indices) == 2:
            item.setdefault("left_runtime_index", wheel_indices[0])
            item.setdefault("right_runtime_index", wheel_indices[1])
        item.setdefault(
            "visual_family",
            VISUAL_FRONT if left_bone == "wheel_lf" else VISUAL_SHARED_MIDDLE_REAR,
        )
        item.setdefault("addon_geometry", [])
        item.setdefault("service_brake", item.pop("serviceBrake", True))
        if "steeringGain" in item:
            if "steering_gain" in item:
                raise ValueError("Axle repeats steering_gain")
            item["steering_gain"] = item.pop("steeringGain")
        if schema == LATEST_AXLE_SCHEMA_VERSION and "steering_gain" not in item:
            raise ValueError("Schema-2 axle rows require explicit steering_gain values")
        migrated_axles.append(item)
    if any(
        "physical_order" not in item
        or "left_runtime_index" not in item
        or "right_runtime_index" not in item
        for item in migrated_axles
    ):
        # Legacy drafts did not always persist wheel indices. Recover them
        # from canonical bone semantics, never from the draft's row order.
        selected = {
            (
                str(item.get("left_bone", "")).strip().casefold(),
                str(item.get("right_bone", "")).strip().casefold(),
            )
            for item in migrated_axles
        }
        canonical = tuple(
            (left, right) for _role, left, right in CANONICAL_WHEEL_PAIRS
        )
        unknown = selected - set(canonical)
        if unknown:
            raise ValueError(
                "Legacy axle configuration cannot derive indices for a "
                "noncanonical wheel-bone pair"
            )
        ordered = [pair for pair in canonical if pair in selected]
        if not MINIMUM_AXLE_PAIRS <= len(ordered) <= MAXIMUM_AXLE_PAIRS:
            raise ValueError(
                "Legacy axle configuration requires 2-5 canonical axle pairs"
            )
        if set(ordered) != set(_dense_canonical_pairs(len(ordered))):
            raise ValueError(
                "Legacy axle configuration contains a gapped canonical middle pair"
            )
        semantic_mapping = {
            bone: index
            for pair_index, pair in enumerate(ordered)
            for bone, index in (
                (pair[0], pair_index * 2),
                (pair[1], (pair_index * 2) + 1),
            )
        }
        semantic_orders = {
            pair: order for order, pair in enumerate(ordered, start=1)
        }
        for item in migrated_axles:
            left = str(item.get("left_bone", "")).strip().casefold()
            right = str(item.get("right_bone", "")).strip().casefold()
            item.setdefault("physical_order", semantic_orders[(left, right)])
            item.setdefault("left_runtime_index", semantic_mapping[left])
            item.setdefault("right_runtime_index", semantic_mapping[right])
    data["axles"] = migrated_axles
    data["schema_version"] = schema
    data.setdefault("preset", PRESET_CUSTOM)
    data.setdefault("export_mode", EXPORT_STOCK_METADATA)
    data.setdefault("runtime_reapplication", asdict(RuntimeReapplicationPolicy()))
    model = str(data.get("vehicle_model", "")).strip().casefold()
    data.setdefault("configuration_id", f"{model}-axles")
    if model:
        data.setdefault("model_hash", joaat_hex(model))
    data.setdefault(
        "minimum_runtime_version",
        SIGNED_STEERING_RUNTIME_VERSION
        if schema == LATEST_AXLE_SCHEMA_VERSION else "1.0.0",
    )
    data.setdefault("compatibility", {"fivem-legacy": True})
    data.setdefault("handbrake_rear_steering", False)
    return data


def resolve_runtime_wheel_index_map(
    pair_names: Iterable[tuple[str, str]],
    *,
    target: str,
) -> dict[str, int]:
    """Resolve explicit wheel slots from target rules and canonical semantics."""
    target_key = target.strip().casefold()
    rules = TARGET_CANONICAL_PAIR_ORDER.get(target_key)
    if rules is None:
        raise ValueError(f"Unsupported axle runtime target: {target}")
    selected = {(left.casefold(), right.casefold()) for left, right in pair_names}
    unknown = selected - set(rules)
    if unknown:
        raise ValueError("Runtime wheel mapping contains a noncanonical bone pair")
    ordered = [pair for pair in rules if pair in selected]
    if not MINIMUM_AXLE_PAIRS <= len(ordered) <= MAXIMUM_AXLE_PAIRS:
        raise ValueError("Runtime wheel mapping requires 2-5 canonical axle pairs")
    if set(ordered) != set(_dense_canonical_pairs(len(ordered))):
        raise ValueError(
            "Runtime wheel mapping contains a gapped canonical middle axle pair"
        )
    mapping: dict[str, int] = {}
    wheel_index = 0
    for left, right in ordered:
        mapping[left] = wheel_index
        mapping[right] = wheel_index + 1
        wheel_index += 2
    return mapping


def retarget_axle_configuration(
    config: AxleConfiguration,
    target: str,
    *,
    reported_wheel_count: int | None = None,
    wheel_index_map: Mapping[str, int] | None = None,
) -> AxleConfiguration:
    """Resolve one explicit target mapping and isolate compatibility to it.

    A caller with target-exported wheel evidence may supply ``wheel_index_map``;
    otherwise the SDK's canonical target resolver is used. The complete map is
    validated before it replaces any authored indices.
    """
    target_key = str(target).strip().casefold()
    if target_key not in TARGET_CANONICAL_PAIR_ORDER:
        raise ValueError(f"Unsupported axle runtime target: {target}")
    expected_count = config.expected_wheel_count
    if reported_wheel_count is not None and (
        isinstance(reported_wheel_count, bool)
        or not isinstance(reported_wheel_count, int)
        or reported_wheel_count != expected_count
    ):
        raise ValueError(
            f"Target reported {reported_wheel_count} wheels; configuration "
            f"requires {expected_count}."
        )
    assigned_bones = {
        bone for axle in config.axles for bone in (axle.left_bone, axle.right_bone)
    }
    if wheel_index_map is None:
        resolved = resolve_runtime_wheel_index_map(
            ((axle.left_bone, axle.right_bone) for axle in config.axles),
            target=target_key,
        )
    else:
        resolved = {}
        for raw_bone, raw_index in wheel_index_map.items():
            bone = str(raw_bone).strip().casefold()
            if (
                isinstance(raw_index, bool) or not isinstance(raw_index, int)
                or raw_index < 0
            ):
                raise ValueError("Target wheel-index evidence must use non-negative integers")
            if bone in resolved:
                raise ValueError("Target wheel-index evidence contains duplicate bones")
            resolved[bone] = raw_index
        if set(resolved) != assigned_bones:
            raise ValueError(
                "Target wheel-index evidence must map every configured canonical wheel bone"
            )
        if sorted(resolved.values()) != list(range(expected_count)):
            raise ValueError(
                "Target wheel-index evidence must contain each physical wheel slot exactly once"
            )
    axles = tuple(
        replace(
            axle,
            left_runtime_index=resolved[axle.left_bone],
            right_runtime_index=resolved[axle.right_bone],
        )
        for axle in config.axles
    )
    compatibility = tuple(
        (candidate, candidate == target_key)
        for candidate in TARGET_CANONICAL_PAIR_ORDER
    )
    return replace(config, axles=axles, compatibility=compatibility)


def _bone_map(bones: Iterable[BoneLike]) -> dict[str, BoneLike]:
    result: dict[str, BoneLike] = {}
    for bone in bones:
        name = str(bone.name).strip().casefold()
        if name and name not in result:
            result[name] = bone
    return result


def detect_axle_configuration(
    vehicle_model: str,
    bones: Iterable[BoneLike],
    *,
    preset: str | None = None,
    export_mode: str = EXPORT_STOCK_METADATA,
    target: str = "fivem-legacy",
) -> AxleConfiguration:
    """Detect canonical pairs and sort them by vehicle-local forward position."""
    available = _bone_map(bones)
    detected: list[tuple[float, int, str, str, str]] = []
    for canonical_order, (role, left, right) in enumerate(CANONICAL_WHEEL_PAIRS):
        if left not in available or right not in available:
            continue
        left_position = available[left].position
        right_position = available[right].position
        forward = (left_position[1] + right_position[1]) / 2.0
        detected.append((forward, canonical_order, role, left, right))
    if not detected:
        raise ValueError("No complete canonical wheel-bone pairs were detected")
    if not MINIMUM_AXLE_PAIRS <= len(detected) <= MAXIMUM_AXLE_PAIRS:
        raise ValueError("Axle detection requires 2-5 canonical physical axle pairs")
    detected_pairs = {(item[3], item[4]) for item in detected}
    expected_pairs = set(_dense_canonical_pairs(len(detected)))
    if detected_pairs != expected_pairs:
        raise ValueError(
            "Canonical middle axle pairs must be dense: use wheel_lm1/rm1 "
            "before wheel_lm2/rm2, and wheel_lm2/rm2 before wheel_lm3/rm3"
        )
    runtime_mapping = resolve_runtime_wheel_index_map(
        ((item[3], item[4]) for item in detected), target=target,
    )
    detected.sort(key=lambda item: (-item[0], item[1]))
    axles = tuple(
        VehicleAxle(
            physical_order=physical_order,
            logical_role=role,
            left_bone=left,
            right_bone=right,
            left_runtime_index=runtime_mapping[left],
            right_runtime_index=runtime_mapping[right],
            steered=False,
            powered=True,
            service_brake=True,
            handbrake=role == "rear",
            visual_family=(
                VISUAL_FRONT if role == "front" else VISUAL_SHARED_MIDDLE_REAR
            ),
        )
        for physical_order, (
            _forward, canonical_order, role, left, right,
        ) in enumerate(detected, start=1)
    )
    selected = preset or (
        PRESET_STANDARD if len(axles) == 2 else PRESET_CUSTOM
    )
    config = AxleConfiguration(
        schema_version=AXLE_SCHEMA_VERSION,
        vehicle_model=_model_name(vehicle_model),
        preset=PRESET_CUSTOM,
        export_mode=export_mode,
        axles=axles,
        configuration_id=f"{_model_name(vehicle_model)}-axles",
        model_hash=joaat_hex(_model_name(vehicle_model)),
    )
    return retarget_axle_configuration(
        apply_axle_preset(config, selected), target,
    )


def apply_axle_preset(config: AxleConfiguration, preset: str) -> AxleConfiguration:
    if preset not in AXLE_PRESETS:
        raise ValueError("Unsupported axle preset")
    if preset == PRESET_CUSTOM:
        return replace(config, preset=preset)
    orders = [item.physical_order for item in config.axles]
    front_order, rear_order = min(orders), max(orders)
    updated = []
    for axle in config.axles:
        if preset in {PRESET_STANDARD, PRESET_FRONT_STEER}:
            steered = axle.physical_order == front_order
            powered = True
        elif preset == PRESET_REAR_STEER:
            steered = axle.physical_order == rear_order
            powered = True
        elif preset == PRESET_ALL_STEER:
            steered = True
            powered = True
        else:
            if len(config.axles) < 3:
                raise ValueError("Steer → Drive → Rear Steer requires at least three axles")
            steered = axle.physical_order in {front_order, rear_order}
            powered = front_order < axle.physical_order < rear_order
        updated.append(replace(
            axle,
            steered=steered,
            powered=powered,
            steering_gain=1.0 if steered else 0.0,
        ))
    return replace(
        config,
        schema_version=AXLE_SCHEMA_VERSION,
        preset=preset,
        axles=tuple(updated),
        steering_calculation=None,
    )


def validate_axle_configuration(
    config: AxleConfiguration,
    bones: Iterable[BoneLike] = (),
    *,
    handling_flags: int | None = None,
    asset_names: Iterable[str] = (),
    target: str | None = None,
) -> tuple[AxleFinding, ...]:
    """Validate model semantics without changing names, transforms, or files."""
    findings: list[AxleFinding] = []
    bone_rows = tuple(bones)
    bone_lookup = _bone_map(bone_rows)
    calculation = config.steering_calculation
    by_order = {item.physical_order: item for item in config.axles}
    if calculation is not None and calculation.mode == STEERING_CALCULATION_AUTOMATIC:
        invalid_pivots = [
            order for order in calculation.pivot_axle_orders
            if order not in by_order or by_order[order].steered
        ]
        if invalid_pivots:
            findings.append(AxleFinding(
                "error", "steering_pivot_evidence",
                "Automatic steering pivot axles must still exist and remain fixed: "
                + ", ".join(str(order) for order in invalid_pivots),
            ))
        reference = by_order.get(calculation.reference_axle_order or -1)
        if reference is None or not reference.steered:
            findings.append(AxleFinding(
                "error", "steering_reference_evidence",
                "The automatic steering reference axle must still exist and remain steered.",
            ))
    if calculation is not None and bone_lookup:
        try:
            # Local import avoids making the core schema depend on its geometry
            # authoring helper at module-import time.
            from .axle_steering_geometry import (
                SteeringGeometryRequest,
                canonical_bone_position_sha256,
                solve_automatic_steering_geometry,
            )

            current_digest = canonical_bone_position_sha256(
                config, bone_rows,
            )
        except (TypeError, ValueError) as exc:
            findings.append(AxleFinding(
                "error", "steering_evidence_unavailable",
                f"Steering geometry evidence cannot be verified: {exc}",
            ))
        else:
            if current_digest != calculation.bone_position_sha256:
                findings.append(AxleFinding(
                    "error", "stale_steering_geometry",
                    "Canonical wheel-bone positions changed after steering was "
                    "calculated. Recalculate signed gains before applying or exporting.",
                ))
            elif calculation.mode == STEERING_CALCULATION_AUTOMATIC:
                request_values: dict[str, Any] = {
                    "reference_lock_degrees": calculation.reference_lock_degrees,
                    "reference_axle_order": calculation.reference_axle_order,
                    "pair_position_tolerance": calculation.pair_position_tolerance,
                    "position_epsilon": calculation.position_epsilon,
                }
                if calculation.pivot_source == "explicit":
                    request_values["pivot_longitudinal_position"] = (
                        calculation.pivot_longitudinal_position
                    )
                elif calculation.pivot_source == "selected_fixed_axles":
                    request_values["pivot_axle_orders"] = calculation.pivot_axle_orders
                try:
                    reproduced = solve_automatic_steering_geometry(
                        config,
                        bone_rows,
                        SteeringGeometryRequest(**request_values),
                    )
                except (TypeError, ValueError) as exc:
                    findings.append(AxleFinding(
                        "error", "steering_evidence_unavailable",
                        f"Automatic steering evidence cannot be reproduced: {exc}",
                    ))
                else:
                    evidence_mismatch = (
                        reproduced.pivot_source != calculation.pivot_source
                        or reproduced.pivot_axle_orders
                        != calculation.pivot_axle_orders
                        or reproduced.reference_axle_order
                        != calculation.reference_axle_order
                        or abs(
                            reproduced.pivot_longitudinal_position
                            - float(calculation.pivot_longitudinal_position)
                        ) > STEERING_GAIN_EPSILON
                        or any(
                            abs(
                                reproduced.gain_by_physical_order[axle.physical_order]
                                - float(axle.steering_gain)
                            ) > STEERING_GAIN_EPSILON
                            for axle in config.axles
                        )
                    )
                    if evidence_mismatch:
                        findings.append(AxleFinding(
                            "error", "steering_evidence_mismatch",
                            "Signed steering gains or axle roles no longer match "
                            "their automatic geometry evidence. Recalculate steering.",
                        ))
    orders = [item.physical_order for item in config.axles]
    if sorted(orders) != list(range(1, len(config.axles) + 1)):
        findings.append(AxleFinding(
            "error", "physical_order", "Physical axle order must be contiguous from front to rear.",
        ))
    assignments = [
        value for axle in config.axles for value in (axle.left_bone, axle.right_bone)
    ]
    if len(assignments) != len(set(assignments)):
        findings.append(AxleFinding(
            "error", "duplicate_bone", "A wheel bone is assigned to more than one axle.",
        ))
    canonical_details = {
        (left, right): (role, canonical_order)
        for canonical_order, (role, left, right) in enumerate(CANONICAL_WHEEL_PAIRS)
    }
    assigned_pairs = {(item.left_bone, item.right_bone) for item in config.axles}
    semantic_pairs = list(_dense_canonical_pairs(len(config.axles)))
    semantic_orders = {
        pair: order for order, pair in enumerate(semantic_pairs, start=1)
    }
    if assigned_pairs != set(semantic_pairs):
        findings.append(AxleFinding(
            "error", "canonical_pair_sequence",
            "Canonical middle axle pairs must be dense: use wheel_lm1/rm1 "
            "before wheel_lm2/rm2, and wheel_lm2/rm2 before wheel_lm3/rm3.",
        ))
    for axle in config.axles:
        for bone_name in (axle.left_bone, axle.right_bone):
            if bone_name not in CANONICAL_WHEEL_BONES:
                findings.append(AxleFinding(
                    "error", "noncanonical_bone",
                    f"{bone_name} is not a supported canonical wheel bone.", axle.physical_order,
                ))
            if bone_lookup and bone_name not in bone_lookup:
                findings.append(AxleFinding(
                    "error", "missing_bone", f"Wheel bone is missing: {bone_name}",
                    axle.physical_order,
                ))
        expected_right = next((
            right for _role, left, right in CANONICAL_WHEEL_PAIRS
            if left == axle.left_bone
        ), None)
        if expected_right != axle.right_bone:
            findings.append(AxleFinding(
                "error", "missing_partner",
                f"{axle.left_bone} is not paired with its canonical right-side partner.",
                axle.physical_order,
            ))
        pair = (axle.left_bone, axle.right_bone)
        canonical = canonical_details.get(pair)
        if canonical is not None:
            expected_role, _canonical_order = canonical
            if axle.logical_role != expected_role:
                findings.append(AxleFinding(
                    "error", "logical_role_semantics",
                    f"{axle.left_bone}/{axle.right_bone} must use the "
                    f"{expected_role} logical role.", axle.physical_order,
                ))
            expected_order = semantic_orders.get(pair)
            if expected_order is not None and axle.physical_order != expected_order:
                findings.append(AxleFinding(
                    "error", "physical_order_semantics",
                    f"{axle.left_bone}/{axle.right_bone} must be physical axle "
                    f"{expected_order} in canonical front-to-rear order.",
                    axle.physical_order,
                ))
        canonical_front = axle.left_bone == "wheel_lf"
        if canonical_front and axle.visual_family != VISUAL_FRONT:
            findings.append(AxleFinding(
                "warning", "front_visual_family",
                "The physical front axle should use GTA's front wheel-template family.",
                axle.physical_order,
            ))
        if not canonical_front and axle.visual_family != VISUAL_SHARED_MIDDLE_REAR:
            findings.append(AxleFinding(
                "error", "independent_shared_template", SHARED_VISUAL_WARNING,
                axle.physical_order,
            ))
        for addon in axle.addon_geometry:
            if addon.bone not in {axle.left_bone, axle.right_bone}:
                findings.append(AxleFinding(
                    "error", "addon_bone",
                    "Axle-specific add-on geometry must be rigidly bound to this axle's wheel bone.",
                    axle.physical_order,
                ))
            if addon.is_wheel_mesh:
                findings.append(AxleFinding(
                    "error", "addon_is_wheel_mesh",
                    "Axle-specific add-on geometry must have Is Wheel Mesh disabled.",
                    axle.physical_order,
                ))

    if bone_lookup:
        complete = []
        complete_pairs: set[tuple[str, str]] = set()
        for role, left, right in CANONICAL_WHEEL_PAIRS:
            left_bone, right_bone = bone_lookup.get(left), bone_lookup.get(right)
            if (left_bone is None) != (right_bone is None):
                present, missing = (left, right) if left_bone is not None else (right, left)
                findings.append(AxleFinding(
                    "error", "missing_partner",
                    f"{present} is present but its canonical partner {missing} is missing.",
                ))
            if left_bone is None or right_bone is None:
                continue
            forward = (left_bone.position[1] + right_bone.position[1]) / 2.0
            complete.append((forward, role, left, right))
            complete_pairs.add((left, right))
            if left_bone.position[0] >= right_bone.position[0]:
                findings.append(AxleFinding(
                    "error", "left_right_exchanged",
                    "Left and right wheel bones appear to be exchanged.",
                ))
            for name, bone in ((left, left_bone), (right, right_bone)):
                if any(value <= 0.0 for value in bone.scale):
                    findings.append(AxleFinding(
                        "error", "negative_scale",
                        f"{name} has a negative or zero scale; apply a positive transform before export.",
                    ))
                elif any(abs(value - 1.0) > 1e-3 for value in bone.scale):
                    findings.append(AxleFinding(
                        "warning", "unapplied_transform",
                        f"{name} has unapplied scale; apply transforms before export.",
                    ))
            if _wheel_axes_disagree(left_bone.rotation, right_bone.rotation):
                findings.append(AxleFinding(
                    "warning", "wheel_orientation",
                    "Paired wheel bones have unexpectedly different local orientation or roll.",
                ))
        omitted_pairs = [
            (left, right) for _role, left, right in CANONICAL_WHEEL_PAIRS
            if (left, right) in complete_pairs
            and (left, right) not in assigned_pairs
        ]
        if omitted_pairs:
            findings.append(AxleFinding(
                "error", "unconfigured_canonical_pair",
                "The skeleton contains complete canonical axle pairs omitted from "
                "the configuration: "
                + ", ".join(f"{left}/{right}" for left, right in omitted_pairs),
            ))
        position_by_pair = {
            (left, right): forward for forward, _role, left, right in complete
        }
        configured_positions = [
            (axle.physical_order, position_by_pair[(axle.left_bone, axle.right_bone)])
            for axle in sorted(config.axles, key=lambda item: item.physical_order)
            if (axle.left_bone, axle.right_bone) in position_by_pair
        ]
        if any(
            leading_position <= trailing_position
            for (_leading_order, leading_position),
            (_trailing_order, trailing_position)
            in zip(configured_positions, configured_positions[1:])
        ):
            findings.append(AxleFinding(
                "error", "physical_order_position",
                "Configured physical axle order does not follow the skeleton's "
                "strict front-to-rear positions.",
            ))
        complete.sort(reverse=True)
        front_position = next((item[0] for item in complete if item[1] == "front"), None)
        rear_position = next((item[0] for item in complete if item[1] == "rear"), None)
        if rear_position is not None and front_position is not None and rear_position > front_position:
            findings.extend((
                AxleFinding(
                    "error", "rear_ahead_of_front",
                    "wheel_lr/rr are positioned ahead of wheel_lf/rf. Rear-wheel steering semantics may cause inverted steering.",
                ),
                AxleFinding(
                    "error", "canonical_reassignment",
                    "Canonical wheel roles have been spatially reassigned. Restore canonical placement and configure behavior through axle settings.",
                ),
            ))
        if complete and complete[0][1] != "front":
            findings.append(AxleFinding(
                "error", "front_not_forwardmost",
                "wheel_lf/rf do not appear to be the forwardmost axle.",
            ))
            if not any(item.code == "canonical_reassignment" for item in findings):
                findings.append(AxleFinding(
                    "error", "canonical_reassignment",
                    "Canonical wheel roles have been spatially reassigned. Restore canonical placement and configure behavior through axle settings.",
                ))
        recognized = set(CANONICAL_WHEEL_BONES)
        unsupported = sorted(
            name for name in bone_lookup
            if re.fullmatch(r"wheel_[lr](?:m\d+|[fr])", name)
            and name not in recognized
        )
        if unsupported:
            findings.append(AxleFinding(
                "warning", "physical_wheel_slot_limit",
                "Wheel bones beyond GTA's recognized canonical physical slots require cosmetic wheels or a future custom-physics extension: "
                + ", ".join(unsupported),
            ))

    indices = [
        value for axle in config.axles
        for value in (axle.left_runtime_index, axle.right_runtime_index)
    ]
    if len(indices) != config.expected_wheel_count or len(set(indices)) != len(indices):
        findings.append(AxleFinding(
            "error", "runtime_indices", "Runtime wheel indices must be unique for every wheel.",
        ))
    if sorted(indices) != list(range(config.expected_wheel_count)):
        findings.append(AxleFinding(
            "error", "wheel_index_count",
            "Runtime wheel indices do not match the expected contiguous wheel count.",
        ))
    target_key: str | None = None
    if target is not None:
        target_key = str(target).strip().casefold()
        declared = dict(config.compatibility)
        if not declared.get(target_key, False):
            findings.append(AxleFinding(
                "error", "target_compatibility",
                f"Axle configuration does not enable target {target_key}.",
            ))
        try:
            expected_mapping = resolve_runtime_wheel_index_map(
                ((item.left_bone, item.right_bone) for item in config.axles),
                target=target_key,
            )
        except ValueError as exc:
            findings.append(AxleFinding("error", "target_mapping", str(exc)))
        else:
            for axle in config.axles:
                if (
                    expected_mapping.get(axle.left_bone) != axle.left_runtime_index
                    or expected_mapping.get(axle.right_bone) != axle.right_runtime_index
                ):
                    findings.append(AxleFinding(
                        "error", "target_mapping",
                        f"Runtime wheel indices do not match {target_key} canonical mapping.",
                        axle.physical_order,
                    ))
        ordered_axles = sorted(config.axles, key=lambda item: item.physical_order)
        if any(not item.service_brake for item in ordered_axles):
            findings.append(AxleFinding(
                "warning", "runtime_service_brake_unsupported",
                f"The current {target_key} axle runtime does not apply per-axle "
                "service-brake selections; those values remain authoring metadata.",
            ))
        expected_handbrake = {
            ordered_axles[-1].physical_order
        } if ordered_axles else set()
        selected_handbrake = {
            item.physical_order for item in ordered_axles if item.handbrake
        }
        if selected_handbrake != expected_handbrake:
            findings.append(AxleFinding(
                "warning", "runtime_handbrake_unsupported",
                f"The current {target_key} axle runtime does not apply custom "
                "per-axle handbrake selections; those values remain authoring metadata.",
            ))

    signed_gain = requires_signed_steering_gain(config)
    if config.export_mode == EXPORT_STOCK_METADATA and signed_gain:
        findings.append(AxleFinding(
            "error", "signed_steering_runtime_required",
            "Signed or scaled per-axle steering cannot be represented by stock "
            "handling metadata. Select a validated runtime target or restore "
            "legacy +1/0 steering gains.",
        ))
    advanced = _is_front_and_final_rear_with_fixed_middle(config)
    if config.export_mode == EXPORT_STOCK_METADATA and advanced:
        findings.append(AxleFinding("warning", "runtime_required", RUNTIME_REQUIRED_MESSAGE))
    powered_states = {item.powered for item in config.axles}
    if config.export_mode == EXPORT_STOCK_METADATA and len(powered_states) > 1:
        findings.append(AxleFinding(
            "warning", "selective_drive_runtime_required",
            "Selective per-axle drive requires a runtime configuration; stock handling.meta only provides drivetrain bias.",
        ))
    if handling_flags is not None:
        updated_flags = stock_metadata_flags(config, handling_flags).updated_flags
        if (
            (handling_flags & STEERING_HANDLING_MASK)
            != (updated_flags & STEERING_HANDLING_MASK)
        ):
            findings.append(AxleFinding(
                "warning", "conflicting_steering_flags",
                "Current handling steering flags conflict with the selected axle configuration.",
            ))

    names = {Path(value).name.casefold() for value in asset_names}
    if names:
        normal = f"{config.vehicle_model}.yft"
        high = f"{config.vehicle_model}_hi.yft"
        if normal not in names:
            findings.append(AxleFinding(
                "warning", "missing_normal_asset", f"Missing normal vehicle asset: {normal}",
            ))
        if high not in names:
            findings.append(AxleFinding(
                "warning", "missing_high_asset", f"Missing high-detail vehicle asset: {high}",
            ))
    return tuple(_deduplicate_findings(findings))


def _wheel_axes_disagree(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    """Compare wheel axes while treating a mirrored axis as equivalent."""
    def axis(quaternion: tuple[float, float, float, float]) -> tuple[float, float, float]:
        x, y, z, w = quaternion
        # Rotate local +X, the wheel axle direction, by the authored quaternion.
        return (
            1.0 - (2.0 * ((y * y) + (z * z))),
            2.0 * ((x * y) + (z * w)),
            2.0 * ((x * z) - (y * w)),
        )
    first, second = axis(left), axis(right)
    dot = sum(a * b for a, b in zip(first, second))
    first_length = math.sqrt(sum(value * value for value in first))
    second_length = math.sqrt(sum(value * value for value in second))
    return first_length <= 1e-9 or second_length <= 1e-9 or abs(
        dot / (first_length * second_length)
    ) < 0.75


def _deduplicate_findings(findings: Iterable[AxleFinding]) -> list[AxleFinding]:
    result = []
    seen = set()
    for finding in findings:
        key = (finding.severity, finding.code, finding.message, finding.axle)
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return result


def _is_front_and_final_rear_with_fixed_middle(config: AxleConfiguration) -> bool:
    if len(config.axles) < 3:
        return False
    ordered = sorted(config.axles, key=lambda item: item.physical_order)
    return (
        ordered[0].steered and ordered[-1].steered
        and any(not item.steered for item in ordered[1:-1])
    )


def requires_signed_steering_gain(config: AxleConfiguration) -> bool:
    """Return whether exact behavior exceeds legacy boolean steering flags."""
    return any(
        abs(float(axle.steering_gain) - (1.0 if axle.steered else 0.0))
        > STEERING_GAIN_EPSILON
        for axle in config.axles
    )


def stock_metadata_flags(
    config: AxleConfiguration, original_flags: int,
) -> StockMetadataResult:
    """RMW only the documented axle-steering bits in strHandlingFlags."""
    if isinstance(original_flags, bool) or not isinstance(original_flags, int) \
            or original_flags < 0:
        raise ValueError("Handling flags must be a non-negative integer")
    ordered = sorted(config.axles, key=lambda item: item.physical_order)
    steered = {item.physical_order for item in ordered if item.steered}
    front, rear = ordered[0].physical_order, ordered[-1].physical_order
    target = 0
    warnings: list[str] = []
    if steered == {rear}:
        target |= HF_STEER_REARWHEELS
    elif len(steered) == len(ordered):
        target |= HF_STEER_ALL_WHEELS
    elif _is_front_and_final_rear_with_fixed_middle(config):
        target |= HF_STEER_ALL_WHEELS
        warnings.append(RUNTIME_REQUIRED_MESSAGE)
    elif steered not in ({front}, set()):
        target |= HF_STEER_ALL_WHEELS
        warnings.append(
            "The selected steering combination is not represented exactly by stock handling.meta."
        )
    if config.handbrake_rear_steering:
        target |= HF_HANDBRAKE_REARWHEELSTEER
    updated = (original_flags & ~STEERING_HANDLING_MASK) | target
    return StockMetadataResult(original_flags, updated, tuple(warnings))


def parse_handling_flags(value: str) -> int:
    """Parse GTA's hexadecimal strHandlingFlags representation."""
    text = str(value).strip()
    digits = text[2:] if text.casefold().startswith("0x") else text
    if not digits or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
        raise ValueError("strHandlingFlags must contain hexadecimal digits")
    result = int(digits, 16)
    if not 0 <= result <= 0xFFFFFFFF:
        raise ValueError("strHandlingFlags exceeds the unsigned 32-bit range")
    return result


def format_handling_flags(value: int, template: str) -> str:
    """Format an RMW result without gratuitously changing source style."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("Handling flags must be an unsigned 32-bit integer")
    original = str(template).strip()
    prefix = original[:2] if original.casefold().startswith("0x") else ""
    digits = original[2:] if prefix else original
    width = max(1, len(digits))
    authored_upper = any(character in "ABCDEF" for character in digits)
    rendered = f"{value:0{width}{'X' if authored_upper else 'x'}}"
    return prefix + rendered


def update_wheel_flags(current_flags: int, *, steered: bool) -> int:
    """Preserve every unrelated per-wheel bit while updating FLAG_IS_STEERED."""
    if isinstance(current_flags, bool) or not isinstance(current_flags, int) \
            or not 0 <= current_flags <= 0xFFFF:
        raise ValueError("Wheel flags must be an unsigned 16-bit integer")
    return (
        (current_flags | FLAG_IS_STEERED) & 0xFFFF
        if steered else current_flags & (~FLAG_IS_STEERED & 0xFFFF)
    )


def update_story_wheel_flags(
    current_flags: int, *, steered: bool, powered: bool,
) -> int:
    """RMW only the reverse-engineered 16-bit steer and driven bits."""
    updated = update_wheel_flags(current_flags, steered=steered)
    return (
        (updated | FLAG_IS_DRIVEN) & 0xFFFF
        if powered else updated & (~FLAG_IS_DRIVEN & 0xFFFF)
    )


def steering_diagnostic(
    config: AxleConfiguration,
    *,
    requested_input: float,
    vehicle_steering_angle: float,
    runtime_wheel_flags: Mapping[int, int],
    runtime_powered: Mapping[int, bool],
    visual_direction_mismatch: bool = False,
) -> SteeringDiagnostic:
    values = (requested_input, vehicle_steering_angle)
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise ValueError("Steering diagnostic inputs must be finite numbers")
    if requested_input * vehicle_steering_angle < -0.01:
        outcome = (
            "Vehicle travels in the opposite direction from steering input: likely "
            "logical/physical axle reassignment or runtime flag conflict."
        )
    elif visual_direction_mismatch:
        outcome = (
            "Vehicle turns correctly but wheel meshes visually point the wrong way: "
            "likely bone roll, local-axis, negative-scale, or mesh-orientation error."
        )
    else:
        outcome = "Steering input, vehicle response, and configured wheel roles agree."
    wheels = tuple(
        SteeringDiagnosticWheel(
            axle_order=axle.physical_order,
            logical_role=axle.logical_role,
            bone=bone,
            wheel_index=index,
            steered=bool(runtime_wheel_flags.get(index, 0) & FLAG_IS_STEERED),
            powered=bool(runtime_powered.get(index, False)),
            visual_family=axle.visual_family,
            configured_steering_gain=float(axle.steering_gain),
            configured_phase=(
                "counter"
                if float(axle.steering_gain) < -STEERING_GAIN_EPSILON
                else "same"
                if float(axle.steering_gain) > STEERING_GAIN_EPSILON
                else "fixed"
            ),
        )
        for axle in sorted(config.axles, key=lambda item: item.physical_order)
        for bone, index in (
            (axle.left_bone, axle.left_runtime_index),
            (axle.right_bone, axle.right_runtime_index),
        )
    )
    return SteeringDiagnostic(
        float(requested_input), float(vehicle_steering_angle), wheels, outcome,
    )


def _enabled_fivem_target(config: AxleConfiguration) -> str:
    enabled = [
        target for target, supported in config.compatibility
        if supported and target.startswith("fivem-")
    ]
    if not enabled:
        raise ValueError(
            "FiveM resource export requires an axle configuration retargeted "
            "to fivem-legacy or fivem-enhanced"
        )
    return enabled[0]


def fivem_client_lua(config: AxleConfiguration) -> str:
    """Generate an event-driven, model-specific client resource."""
    if config.export_mode != EXPORT_FIVEM_RUNTIME:
        raise ValueError("FiveM resource export requires fivem_runtime mode")
    if requires_signed_steering_gain(config):
        raise ValueError(
            "FiveM export cannot apply signed or scaled per-axle steering gain; "
            "the available wheel natives expose steering flags only"
        )
    target = _enabled_fivem_target(config)
    findings = validate_axle_configuration(config, target=target)
    errors = [item for item in findings if item.severity == "error"]
    if errors:
        raise ValueError("Cannot generate runtime resource: " + errors[0].message)
    policy = config.runtime_reapplication
    wheel_rows = ",\n".join(
        f"    [{index}] = {{ steered = {str(axle.steered).lower()}, "
        f"powered = {str(axle.powered).lower()} }}"
        for axle in sorted(config.axles, key=lambda item: item.physical_order)
        for index in (axle.left_runtime_index, axle.right_runtime_index)
    )
    ownership_condition = (
        "state and state.owner ~= owner"
        if policy.on_network_ownership else "false"
    )
    recreation_condition = (
        "not wheelStateMatches(vehicle)" if policy.after_repair else "false"
    )
    restart_body = (
        'applyExisting("resource restart")'
        if policy.on_resource_restart else "-- resource-restart application disabled"
    )
    created_handler = (
        """RegisterNetEvent("allin1_axles:created", function(networkId)
    local vehicle = NetToVeh(networkId)
    if vehicle ~= 0 then applyAxles(vehicle, "created") end
end)"""
        if policy.on_entity_created else "-- server creation signal disabled"
    )
    return f"""-- Generated by ALLIN1 SDK Axle Configurator. Do not hand-edit.
local MODEL = {int((config.model_hash or joaat_hex(config.vehicle_model))[2:], 16)}
local EXPECTED_WHEELS = {config.expected_wheel_count}
local STEER = 0x08
local RECOVERY_MS = {policy.recovery_check_ms}
local WHEELS = {{
{wheel_rows}
}}

local applied = {{}}
local warned = {{}}
local runtimeAvailable = nil

local function nativesAvailable()
    if runtimeAvailable ~= nil then return runtimeAvailable end
    runtimeAvailable = type(GetVehicleNumberOfWheels) == "function"
        and type(GetVehicleWheelFlags) == "function"
        and type(SetVehicleWheelFlags) == "function"
        and type(SetVehicleWheelIsPowered) == "function"
        and type(NetworkHasControlOfEntity) == "function"
    if not runtimeAvailable then
        print("[ALLIN1 Axles] required wheel natives are unavailable; runtime disabled")
    end
    return runtimeAvailable
end

local function ownsVehicle(vehicle)
    return not NetworkGetEntityIsNetworked(vehicle)
        or (NetworkGetEntityOwner(vehicle) == PlayerId() and NetworkHasControlOfEntity(vehicle))
end

local function warnOnce(vehicle, message)
    local key = NetworkGetEntityIsNetworked(vehicle) and NetworkGetNetworkIdFromEntity(vehicle) or vehicle
    if warned[key] then return end
    warned[key] = true
    print(("[ALLIN1 Axles] %s: %s"):format({json.dumps(config.vehicle_model)}, message))
end

local function wheelStateMatches(vehicle)
    for index = 0, EXPECTED_WHEELS - 1 do
        local desired = WHEELS[index]
        local flags = GetVehicleWheelFlags(vehicle, index)
        if ((flags & STEER) ~= 0) ~= desired.steered then return false end
        if GetVehicleWheelIsPowered and GetVehicleWheelIsPowered(vehicle, index) ~= desired.powered then
            return false
        end
    end
    return true
end

local function applyAxles(vehicle, reason)
    if not nativesAvailable() then return false end
    if not DoesEntityExist(vehicle) or GetEntityType(vehicle) ~= 2 then return false end
    if GetEntityModel(vehicle) ~= MODEL or not ownsVehicle(vehicle) then return false end
    local count = GetVehicleNumberOfWheels(vehicle)
    if count ~= EXPECTED_WHEELS then
        warnOnce(vehicle, ("expected %d wheels but found %d; configuration was not applied"):format(EXPECTED_WHEELS, count))
        return false
    end
    for index = 0, EXPECTED_WHEELS - 1 do
        local desired = WHEELS[index]
        local flags = GetVehicleWheelFlags(vehicle, index)
        if desired.steered then flags = (flags | STEER) & 0xFFFF else flags = flags & 0xFFF7 end
        SetVehicleWheelFlags(vehicle, index, flags)
        SetVehicleWheelIsPowered(vehicle, index, desired.powered)
    end
    applied[vehicle] = {{ owner = NetworkGetEntityOwner(vehicle), reason = reason }}
    return true
end

local function applyExisting(reason)
    for _, vehicle in ipairs(GetGamePool("CVehicle")) do applyAxles(vehicle, reason) end
end

{created_handler}

AddEventHandler("onClientResourceStart", function(resource)
    if resource == GetCurrentResourceName() then {restart_body} end
end)

CreateThread(function()
    while true do
        Wait(RECOVERY_MS)
        for _, vehicle in ipairs(GetGamePool("CVehicle")) do
            if DoesEntityExist(vehicle) and GetEntityModel(vehicle) == MODEL and ownsVehicle(vehicle) then
                local state = applied[vehicle]
                local owner = NetworkGetEntityOwner(vehicle)
                local ownershipChanged = {ownership_condition}
                local recreated = {recreation_condition}
                if not state or ownershipChanged or recreated then
                    applyAxles(vehicle, ownershipChanged and "ownership" or "wheel-state recovery")
                end
            end
        end
    end
end)
"""


def fivem_server_lua(config: AxleConfiguration) -> str:
    """Use the documented server entity event only as a bounded creation hint."""
    if config.export_mode != EXPORT_FIVEM_RUNTIME:
        raise ValueError("FiveM resource export requires fivem_runtime mode")
    if not config.runtime_reapplication.on_entity_created:
        return "-- Entity-created axle hints are disabled for this configuration.\n"
    model_hash = int((config.model_hash or joaat_hex(config.vehicle_model))[2:], 16)
    return f"""-- Generated by ALLIN1 SDK Axle Configurator. Do not hand-edit.
local MODEL = {model_hash}

AddEventHandler("entityCreated", function(entity)
    if GetEntityType(entity) ~= 2 or GetEntityModel(entity) ~= MODEL then return end
    SetTimeout(0, function()
        if not DoesEntityExist(entity) then return end
        local networkId = NetworkGetNetworkIdFromEntity(entity)
        if networkId and networkId ~= 0 then
            TriggerClientEvent("allin1_axles:created", -1, networkId)
        end
    end)
end)
"""


def write_fivem_resource(
    config: AxleConfiguration,
    destination: str | Path,
    *,
    update: bool = False,
) -> Path:
    """Publish or update only a matching SDK-owned model resource."""
    target = Path(destination).expanduser().resolve()
    if target.exists() or target.is_symlink():
        if not update or not target.is_dir() or target.is_symlink():
            raise FileExistsError(f"FiveM axle resource destination already exists: {target}")
        marker = target / "axle-config.json"
        try:
            existing = AxleConfiguration.from_dict(json.loads(marker.read_text("utf-8")))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Existing resource is not an SDK-owned axle resource") from exc
        if existing.vehicle_model != config.vehicle_model:
            raise ValueError("Existing axle resource belongs to a different vehicle model")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.axles-", dir=target.parent))
    try:
        (stage / "fxmanifest.lua").write_text(
            "fx_version 'cerulean'\n"
            "game 'gta5'\n"
            f"name 'ALLIN1 axle configuration: {config.vehicle_model}'\n"
            "author 'Generated by ALLIN1 SDK'\n"
            "version '1.0.0'\n"
            "client_script 'client.lua'\n"
            "server_script 'server.lua'\n"
            "files { 'axle-config.json' }\n",
            encoding="utf-8",
        )
        (stage / "client.lua").write_text(fivem_client_lua(config), encoding="utf-8")
        (stage / "server.lua").write_text(fivem_server_lua(config), encoding="utf-8")
        (stage / "axle-config.json").write_text(
            json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8",
        )
        if target.exists():
            backup = target.with_name(f".{target.name}.previous")
            if backup.exists() or backup.is_symlink():
                raise ValueError(f"Stale axle resource backup exists: {backup}")
            target.replace(backup)
            try:
                stage.replace(target)
            except Exception:
                backup.replace(target)
                raise
            shutil.rmtree(backup)
        else:
            stage.replace(target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return target


__all__ = [
    "AXLE_PRESETS", "AXLE_SCHEMA_VERSION", "LATEST_AXLE_SCHEMA_VERSION",
    "SIGNED_STEERING_RUNTIME_VERSION", "STEERING_CALCULATION_AUTOMATIC",
    "STEERING_CALCULATION_MANUAL", "STEERING_GEOMETRY_ALGORITHM_VERSION",
    "CANONICAL_WHEEL_PAIRS",
    "MAXIMUM_AXLE_PAIRS", "MINIMUM_AXLE_PAIRS", "TARGET_CANONICAL_PAIR_ORDER",
    "EXPORT_FIVEM_RUNTIME", "EXPORT_MODES", "EXPORT_STOCK_METADATA",
    "FLAG_IS_DRIVEN", "FLAG_IS_STEERED", "HF_HANDBRAKE_REARWHEELSTEER",
    "HF_STEER_ALL_WHEELS", "HF_STEER_REARWHEELS", "PRESET_ALL_STEER",
    "PRESET_CUSTOM", "PRESET_FRONT_STEER", "PRESET_REAR_STEER",
    "PRESET_STANDARD", "PRESET_STEER_DRIVE_REAR", "RUNTIME_REQUIRED_MESSAGE",
    "SHARED_VISUAL_WARNING", "VISUAL_FRONT", "VISUAL_SHARED_MIDDLE_REAR",
    "AxleAddonGeometry", "AxleConfiguration", "AxleFinding",
    "SteeringCalculationProvenance",
    "RuntimeReapplicationPolicy", "SteeringDiagnostic", "VehicleAxle",
    "apply_axle_preset", "detect_axle_configuration", "fivem_client_lua",
    "fivem_server_lua", "joaat_hex",
    "format_handling_flags", "migrate_axle_configuration", "parse_handling_flags",
    "requires_signed_steering_gain", "retarget_axle_configuration",
    "steering_diagnostic", "stock_metadata_flags",
    "resolve_runtime_wheel_index_map", "update_story_wheel_flags", "update_wheel_flags",
    "validate_axle_configuration", "write_fivem_resource",
]

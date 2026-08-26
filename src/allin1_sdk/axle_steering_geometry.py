"""Pure geometry solver for variable-length multi-axle steering.

The solver intentionally consumes canonical wheel-bone *positions*, not wheel
meshes or visual-template families.  A rear axle may reuse front-wheel geometry
and still receives counter-phase steering when its bone centre lies behind the
selected neutral pivot.

This is a centre-line (single-track) authoring calculation.  Given a reference
lock angle, every steered axle is aimed at one common instantaneous turn centre::

    radius = abs(reference_y - pivot_y) / tan(reference_lock)
    axle_angle = atan((axle_y - pivot_y) / radius)
    steering_gain = axle_angle / reference_lock_magnitude

It does not claim to predict dynamic tyre-slip understeer or oversteer.  When
several fixed axles are selected, their longitudinal centroid is a documented,
stable neutral-pivot approximation.  Authors can override it with an explicit
vehicle-local Y position.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping

from .axle_configurator import (
    AXLE_SCHEMA_VERSION,
    CANONICAL_WHEEL_PAIRS,
    LATEST_AXLE_SCHEMA_VERSION,
    MAXIMUM_AXLE_PAIRS,
    MINIMUM_AXLE_PAIRS,
    PRESET_CUSTOM,
    SIGNED_STEERING_RUNTIME_VERSION,
    STEERING_CALCULATION_AUTOMATIC,
    STEERING_CALCULATION_MANUAL,
    STEERING_GEOMETRY_ALGORITHM_VERSION,
    STEERING_GAIN_EPSILON,
    AxleConfiguration,
    BoneLike,
    SteeringCalculationProvenance,
)


DEFAULT_REFERENCE_LOCK_DEGREES = 35.0
MINIMUM_REFERENCE_LOCK_DEGREES = 1.0
MAXIMUM_REFERENCE_LOCK_DEGREES = 80.0
DEFAULT_PAIR_POSITION_TOLERANCE = 0.25
DEFAULT_POSITION_EPSILON = 1.0e-4

PIVOT_EXPLICIT = "explicit"
PIVOT_SELECTED_FIXED = "selected_fixed_axles"
PIVOT_DERIVED_FIXED = "derived_fixed_axles"
PIVOT_SOURCES = (PIVOT_EXPLICIT, PIVOT_SELECTED_FIXED, PIVOT_DERIVED_FIXED)


class SteeringGeometryError(ValueError):
    """Raised when geometry cannot produce an unambiguous steering solution."""


def _finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SteeringGeometryError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SteeringGeometryError(f"{label} must be a finite number")
    return result


def _optional_order(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SteeringGeometryError(f"{label} must be a positive axle order")
    return value


@dataclass(frozen=True)
class SteeringGeometryRequest:
    """Author input for automatic steering-gain calculation.

    ``pivot_longitudinal_position`` and ``pivot_axle_orders`` are mutually
    exclusive.  With neither supplied, all explicitly non-steered physical
    axles become the pivot set.  An all-steer layout therefore requires an
    explicit pivot and fails closed without one.
    """

    reference_lock_degrees: float = DEFAULT_REFERENCE_LOCK_DEGREES
    pivot_longitudinal_position: float | None = None
    pivot_axle_orders: tuple[int, ...] = ()
    reference_axle_order: int | None = None
    pair_position_tolerance: float = DEFAULT_PAIR_POSITION_TOLERANCE
    position_epsilon: float = DEFAULT_POSITION_EPSILON

    def __post_init__(self) -> None:
        lock = _finite_float(self.reference_lock_degrees, "Reference lock angle")
        if not MINIMUM_REFERENCE_LOCK_DEGREES <= lock <= MAXIMUM_REFERENCE_LOCK_DEGREES:
            raise SteeringGeometryError(
                "Reference lock angle must be between 1 and 80 degrees"
            )
        if self.pivot_longitudinal_position is not None:
            _finite_float(self.pivot_longitudinal_position, "Explicit pivot position")
        if self.pivot_longitudinal_position is not None and self.pivot_axle_orders:
            raise SteeringGeometryError(
                "Select either an explicit pivot position or pivot axles, not both"
            )
        orders: list[int] = []
        for raw in self.pivot_axle_orders:
            order = _optional_order(raw, "Pivot axle order")
            assert order is not None
            orders.append(order)
        if len(orders) != len(set(orders)):
            raise SteeringGeometryError("Pivot axle orders must be unique")
        _optional_order(self.reference_axle_order, "Reference axle order")
        tolerance = _finite_float(
            self.pair_position_tolerance, "Wheel-pair position tolerance"
        )
        if tolerance <= 0.0:
            raise SteeringGeometryError(
                "Wheel-pair position tolerance must be greater than zero"
            )
        epsilon = _finite_float(self.position_epsilon, "Position epsilon")
        if epsilon <= 0.0:
            raise SteeringGeometryError("Position epsilon must be greater than zero")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "SteeringGeometryRequest":
        values = dict(payload or {})
        aliases = {
            "referenceLockDegrees": "reference_lock_degrees",
            "pivotLongitudinalPosition": "pivot_longitudinal_position",
            "pivotAxleOrders": "pivot_axle_orders",
            "referenceAxleOrder": "reference_axle_order",
            "pairPositionTolerance": "pair_position_tolerance",
            "positionEpsilon": "position_epsilon",
        }
        for authored, canonical in aliases.items():
            if authored in values:
                if canonical in values:
                    raise SteeringGeometryError(
                        f"Steering geometry request repeats {canonical}"
                    )
                values[canonical] = values.pop(authored)
        allowed = {
            "reference_lock_degrees",
            "pivot_longitudinal_position",
            "pivot_axle_orders",
            "reference_axle_order",
            "pair_position_tolerance",
            "position_epsilon",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise SteeringGeometryError(
                "Unsupported steering geometry fields: " + ", ".join(unknown)
            )
        raw_orders = values.get("pivot_axle_orders", ())
        if not isinstance(raw_orders, (list, tuple)):
            raise SteeringGeometryError("Pivot axle orders must be an array")
        values["pivot_axle_orders"] = tuple(raw_orders)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AxleSteeringGain:
    physical_order: int
    longitudinal_position: float
    offset_from_pivot: float
    steering_angle_degrees: float
    steering_gain: float
    phase: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.physical_order, bool)
            or not isinstance(self.physical_order, int)
            or self.physical_order < 1
        ):
            raise SteeringGeometryError("Physical axle order must be positive")
        for value, label in (
            (self.longitudinal_position, "Axle longitudinal position"),
            (self.offset_from_pivot, "Axle pivot offset"),
            (self.steering_angle_degrees, "Axle steering angle"),
            (self.steering_gain, "Axle steering gain"),
        ):
            _finite_float(value, label)
        if not -1.0 <= float(self.steering_gain) <= 1.0:
            raise SteeringGeometryError("Axle steering gain must be between -1 and 1")
        expected_phase = (
            "same" if self.steering_gain > 0.0
            else "counter" if self.steering_gain < 0.0
            else self.phase
        )
        if self.phase not in {"same", "counter", "neutral", "fixed"}:
            raise SteeringGeometryError("Axle steering phase is unsupported")
        if self.steering_gain != 0.0 and self.phase != expected_phase:
            raise SteeringGeometryError("Axle steering phase disagrees with its gain")
        if self.steering_gain == 0.0 and self.phase not in {"neutral", "fixed"}:
            raise SteeringGeometryError("Zero steering gain must be neutral or fixed")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_runtime_dict(self) -> dict[str, Any]:
        return {
            "order": self.physical_order - 1,
            "longitudinalPosition": self.longitudinal_position,
            "offsetFromPivot": self.offset_from_pivot,
            "steeringAngleDegrees": self.steering_angle_degrees,
            "steeringGain": self.steering_gain,
            "phase": self.phase,
        }


@dataclass(frozen=True)
class SteeringGeometrySolution:
    pivot_longitudinal_position: float
    pivot_source: str
    pivot_axle_orders: tuple[int, ...]
    reference_axle_order: int
    reference_lock_degrees: float
    turn_radius: float
    axles: tuple[AxleSteeringGain, ...]
    bone_position_sha256: str
    pair_position_tolerance: float = DEFAULT_PAIR_POSITION_TOLERANCE
    position_epsilon: float = DEFAULT_POSITION_EPSILON
    approximation: str = (
        "single-track centre-line geometry; multiple fixed pivot axles use "
        "their longitudinal centroid"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pivot_longitudinal_position": self.pivot_longitudinal_position,
            "pivot_source": self.pivot_source,
            "pivot_axle_orders": list(self.pivot_axle_orders),
            "reference_axle_order": self.reference_axle_order,
            "reference_lock_degrees": self.reference_lock_degrees,
            "turn_radius": self.turn_radius,
            "bone_position_sha256": self.bone_position_sha256,
            "pair_position_tolerance": self.pair_position_tolerance,
            "position_epsilon": self.position_epsilon,
            "axles": [item.to_dict() for item in self.axles],
            "approximation": self.approximation,
        }

    def to_runtime_dict(self) -> dict[str, Any]:
        return {
            "mode": "automaticGeometry",
            "pivotLongitudinalPosition": self.pivot_longitudinal_position,
            "pivotSource": self.pivot_source,
            "pivotAxleOrders": [order - 1 for order in self.pivot_axle_orders],
            "referenceAxleOrder": self.reference_axle_order - 1,
            "referenceLockDegrees": self.reference_lock_degrees,
            "turnRadius": self.turn_radius,
            "bonePositionSha256": self.bone_position_sha256,
            "pairPositionTolerance": self.pair_position_tolerance,
            "positionEpsilon": self.position_epsilon,
            "axles": [item.to_runtime_dict() for item in self.axles],
            "approximation": self.approximation,
        }

    @property
    def gain_by_physical_order(self) -> dict[int, float]:
        return {item.physical_order: item.steering_gain for item in self.axles}

    def provenance(self) -> SteeringCalculationProvenance:
        return SteeringCalculationProvenance(
            mode=STEERING_CALCULATION_AUTOMATIC,
            algorithm_version=STEERING_GEOMETRY_ALGORITHM_VERSION,
            bone_position_sha256=self.bone_position_sha256,
            pivot_longitudinal_position=self.pivot_longitudinal_position,
            pivot_source=self.pivot_source,
            pivot_axle_orders=self.pivot_axle_orders,
            reference_axle_order=self.reference_axle_order,
            reference_lock_degrees=self.reference_lock_degrees,
            pair_position_tolerance=self.pair_position_tolerance,
            position_epsilon=self.position_epsilon,
        )


def canonical_bone_position_sha256(
    config: AxleConfiguration,
    bones: Iterable[BoneLike],
) -> str:
    """Hash only configured canonical bone names and vehicle-local XYZ.

    IEEE-754 hexadecimal strings make the digest stable across locale and JSON
    number formatting.  No model, mesh, material, visual-family, or tyre data
    participates in this evidence.
    """

    lookup: dict[str, BoneLike] = {}
    duplicates: set[str] = set()
    for bone in bones:
        name = str(bone.name).strip().casefold()
        if name in lookup:
            duplicates.add(name)
        elif name:
            lookup[name] = bone
    configured = {
        name
        for axle in config.axles
        for name in (axle.left_bone.casefold(), axle.right_bone.casefold())
    }
    canonical = {
        name for _role, left, right in CANONICAL_WHEEL_PAIRS
        for name in (left, right)
    }
    noncanonical = sorted(configured - canonical)
    if noncanonical:
        raise SteeringGeometryError(
            "Steering evidence accepts canonical wheel bones only: "
            + ", ".join(noncanonical)
        )
    duplicate_configured = sorted(configured & duplicates)
    if duplicate_configured:
        raise SteeringGeometryError(
            "Canonical wheel bones are duplicated: " + ", ".join(duplicate_configured)
        )
    rows: list[tuple[str, tuple[str, str, str]]] = []
    for axle in sorted(config.axles, key=lambda item: item.physical_order):
        for authored_name in (axle.left_bone, axle.right_bone):
            name = authored_name.casefold()
            bone = lookup.get(name)
            if bone is None:
                raise SteeringGeometryError(
                    f"Steering evidence requires canonical wheel bone {name}"
                )
            try:
                xyz = tuple(
                    _finite_float(bone.position[index], f"{name} position")
                    for index in range(3)
                )
            except (IndexError, TypeError) as exc:
                raise SteeringGeometryError(
                    f"Steering evidence requires XYZ for canonical wheel bone {name}"
                ) from exc
            rows.append((name, tuple(value.hex() for value in xyz)))
    # Canonical name order keeps the digest independent of authored row/order
    # presentation as well as every visual/material field.
    rows.sort(key=lambda item: item[0])
    encoded = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bone_positions(
    config: AxleConfiguration,
    bones: Iterable[BoneLike],
    request: SteeringGeometryRequest,
) -> dict[int, float]:
    if not MINIMUM_AXLE_PAIRS <= len(config.axles) <= MAXIMUM_AXLE_PAIRS:
        raise SteeringGeometryError("Automatic steering requires 2-5 physical axles")
    ordered = sorted(config.axles, key=lambda item: item.physical_order)
    orders = [item.physical_order for item in ordered]
    if orders != list(range(1, len(ordered) + 1)):
        raise SteeringGeometryError(
            "Physical axle order must be contiguous from front to rear"
        )
    lookup: dict[str, BoneLike] = {}
    for bone in bones:
        name = str(bone.name).strip().casefold()
        if name and name not in lookup:
            lookup[name] = bone
    positions: dict[int, float] = {}
    for axle in ordered:
        try:
            left = lookup[axle.left_bone.casefold()]
            right = lookup[axle.right_bone.casefold()]
        except KeyError as exc:
            raise SteeringGeometryError(
                f"Automatic steering requires both canonical bones for axle "
                f"{axle.physical_order}"
            ) from exc
        try:
            left_y = _finite_float(left.position[1], f"{axle.left_bone} Y position")
            right_y = _finite_float(right.position[1], f"{axle.right_bone} Y position")
        except (IndexError, TypeError) as exc:
            raise SteeringGeometryError(
                f"Axle {axle.physical_order} wheel bones need vehicle-local XYZ positions"
            ) from exc
        if abs(left_y - right_y) > request.pair_position_tolerance:
            raise SteeringGeometryError(
                f"Axle {axle.physical_order} left/right wheel centres disagree "
                "longitudinally; apply or repair the wheel-bone transforms"
            )
        positions[axle.physical_order] = (left_y + right_y) / 2.0
    for leading, trailing in zip(ordered, ordered[1:]):
        gap = positions[leading.physical_order] - positions[trailing.physical_order]
        if gap <= request.position_epsilon:
            raise SteeringGeometryError(
                "Axle centres must have distinct, strictly front-to-rear "
                "vehicle-local Y positions"
            )
    return positions


def _resolve_pivot(
    config: AxleConfiguration,
    positions: Mapping[int, float],
    request: SteeringGeometryRequest,
) -> tuple[float, str, tuple[int, ...]]:
    by_order = {item.physical_order: item for item in config.axles}
    if request.pivot_longitudinal_position is not None:
        return (
            float(request.pivot_longitudinal_position),
            PIVOT_EXPLICIT,
            (),
        )
    if request.pivot_axle_orders:
        pivot_orders = tuple(sorted(request.pivot_axle_orders))
        source = PIVOT_SELECTED_FIXED
    else:
        pivot_orders = tuple(sorted(
            item.physical_order for item in config.axles if not item.steered
        ))
        source = PIVOT_DERIVED_FIXED
    if not pivot_orders:
        raise SteeringGeometryError(
            "All-steer layouts require an explicit neutral-pivot position; "
            "geometry alone cannot infer same-phase versus counter-phase steering"
        )
    unknown = [order for order in pivot_orders if order not in by_order]
    if unknown:
        raise SteeringGeometryError(
            "Pivot selection references unknown axle orders: "
            + ", ".join(str(item) for item in unknown)
        )
    steered = [order for order in pivot_orders if by_order[order].steered]
    if steered:
        raise SteeringGeometryError(
            "Neutral-pivot axle selection must contain only non-steered axles: "
            + ", ".join(str(item) for item in steered)
        )
    return (
        sum(positions[order] for order in pivot_orders) / len(pivot_orders),
        source,
        pivot_orders,
    )


def solve_automatic_steering_geometry(
    config: AxleConfiguration,
    bones: Iterable[BoneLike],
    request: SteeringGeometryRequest | Mapping[str, Any] | None = None,
) -> SteeringGeometrySolution:
    """Calculate signed per-axle steering gains from canonical bone positions.

    Positive gains follow GTA's forward-axle steering command; negative gains
    counter-steer.  Non-steered axles always receive zero, regardless of their
    wheel mesh, visual family, tyre count, or mirrored geometry.  The selected
    reference axle only establishes the normalized lock magnitude, so a rear
    reference correctly receives a negative gain.
    """

    options = (
        request
        if isinstance(request, SteeringGeometryRequest)
        else SteeringGeometryRequest.from_dict(request)
    )
    bone_rows = tuple(bones)
    positions = _bone_positions(config, bone_rows, options)
    bone_position_sha256 = canonical_bone_position_sha256(config, bone_rows)
    pivot, pivot_source, pivot_orders = _resolve_pivot(config, positions, options)
    steered = [item for item in config.axles if item.steered]
    if not steered:
        raise SteeringGeometryError(
            "Automatic steering requires at least one explicitly steered axle"
        )
    candidates = [
        item for item in steered
        if abs(positions[item.physical_order] - pivot) > options.position_epsilon
    ]
    if len(candidates) != len(steered):
        coincident = sorted(
            item.physical_order for item in steered if item not in candidates
        )
        raise SteeringGeometryError(
            "A steered axle cannot coincide with the neutral pivot: "
            + ", ".join(str(item) for item in coincident)
        )
    if options.reference_axle_order is not None:
        reference = next((
            item for item in candidates
            if item.physical_order == options.reference_axle_order
        ), None)
        if reference is None:
            raise SteeringGeometryError(
                "Reference axle must identify a configured steered axle away "
                "from the neutral pivot"
            )
    else:
        # The farthest lever arm guarantees every normalized gain fits the
        # runtime's finite [-1, 1] contract. Prefer the forward axle only as a
        # deterministic tie-breaker. Sign still comes solely from bone position
        # relative to the pivot, so a rear reference correctly normalizes to -1.
        reference = max(
            candidates,
            key=lambda item: (
                abs(positions[item.physical_order] - pivot),
                positions[item.physical_order] - pivot,
            ),
        )
    lock_radians = math.radians(float(options.reference_lock_degrees))
    reference_offset = positions[reference.physical_order] - pivot
    turn_radius = abs(reference_offset) / math.tan(lock_radians)
    if not math.isfinite(turn_radius) or turn_radius <= options.position_epsilon:
        raise SteeringGeometryError(
            "Reference axle geometry cannot produce a stable turn radius"
        )
    rows: list[AxleSteeringGain] = []
    for axle in sorted(config.axles, key=lambda item: item.physical_order):
        position = positions[axle.physical_order]
        offset = position - pivot
        if axle.steered:
            angle = math.atan(offset / turn_radius)
            gain = angle / lock_radians
            if abs(gain) > 1.0 + options.position_epsilon:
                raise SteeringGeometryError(
                    "Reference axle is not the longest steering lever arm; "
                    "select the farthest steered axle so gains remain normalized"
                )
            gain = max(-1.0, min(1.0, gain))
            if abs(gain) <= options.position_epsilon:
                gain = 0.0
            phase = (
                "same" if gain > 0.0
                else "counter" if gain < 0.0
                else "neutral"
            )
        else:
            angle = 0.0
            gain = 0.0
            phase = "fixed"
        rows.append(AxleSteeringGain(
            physical_order=axle.physical_order,
            longitudinal_position=position,
            offset_from_pivot=offset,
            steering_angle_degrees=math.degrees(angle),
            steering_gain=gain,
            phase=phase,
        ))
    return SteeringGeometrySolution(
        pivot_longitudinal_position=pivot,
        pivot_source=pivot_source,
        pivot_axle_orders=pivot_orders,
        reference_axle_order=reference.physical_order,
        reference_lock_degrees=float(options.reference_lock_degrees),
        turn_radius=turn_radius,
        axles=tuple(rows),
        bone_position_sha256=bone_position_sha256,
        pair_position_tolerance=float(options.pair_position_tolerance),
        position_epsilon=float(options.position_epsilon),
    )


def _solution_requires_schema_two(solution: SteeringGeometrySolution) -> bool:
    return any(
        abs(row.steering_gain - (0.0 if row.phase == "fixed" else 1.0))
        > STEERING_GAIN_EPSILON
        for row in solution.axles
    )


def _preserved_runtime_version(existing: Any, required: str) -> str:
    """Raise a runtime floor without discarding a stronger authored floor."""

    def parse(value: Any) -> tuple[int, int, int]:
        text = str(value).strip()
        match = re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", text,
        )
        if match is None:
            raise SteeringGeometryError(
                "Minimum axle runtime version must use major.minor.patch"
            )
        return tuple(int(match.group(index)) for index in (1, 2, 3))

    return str(existing).strip() if parse(existing) >= parse(required) else required


def _automatic_runtime_provenance(
    solution: SteeringGeometrySolution,
) -> dict[str, Any]:
    return {
        "mode": "automaticGeometry",
        "algorithmVersion": STEERING_GEOMETRY_ALGORITHM_VERSION,
        "bonePositionSha256": solution.bone_position_sha256,
        "pivotLongitudinalPosition": solution.pivot_longitudinal_position,
        "pivotSource": solution.pivot_source,
        "pivotAxleOrders": [order - 1 for order in solution.pivot_axle_orders],
        "referenceAxleOrder": solution.reference_axle_order - 1,
        "referenceLockDegrees": solution.reference_lock_degrees,
        "pairPositionTolerance": solution.pair_position_tolerance,
        "positionEpsilon": solution.position_epsilon,
    }


def apply_steering_geometry_to_payload(
    payload: Mapping[str, Any],
    solution: SteeringGeometrySolution,
) -> dict[str, Any]:
    """Return a copied SDK or runtime payload carrying the solved gains.

    Snake-case SDK payloads use one-based ``physical_order``; runtime payloads
    use zero-based ``order``.  Visual fields and dual-tyre declarations are
    copied verbatim and never participate in steering-phase inference.
    """

    result = copy.deepcopy(dict(payload))
    rows = result.get("axles")
    if not isinstance(rows, list):
        raise SteeringGeometryError("Axle configuration payload requires an axle array")
    runtime_shape = any(
        isinstance(row, Mapping) and "order" in row and "physical_order" not in row
        for row in rows
    )
    gains = solution.gain_by_physical_order
    seen: set[int] = set()
    promote = _solution_requires_schema_two(solution)
    for row in rows:
        if not isinstance(row, dict):
            raise SteeringGeometryError("Axle configuration contains an invalid axle row")
        if runtime_shape:
            raw_order = row.get("order")
            if isinstance(raw_order, bool) or not isinstance(raw_order, int):
                raise SteeringGeometryError("Runtime axle order must be an integer")
            order = raw_order + 1
        else:
            raw_order = row.get("physical_order")
            if isinstance(raw_order, bool) or not isinstance(raw_order, int):
                raise SteeringGeometryError("SDK physical axle order must be an integer")
            order = raw_order
        if order not in gains:
            raise SteeringGeometryError(
                f"Steering solution has no gain for physical axle {order}"
            )
        if promote:
            if runtime_shape:
                row["steeringGain"] = gains[order]
            else:
                row["steering_gain"] = gains[order]
        else:
            row.pop("steeringGain", None)
            row.pop("steering_gain", None)
        seen.add(order)
    if seen != set(gains):
        raise SteeringGeometryError(
            "Steering solution and configuration payload axle sets do not match"
        )
    if promote:
        if runtime_shape:
            result["schemaVersion"] = LATEST_AXLE_SCHEMA_VERSION
            result["minimumRuntimeVersion"] = _preserved_runtime_version(
                result.get("minimumRuntimeVersion", "1.0.0"),
                SIGNED_STEERING_RUNTIME_VERSION,
            )
            result["steeringCalculation"] = _automatic_runtime_provenance(solution)
        else:
            result["schema_version"] = LATEST_AXLE_SCHEMA_VERSION
            result["minimum_runtime_version"] = _preserved_runtime_version(
                result.get("minimum_runtime_version", "1.0.0"),
                SIGNED_STEERING_RUNTIME_VERSION,
            )
            result["preset"] = PRESET_CUSTOM
            result["steering_calculation"] = solution.provenance().to_dict()
    else:
        if runtime_shape:
            result["schemaVersion"] = AXLE_SCHEMA_VERSION
            result["minimumRuntimeVersion"] = _preserved_runtime_version(
                result.get("minimumRuntimeVersion", "1.0.0"), "1.0.0",
            )
            result.pop("steeringCalculation", None)
        else:
            result["schema_version"] = AXLE_SCHEMA_VERSION
            result["minimum_runtime_version"] = _preserved_runtime_version(
                result.get("minimum_runtime_version", "1.0.0"), "1.0.0",
            )
            result.pop("steering_calculation", None)
    return result


def apply_steering_geometry_to_configuration(
    config: AxleConfiguration,
    solution: SteeringGeometrySolution,
) -> AxleConfiguration:
    """Copy solved gains into a selectively versioned axle configuration."""

    gains = solution.gain_by_physical_order
    configured = {item.physical_order for item in config.axles}
    if configured != set(gains):
        raise SteeringGeometryError(
            "Steering solution and axle configuration orders do not match"
        )
    axles = tuple(
        replace(axle, steering_gain=gains[axle.physical_order])
        for axle in config.axles
    )
    if not _solution_requires_schema_two(solution):
        return replace(
            config,
            schema_version=AXLE_SCHEMA_VERSION,
            minimum_runtime_version=_preserved_runtime_version(
                config.minimum_runtime_version, "1.0.0",
            ),
            axles=axles,
            steering_calculation=None,
        )
    return replace(
        config,
        schema_version=LATEST_AXLE_SCHEMA_VERSION,
        minimum_runtime_version=_preserved_runtime_version(
            config.minimum_runtime_version, SIGNED_STEERING_RUNTIME_VERSION,
        ),
        preset=PRESET_CUSTOM,
        axles=axles,
        steering_calculation=solution.provenance(),
    )


def apply_manual_steering_gains_to_configuration(
    config: AxleConfiguration,
    bones: Iterable[BoneLike],
    gains: Mapping[int, float],
) -> AxleConfiguration:
    """Apply an exact, author-supplied gain map with bone-only provenance."""

    expected = {axle.physical_order for axle in config.axles}
    if set(gains) != expected:
        raise SteeringGeometryError(
            "Manual steering gains must name every configured physical axle exactly once"
        )
    normalized: dict[int, float] = {}
    for axle in config.axles:
        gain = _finite_float(
            gains[axle.physical_order],
            f"Axle {axle.physical_order} manual steering gain",
        )
        if not -1.0 <= gain <= 1.0:
            raise SteeringGeometryError("Manual steering gains must be between -1 and 1")
        if not axle.steered and abs(gain) > STEERING_GAIN_EPSILON:
            raise SteeringGeometryError(
                f"Non-steered axle {axle.physical_order} must use zero steering gain"
            )
        normalized[axle.physical_order] = gain
    axles = tuple(
        replace(axle, steering_gain=normalized[axle.physical_order])
        for axle in config.axles
    )
    nonlegacy = any(
        abs(normalized[axle.physical_order] - (1.0 if axle.steered else 0.0))
        > STEERING_GAIN_EPSILON
        for axle in config.axles
    )
    if not nonlegacy:
        return replace(
            config,
            schema_version=AXLE_SCHEMA_VERSION,
            minimum_runtime_version=_preserved_runtime_version(
                config.minimum_runtime_version, "1.0.0",
            ),
            axles=axles,
            steering_calculation=None,
        )
    provenance = SteeringCalculationProvenance(
        mode=STEERING_CALCULATION_MANUAL,
        algorithm_version=STEERING_GEOMETRY_ALGORITHM_VERSION,
        bone_position_sha256=canonical_bone_position_sha256(config, tuple(bones)),
    )
    return replace(
        config,
        schema_version=LATEST_AXLE_SCHEMA_VERSION,
        minimum_runtime_version=_preserved_runtime_version(
            config.minimum_runtime_version, SIGNED_STEERING_RUNTIME_VERSION,
        ),
        preset=PRESET_CUSTOM,
        axles=axles,
        steering_calculation=provenance,
    )


__all__ = [
    "DEFAULT_PAIR_POSITION_TOLERANCE",
    "DEFAULT_POSITION_EPSILON",
    "DEFAULT_REFERENCE_LOCK_DEGREES",
    "MAXIMUM_REFERENCE_LOCK_DEGREES",
    "MINIMUM_REFERENCE_LOCK_DEGREES",
    "PIVOT_DERIVED_FIXED",
    "PIVOT_EXPLICIT",
    "PIVOT_SELECTED_FIXED",
    "PIVOT_SOURCES",
    "AxleSteeringGain",
    "SteeringGeometryError",
    "SteeringGeometryRequest",
    "SteeringGeometrySolution",
    "apply_manual_steering_gains_to_configuration",
    "apply_steering_geometry_to_configuration",
    "apply_steering_geometry_to_payload",
    "canonical_bone_position_sha256",
    "solve_automatic_steering_geometry",
]

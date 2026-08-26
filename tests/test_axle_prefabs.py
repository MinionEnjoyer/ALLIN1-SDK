from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import pytest

from allin1_sdk.axle_configurator import AxleConfiguration, EXPORT_FIVEM_RUNTIME
from allin1_sdk.axle_steering_geometry import (
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)
from allin1_sdk.axle_prefabs import (
    AxleBehaviorPrefab,
    AxlePrefabCatalog,
    CanonicalTargetResolver,
    ProjectPrefabCatalog,
    PrefabAxleConfiguration,
    VisualGeometryAsset,
    VisualTyreSelection,
    VisualTyreCatalog,
    apply_prefab,
    apply_visual_package,
    calculate_compatibility,
    confirm_prefab_application,
    confirm_visual_package,
    create_custom_prefab,
    load_prefab_axle_configuration,
    persist_visual_design,
    required_canonical_pairs,
    schematic_text,
    validate_prefab,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def bones(count: int, *, reversed_order: bool = False) -> tuple[Bone, ...]:
    pairs = required_canonical_pairs(count)
    positions = [8.0 - (index * 4.0) for index in range(count)]
    if reversed_order:
        positions[-1] = positions[0] + 2.0
    return tuple(
        Bone(name, (x, positions[index], 0.0))
        for index, pair in enumerate(pairs)
        for name, x in ((pair[0], -1.25), (pair[1], 1.25))
    )


@pytest.fixture(scope="module")
def catalog() -> AxlePrefabCatalog:
    return AxlePrefabCatalog.load_builtin(ROOT)


@pytest.fixture(scope="module")
def tyres() -> VisualTyreCatalog:
    return VisualTyreCatalog.load_builtin(ROOT)


def test_builtin_catalog_is_complete_versioned_and_immutable(catalog):
    assert catalog.schema_version == 1
    assert len(catalog.prefabs) == 27
    assert len({item.id for item in catalog.prefabs}) == 27
    assert len(catalog.source_digest) == 64
    for prefab in catalog.prefabs:
        validate_prefab(prefab, localization=dict(catalog.localization))
        assert len(prefab.pattern.split("-")) == prefab.axle_count
        assert prefab.localization_key in dict(catalog.localization)
        assert prefab.common_use_localization_key in dict(catalog.localization)
    with pytest.raises(FrozenInstanceError):
        catalog.prefabs[0].display_name = "mutated"


def test_every_required_prefab_id_is_present(catalog):
    expected = {
        "4x2_standard", "4x4_all_drive", "6x2_fixed_tag",
        "6x2_rear_steer_bus", "6x2_lift_tag", "6x2_pusher",
        "6x2_steered_pusher", "6x2_twin_steer", "6x4_tandem_drive",
        "6x6_all_drive", "8x2_twin_steer_tag", "8x2_multi_steer",
        "8x4_twin_steer", "8x4_fixed_tag", "8x4_rear_steer",
        "8x6_heavy_traction", "8x8_all_drive", "10x4_multi_steer",
        "10x6_heavy_haul", "10x6_rear_steer", "10x8_heavy_haul",
        "10x10_all_drive", "trailer_tandem", "trailer_tridem",
        "trailer_steered_tag", "trailer_multi_steer", "trailer_lift_front",
    }
    assert {item.id for item in catalog.prefabs} == expected


def test_every_builtin_prefab_resolves_variable_length_runtime_mapping(catalog):
    for prefab in catalog.prefabs:
        preview = apply_prefab(
            prefab.id, f"fixture_{prefab.id}", bones(prefab.axle_count),
            "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
            reported_wheel_count=prefab.axle_count * 2,
        )
        assert not [item for item in preview.findings if item.severity == "error"], prefab.id
        assert len(preview.proposed.axles) == prefab.axle_count
        assert len({
            value for axle in preview.proposed.axles
            for value in (axle.left_runtime_index, axle.right_runtime_index)
        }) == prefab.axle_count * 2


def test_behavior_prefab_preserves_stronger_runtime_floor(catalog):
    wheel_bones = bones(3)
    base = replace(
        apply_prefab(
            "6x2_rear_steer_bus", "runtime_floor_bus", wheel_bones,
            "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
        ).proposed,
        minimum_runtime_version="3.1.0",
    )

    preview = apply_prefab(
        "6x4_tandem_drive", "runtime_floor_bus", wheel_bones,
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, base_config=base,
        catalog=catalog,
    )

    assert preview.proposed.minimum_runtime_version == "3.1.0"


def test_visual_catalog_is_complete(tyres):
    assert tyres.schema_version == 1
    assert {item.id for item in tyres.packages} == {
        "all_singles", "dual_drive", "dual_tandem", "bus_singles",
        "mixed_tag", "offroad_singles", "heavy_duals", "axle_addon_inner",
    }
    assert all(item.localization_key in dict(tyres.localization) for item in tyres.packages)


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (2, (("wheel_lf", "wheel_rf"), ("wheel_lr", "wheel_rr"))),
        (3, (("wheel_lf", "wheel_rf"), ("wheel_lm1", "wheel_rm1"), ("wheel_lr", "wheel_rr"))),
        (4, (("wheel_lf", "wheel_rf"), ("wheel_lm1", "wheel_rm1"), ("wheel_lm2", "wheel_rm2"), ("wheel_lr", "wheel_rr"))),
        (5, (("wheel_lf", "wheel_rf"), ("wheel_lm1", "wheel_rm1"), ("wheel_lm2", "wheel_rm2"), ("wheel_lm3", "wheel_rm3"), ("wheel_lr", "wheel_rr"))),
    ],
)
def test_canonical_mapping_for_two_through_five_axles(count, expected):
    assert required_canonical_pairs(count) == expected


def test_bus_acceptance_fixture_behavior_and_mixed_tag_visuals(catalog, tyres):
    preview = apply_prefab(
        "6x2_rear_steer_bus", "acceptance_bus", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
        reported_wheel_count=6,
    )
    assert preview.can_apply
    config = confirm_prefab_application(preview, confirmed=True)
    assert [(item.steered, item.powered) for item in config.axles] == [
        (True, False), (False, True), (True, False),
    ]
    assert [(item.left_bone, item.right_bone) for item in config.axles] == list(
        required_canonical_pairs(3)
    )
    visual = apply_visual_package("mixed_tag", config, catalog=tyres)
    assert not visual.can_apply
    assert visual.can_persist and visual.design_only
    assert [item.tyre_style for item in visual.axle_states] == [
        "single", "dual", "single",
    ]
    assert visual.axle_states[1].addon_bones == ()
    assert not visual.axle_states[2].uses_inner_addon
    assert visual.runtime_wheel_count_before == visual.runtime_wheel_count_after == 6
    assert all(
        not axle.addon_geometry for axle in visual.proposed.axles
    )
    assert visual.proposed.to_dict()["visual_tyre_package"]["packageId"] == "mixed_tag"


def test_four_axle_heavy_truck_acceptance_fixture(catalog, tyres):
    preview = apply_prefab(
        "8x4_twin_steer", "acceptance_truck", bones(4),
        "story-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
        reported_wheel_count=8,
    )
    assert [(item.steered, item.powered) for item in preview.proposed.axles] == [
        (True, False), (True, False), (False, True), (False, True),
    ]
    visual = apply_visual_package("dual_tandem", preview.proposed, catalog=tyres)
    assert [item.tyre_style for item in visual.axle_states] == [
        "single", "single", "dual", "dual",
    ]
    assert visual.runtime_wheel_count_after == 8


def test_five_axle_crane_acceptance_fixture(catalog):
    preview = apply_prefab(
        "10x4_multi_steer", "acceptance_crane", bones(5),
        "fivem-enhanced", EXPORT_FIVEM_RUNTIME, catalog=catalog,
        reported_wheel_count=10,
    )
    assert preview.can_apply
    assert len(preview.mapping) == 5
    assert [(item.steered, item.powered) for item in preview.proposed.axles] == [
        (True, False), (True, False), (False, True),
        (False, True), (True, False),
    ]
    indices = [
        value for item in preview.proposed.axles
        for value in (item.left_runtime_index, item.right_runtime_index)
    ]
    assert len(indices) == len(set(indices)) == 10


def test_missing_middle_pair_blocks_exact_application(catalog):
    incomplete = tuple(item for item in bones(4) if item.name != "wheel_rm2")
    preview = apply_prefab(
        "8x4_twin_steer", "missing_middle", incomplete,
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    )
    assert not preview.can_apply
    assert any(item.code == "missing_required_bone" for item in preview.findings)


def test_extra_physical_axle_pair_blocks_smaller_prefab(catalog):
    preview = apply_prefab(
        "6x2_fixed_tag", "too_many_axles", bones(4),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    )
    assert not preview.can_apply
    assert any(item.code == "axle_count_mismatch" for item in preview.findings)


def test_incorrect_physical_order_is_reported(catalog):
    preview = apply_prefab(
        "6x2_fixed_tag", "bad_order", bones(3, reversed_order=True),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    )
    assert not preview.can_apply
    assert any(item.code == "physical_order" for item in preview.findings)


def test_game_reported_wheel_count_is_validated_before_apply(catalog):
    preview = apply_prefab(
        "10x4_multi_steer", "wrong_count", bones(5),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
        reported_wheel_count=6,
    )
    assert not preview.can_apply
    assert any(item.code == "runtime_mapping" for item in preview.findings)


def test_preview_has_diff_mapping_flags_and_serializable_output(catalog):
    preview = apply_prefab(
        "6x2_rear_steer_bus", "preview_bus", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
        handling_flags=0x100,
    )
    assert preview.mapping
    assert preview.differences
    assert preview.handling_flags_before == 0x100
    assert preview.handling_flags_after is not None
    assert preview.to_dict()["proposed"]["expected_wheel_count"] == 6


@pytest.mark.parametrize(
    "package_id",
    [
        "all_singles", "dual_drive", "dual_tandem", "bus_singles",
        "mixed_tag", "offroad_singles", "heavy_duals", "axle_addon_inner",
    ],
)
def test_visual_packages_never_add_physics_slots(catalog, tyres, package_id):
    config = apply_prefab(
        "6x4_tandem_drive", "visual_truck", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    selected = (2,) if package_id == "axle_addon_inner" else ()
    preview = apply_visual_package(
        package_id, config, catalog=tyres, selected_axles=selected,
    )
    assert preview.runtime_wheel_count_before == preview.runtime_wheel_count_after
    before = [
        value for item in preview.previous.axles
        for value in (item.left_runtime_index, item.right_runtime_index)
    ]
    after = [
        value for item in preview.proposed.axles
        for value in (item.left_runtime_index, item.right_runtime_index)
    ]
    assert before == after
    assert all(not addon.is_wheel_mesh for axle in preview.proposed.axles for addon in axle.addon_geometry)


def test_visual_package_preserves_signed_geometry_and_bone_evidence(catalog, tyres):
    wheel_bones = bones(3)
    base = apply_prefab(
        "6x2_rear_steer_bus", "signed_visual_bus", wheel_bones,
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    signed = apply_steering_geometry_to_configuration(
        base, solve_automatic_steering_geometry(base, wheel_bones),
    )
    preview = apply_visual_package("all_singles", signed, catalog=tyres)

    assert [item.steering_gain for item in preview.proposed.axles] == [
        item.steering_gain for item in signed.axles
    ]
    assert preview.proposed.steering_calculation == signed.steering_calculation
    assert [
        (item.left_runtime_index, item.right_runtime_index)
        for item in preview.proposed.axles
    ] == [
        (item.left_runtime_index, item.right_runtime_index)
        for item in signed.axles
    ]


def test_shared_middle_rear_template_limitation_is_explicit(catalog, tyres):
    config = apply_prefab(
        "6x2_rear_steer_bus", "shared_family", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    preview = apply_visual_package("mixed_tag", config, catalog=tyres)
    codes = {item.code for item in preview.findings}
    assert {"shared_visual_template", "inner_addon_required"}.issubset(codes)


def test_verified_dual_geometry_is_used_without_placeholder_assets(
    catalog, tyres, tmp_path,
):
    left = tmp_path / "inner_left.ydr"
    right = tmp_path / "inner_right.ydr"
    left.write_bytes(b"verified-left")
    right.write_bytes(b"verified-right")
    config = apply_prefab(
        "6x2_rear_steer_bus", "geometry_bus", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    preview = apply_visual_package(
        "mixed_tag", config, catalog=tyres,
        geometry_assets=(
            VisualGeometryAsset("inner_left", left, "vehicle/tyres/inner_left.ydr"),
            VisualGeometryAsset("inner_right", right, "vehicle/tyres/inner_right.ydr"),
        ),
    )
    assert preview.can_apply and preview.geometry_ready and not preview.design_only
    middle = preview.proposed.axles[1]
    assert [item.asset for item in middle.addon_geometry] == [
        "vehicle/tyres/inner_left.ydr", "vehicle/tyres/inner_right.ydr",
    ]
    assert all(not item.is_wheel_mesh for item in middle.addon_geometry)
    assert dict(preview.selection.parameters) == {
        "geometry_inner_left": "vehicle/tyres/inner_left.ydr",
        "geometry_inner_right": "vehicle/tyres/inner_right.ydr",
    }


def test_singles_selection_persists_without_addon_geometry(catalog, tyres):
    config = apply_prefab(
        "4x2_standard", "single_truck", bones(2),
        "story-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    preview = apply_visual_package(
        "all_singles", config, catalog=tyres,
        parameters={"profile": "stock"},
    )
    assert preview.can_apply and preview.can_persist
    assert isinstance(preview.proposed, PrefabAxleConfiguration)
    payload = preview.proposed.to_dict()
    assert payload["visual_tyre_package"] == {
        "schemaVersion": 1,
        "packageId": "all_singles",
        "selectedAxles": [],
        "parameters": {"profile": "stock"},
    }
    # The shared base parser remains compatible, while the extension-aware
    # parser preserves the visual selection for authoring round trips.
    assert AxleConfiguration.from_dict(payload).vehicle_model == "single_truck"
    loaded = load_prefab_axle_configuration(payload, visual_catalog=tyres)
    assert loaded.visual_tyre_selection == preview.selection
    assert loaded.to_dict() == payload


def test_behavior_prefab_preview_preserves_explicit_singles_selection(catalog):
    preview = apply_prefab(
        "4x2_standard", "prefab_singles", bones(2),
        "story-enhanced", EXPORT_FIVEM_RUNTIME, catalog=catalog,
        tyre_package_id="all_singles",
        visual_parameters={"profile": "factory"},
    )
    assert preview.can_apply
    assert preview.proposed.to_dict()["visual_tyre_package"] == {
        "schemaVersion": 1,
        "packageId": "all_singles",
        "selectedAxles": [],
        "parameters": {"profile": "factory"},
    }


def test_selected_axles_requirement_is_fail_closed(catalog, tyres):
    config = apply_prefab(
        "6x4_tandem_drive", "selected_inner", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    preview = apply_visual_package("axle_addon_inner", config, catalog=tyres)
    assert not preview.can_apply and not preview.can_persist
    assert preview.proposed is config
    assert preview.selection is None
    assert any(item.code == "selected_axles_required" for item in preview.findings)
    with pytest.raises(ValueError, match="cannot be applied"):
        confirm_visual_package(preview, confirmed=True)
    payload = config.to_dict()
    payload["visual_tyre_package"] = {
        "schemaVersion": 1,
        "packageId": "axle_addon_inner",
        "selectedAxles": [],
        "parameters": {},
    }
    with pytest.raises(ValueError, match="requires selected axles"):
        load_prefab_axle_configuration(payload, visual_catalog=tyres)


def test_design_only_dual_selection_can_be_persisted_but_not_confirmed(catalog, tyres):
    config = apply_prefab(
        "6x2_rear_steer_bus", "design_bus", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    preview = apply_visual_package("mixed_tag", config, catalog=tyres)
    assert preview.design_only and preview.can_persist and not preview.can_apply
    saved = persist_visual_design(preview, confirmed=True)
    assert saved.visual_tyre_selection.package_id == "mixed_tag"
    assert not any(
        item.asset.startswith("generated/axle-tyres/")
        for axle in saved.axles for item in axle.addon_geometry
    )
    with pytest.raises(ValueError, match="design-only"):
        confirm_visual_package(preview, confirmed=True)


@pytest.mark.parametrize(
    "package_id",
    ["dual_drive", "dual_tandem", "mixed_tag", "heavy_duals", "axle_addon_inner"],
)
def test_dual_catalog_entries_never_emit_unverified_placeholder_paths(
    catalog, tyres, package_id,
):
    prefab_id = "6x2_rear_steer_bus" if package_id == "mixed_tag" else "6x4_tandem_drive"
    config = apply_prefab(
        prefab_id, f"safe_{package_id}", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    selected = (2,) if package_id == "axle_addon_inner" else ()
    preview = apply_visual_package(
        package_id, config, catalog=tyres, selected_axles=selected,
    )
    assert preview.design_only and not preview.geometry_ready
    assert preview.can_persist and not preview.can_apply
    assert not [
        item.asset for axle in preview.proposed.axles for item in axle.addon_geometry
        if item.asset.startswith("generated/axle-tyres/")
    ]


def test_unsafe_geometry_binding_fails_closed(catalog, tyres, tmp_path):
    missing = tmp_path / "missing.ydr"
    config = apply_prefab(
        "6x2_rear_steer_bus", "unsafe_geometry", bones(3),
        "fivem-legacy", EXPORT_FIVEM_RUNTIME, catalog=catalog,
    ).proposed
    preview = apply_visual_package(
        "mixed_tag", config, catalog=tyres,
        geometry_assets=(
            VisualGeometryAsset("inner_left", missing, "vehicle/inner_left.ydr"),
        ),
    )
    assert not preview.can_apply and not preview.can_persist
    assert preview.proposed is config
    assert any(item.code == "visual_geometry_unsafe" for item in preview.findings)


def test_dual_catalog_cannot_disable_geometry_safety(tyres):
    payload = tyres.to_dict()
    payload.pop("sourceDigest")
    package = next(item for item in payload["packages"] if item["id"] == "mixed_tag")
    package["requiredGeometryKeys"] = []
    package["designOnlyWithoutGeometry"] = False
    with pytest.raises(ValueError, match="must require verified"):
        VisualTyreCatalog.from_dict(payload)


def test_custom_override_keeps_builtin_unchanged_and_round_trips(catalog, tmp_path):
    base = catalog.get("6x2_fixed_tag")
    before = base.to_dict()
    custom = create_custom_prefab(
        base, custom_id="project_rear_steer", display_name="Project rear steer",
        axle_overrides={2: {"steered": True, "steeringRole": "rear"}},
    )
    assert base.to_dict() == before
    assert custom.base_prefab_id == base.id
    assert custom.pattern == "S-D-RS"
    project = ProjectPrefabCatalog().add(custom, builtin_catalog=catalog)
    path = project.write(tmp_path / "axle-prefabs.project.json")
    loaded = ProjectPrefabCatalog.load(path)
    assert loaded.to_dict() == project.to_dict()


def test_compatibility_badges_do_not_overclaim_unvalidated_runtime(catalog):
    stock = calculate_compatibility(catalog.get("4x2_standard"), "stock")
    assert stock.badge == "Stock" and stock.exact_supported
    runtime = calculate_compatibility(
        catalog.get("6x2_rear_steer_bus"), "fivem-legacy",
    )
    assert runtime.badge == "FiveM Runtime"
    assert runtime.requirements_met
    assert runtime.experimental and not runtime.exact_supported
    lift = calculate_compatibility(catalog.get("6x2_lift_tag"), "story-legacy")
    assert lift.badge == "Lift Runtime" and lift.design_only
    trailer = calculate_compatibility(
        catalog.get("trailer_steered_tag"), "fivem-legacy",
    )
    assert trailer.experimental and not trailer.exact_supported


def test_filtering_and_accessible_schematic(catalog):
    matches = catalog.list_prefabs(
        axle_count=5, category="heavy", steering_type="multi",
        drive_type="multiple", experimental=False,
    )
    assert {item.id for item in matches} >= {"10x4_multi_steer", "10x8_heavy_haul"}
    text = schematic_text(catalog.get("6x2_rear_steer_bus"))
    assert "1: S; steer" in text
    assert "3: RS; rear steer" in text


@pytest.mark.parametrize(
    "fixture_name",
    ["three-axle-bus.json", "four-axle-heavy-truck.json", "five-axle-crane.json"],
)
def test_documented_acceptance_fixtures_are_executable(catalog, tyres, fixture_name):
    payload = json.loads(
        (ROOT / "examples" / "axle-prefabs" / fixture_name).read_text("utf-8")
    )
    fixture_bones = tuple(
        Bone(item["name"], tuple(item["position"])) for item in payload["bones"]
    )
    preview = apply_prefab(
        payload["prefabId"], payload["vehicleModel"], fixture_bones,
        payload["target"], payload["exportMode"], catalog=catalog,
        reported_wheel_count=payload["reportedWheelCount"],
    )
    assert preview.can_apply
    if payload.get("visualTyrePackageId"):
        visual = apply_visual_package(
            payload["visualTyrePackageId"], preview.proposed, catalog=tyres,
        )
        assert visual.can_persist
        if visual.package.required_geometry_keys:
            assert visual.design_only and not visual.can_apply
        else:
            assert visual.can_apply


def test_explicit_resolver_is_target_aware():
    mapping = CanonicalTargetResolver().resolve(
        target="story-enhanced",
        pair_names=required_canonical_pairs(5),
        reported_wheel_count=10,
    )
    assert set(mapping) == {
        bone for pair in required_canonical_pairs(5) for bone in pair
    }


def test_invalid_catalog_is_rejected(catalog):
    payload = catalog.to_dict()
    payload.pop("sourceDigest")
    payload["prefabs"][0]["pattern"] = "S-S"
    with pytest.raises(ValueError, match="pattern does not match"):
        AxlePrefabCatalog.from_dict(payload)

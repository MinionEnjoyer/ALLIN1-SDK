from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

import allin1_sdk.vehicle_viewport as vehicle_viewport
from allin1_sdk.vehicle_viewport import VehicleViewportRenderer


class _FixtureScene:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.atlas_calls: list[dict[str, object]] = []
        self.lods = ("High", "Medium")
        self.materials = (
            SimpleNamespace(
                index=0, name="vehicle_paint1",
                texture_parameters=(("DiffuseSampler", "fixture_body"),),
                parameters=(
                    SimpleNamespace(
                        name="specularIntensityMult", source_type="Vector",
                        values=((0.5, 0.0, 0.0, 0.0),),
                    ),
                    SimpleNamespace(
                        name="detailSettings", source_type="Array",
                        values=((1.0, 2.0, 3.0, 4.0), (5.0, 6.0, 7.0, 8.0)),
                    ),
                ),
            ),
            SimpleNamespace(
                index=1, name="vehicle_glass",
                texture_parameters=(("DiffuseSampler", "fixture_glass"),),
                parameters=(),
            ),
            SimpleNamespace(
                index=2, name="vehicle_paint1",
                texture_parameters=(("DiffuseSampler", "fixture_body"),),
                parameters=(
                    SimpleNamespace(
                        name="specularIntensityMult", source_type="Vector",
                        values=((0.5, 0.0, 0.0, 0.0),),
                    ),
                    SimpleNamespace(
                        name="detailSettings", source_type="Array",
                        values=((1.0, 2.0, 3.0, 4.0), (5.0, 6.0, 7.0, 8.0)),
                    ),
                ),
            ),
        )
        self.geometries = (
            SimpleNamespace(
                material_name="vehicle_paint1", triangles=((0, 1, 2),) * 8,
                lod="High", component="Chassis",
            ),
        )
        self.bones = (object(),) * 4
        self.components = (
            SimpleNamespace(
                name="Chassis", lod="High", geometry_count=2,
                vertex_count=12, triangle_count=8,
                material_names=("vehicle_paint1",),
                texture_names=("fixture_body",),
            ),
        )

    def render(self, **options: object) -> tuple[bytes, dict[str, object]]:
        self.calls.append(options)
        output = io.BytesIO()
        Image.new("RGB", (16, 12), (31, 112, 75)).save(output, format="PNG")
        return output.getvalue(), {
            "model_camera_lod": options.get("lod") or "All",
            "model_camera_component": options.get("component") or "All",
            "model_camera_material": options.get("material") or "All",
            "model_rendered_triangles": 8,
        }

    def render_uv_atlas(self, **options: object) -> tuple[bytes, dict[str, object]]:
        self.atlas_calls.append(options)
        output = io.BytesIO()
        Image.new("RGB", (32, 40), (19, 38, 29)).save(output, format="PNG")
        return output.getvalue(), {
            "width": 32,
            "height": 40,
            "triangle_budget": 45_000,
            "source_triangle_count": 8,
            "sampled_triangle_count": 8,
            "rendered_triangle_count": 8,
            "valid_triangle_count": 8,
            "degenerate_triangle_count": 0,
            "missing_triangle_count": 0,
            "seam_triangle_count": 0,
            "island_count": 1,
            "texture_group_count": 1,
            "returned_texture_group_count": 1,
            "sampled": False,
            "texture_groups": [{
                "name": "fixture_body", "resolved": True,
                "material_names": ["vehicle_paint1"], "geometry_count": 1,
                "sampled_triangle_count": 8, "valid_triangle_count": 8,
                "rendered_triangle_count": 8, "island_count": 1,
                "seam_triangle_count": 0, "degenerate_triangle_count": 0,
                "missing_triangle_count": 0,
            }],
            "selection": {
                "lod": options.get("lod") or "All",
                "component": options.get("component") or "All",
                "material": options.get("material") or "All",
            },
            "fidelity": "fixture UV atlas",
            "read_only": True,
        }


def test_vehicle_viewport_revalidates_members_and_reuses_bounded_scene_cache(
    tmp_path, monkeypatch,
):
    package = tmp_path / "package"
    package.mkdir()
    (package / "fixture.yft").write_bytes(b"RSC7" + (b"\0" * 60))
    scene = _FixtureScene()
    inspections: list[tuple[str, str]] = []

    class _FixtureInspector:
        def __init__(self, _project_root, _gta_path) -> None:
            pass

        def inspect_bytes(self, name, _data, *, edition, truncated):
            inspections.append((name, edition))
            assert truncated is False
            return SimpleNamespace(model_scene=scene, warnings=())

    monkeypatch.setattr(vehicle_viewport, "NativeAssetInspector", _FixtureInspector)
    renderer = VehicleViewportRenderer(
        tmp_path, artifact_root=tmp_path / "cache",
    )

    first = renderer.render(
        package, "fixture.yft", yaw=34, pitch=24,
        render_mode="shaded", quality="final",
    )
    second = renderer.render(
        package, "fixture.yft", yaw=80, pitch=-12,
        lod="High", component="Chassis",
        render_mode="wireframe", quality="interactive",
    )

    assert inspections == [("fixture.yft", "Enhanced")]
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["camera"] == {
        "yaw": 80.0, "pitch": -12.0, "lod": "High",
        "component": "Chassis", "material": "All",
            "render_mode": "wireframe",
            "quality": "interactive",
            "collision_visible": False,
        }
    assert second["scene"]["component_count"] == 1
    assert second["scene"]["bone_count"] == 4
    assert scene.calls[-1]["quality"] == "interactive"
    assert (tmp_path / "cache" / f"{second['artifact']['sha256']}.png").is_file()


def test_vehicle_viewport_resolves_material_bindings_against_linked_ytd(
    tmp_path, monkeypatch,
):
    package = tmp_path / "package"
    package.mkdir()
    (package / "fixture.yft").write_bytes(b"RSC7" + (b"\0" * 60))
    (package / "fixture.ytd").write_bytes(b"RSC7" + (b"\1" * 60))
    scene = _FixtureScene()
    inspections: list[str] = []
    contact_sheet = io.BytesIO()
    Image.new("RGB", (32, 24), (190, 42, 61)).save(contact_sheet, format="PNG")

    class _FixtureInspector:
        def __init__(self, _project_root, _gta_path) -> None:
            pass

        def inspect_bytes(self, name, _data, *, edition, truncated):
            del edition
            assert truncated is False
            inspections.append(name)
            if name.endswith(".ytd"):
                return SimpleNamespace(
                    model_scene=None, warnings=(), image_png=contact_sheet.getvalue(),
                    metadata={"exported_textures": 1},
                    texture_previews=(SimpleNamespace(
                        name="fixture_body", file_name="fixture_body.dds",
                        width=1024, height=1024, mip_levels=10,
                        format="DXT5", usage="DEFAULT", size=4096,
                        sha256="b" * 64, thumbnail_png=contact_sheet.getvalue(),
                        warnings=(),
                    ),),
                )
            return SimpleNamespace(model_scene=scene, warnings=())

    monkeypatch.setattr(vehicle_viewport, "NativeAssetInspector", _FixtureInspector)
    renderer = VehicleViewportRenderer(tmp_path, artifact_root=tmp_path / "cache")

    first = renderer.render(
        package, "fixture.yft", material="vehicle_paint1",
        texture_entry="fixture.ytd", render_mode="textured",
    )
    second = renderer.render(
        package, "fixture.yft", material="vehicle_paint1",
        texture_entry="fixture.ytd", render_mode="uvs",
    )
    third = renderer.render(
        package, "fixture.yft", material="vehicle_paint1",
        texture_entry="fixture.ytd", render_mode="uvs",
    )

    assert inspections == ["fixture.yft", "fixture.ytd"]
    assert first["camera"]["material"] == "vehicle_paint1"
    assert first["camera"]["render_mode"] == "textured"
    sampled = scene.calls[0]["textures"]
    assert isinstance(sampled, dict)
    assert sampled["fixture_body"].getpixel((0, 0)) == (190, 42, 61)
    assert first["scene"]["materials"][0]["texture_bindings"] == [{
        "slot": "DiffuseSampler", "name": "fixture_body", "resolved": True,
    }]
    assert first["scene"]["materials"][0]["parameter_count"] == 2
    assert first["scene"]["materials"][0]["parameters"] == [
        {
            "name": "specularIntensityMult", "source_type": "Vector",
            "values": [[0.5, 0.0, 0.0, 0.0]], "record_count": 2,
        },
        {
            "name": "detailSettings", "source_type": "Array",
            "values": [
                [1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0],
            ],
            "record_count": 2,
        },
    ]
    assert first["scene"]["materials"][1]["texture_bindings"][0]["resolved"] is False
    assert first["texture_dictionary"]["texture_count"] == 1
    assert first["texture_dictionary"]["cache_hit"] is False
    assert second["texture_dictionary"]["cache_hit"] is True
    assert second["camera"]["render_mode"] == "uvs"
    assert isinstance(scene.calls[1]["textures"], dict)
    assert second["texture_dictionary"]["artifact"]["media_type"] == "image/png"
    assert second["uv_atlas"]["artifact"]["media_type"] == "image/png"
    assert second["uv_atlas"]["island_count"] == 1
    assert second["uv_atlas"]["cache_hit"] is False
    assert third["uv_atlas"]["cache_hit"] is True
    assert len(scene.atlas_calls) == 1
    assert scene.atlas_calls[0]["material"] == "vehicle_paint1"


def test_vehicle_viewport_overlays_package_owned_collision_scene(
    tmp_path, monkeypatch,
):
    package = tmp_path / "package"
    package.mkdir()
    (package / "fixture.yft").write_bytes(b"RSC7" + (b"\0" * 60))
    (package / "fixture.ybn").write_bytes(b"RSC7" + (b"\2" * 60))
    model_scene = _FixtureScene()
    collision_scene = SimpleNamespace(
        primitive_counts=(("Box", 1), ("Capsule", 2), ("Triangle", 6)),
        owner_count=2,
        render_triangle_count=10,
        bounds={
            "min": [-1.0, -2.0, -0.5],
            "max": [1.0, 2.0, 1.5],
            "size": [2.0, 4.0, 2.0],
        },
    )
    inspections: list[str] = []

    class _FixtureInspector:
        def __init__(self, _project_root, _gta_path) -> None:
            pass

        def inspect_bytes(self, name, _data, *, edition, truncated):
            del edition
            assert truncated is False
            inspections.append(name)
            if name.endswith(".ybn"):
                return SimpleNamespace(
                    collision_scene=collision_scene,
                    warnings=(),
                    metadata={
                        "collision_geometry_count": 2,
                        "collision_vertex_count": 24,
                        "collision_polygon_count": 9,
                        "collision_material_count": 3,
                    },
                )
            return SimpleNamespace(model_scene=model_scene, warnings=())

    monkeypatch.setattr(vehicle_viewport, "NativeAssetInspector", _FixtureInspector)
    renderer = VehicleViewportRenderer(tmp_path, artifact_root=tmp_path / "cache")

    first = renderer.render(
        package, "fixture.yft", collision_entry="fixture.ybn",
        collision_visible=True,
    )
    second = renderer.render(
        package, "fixture.yft", collision_entry="fixture.ybn",
        collision_visible=False,
    )

    assert inspections == ["fixture.yft", "fixture.ybn"]
    assert first["camera"]["collision_visible"] is True
    assert second["camera"]["collision_visible"] is False
    assert first["collision_dictionary"]["primitive_counts"] == [
        {"kind": "Box", "count": 1, "overlay": True, "fidelity": "diagnostic hull"},
        {"kind": "Capsule", "count": 2, "overlay": False, "fidelity": "count only"},
        {"kind": "Triangle", "count": 6, "overlay": True, "fidelity": "exact mesh"},
    ]
    assert first["collision_dictionary"]["overlay_polygon_count"] == 7
    assert first["collision_dictionary"]["unrendered_polygon_count"] == 2
    assert second["collision_dictionary"]["cache_hit"] is True
    assert model_scene.calls[0]["collision_scene"] is collision_scene
    assert model_scene.calls[0]["collision_visible"] is True


def test_vehicle_viewport_rejects_non_models_and_package_traversal(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "notes.txt").write_text("fixture", encoding="utf-8")
    renderer = VehicleViewportRenderer(
        tmp_path, artifact_root=tmp_path / "cache",
    )

    with pytest.raises(ValueError, match="only YFT, YDR, or YDD"):
        renderer.render(package, "notes.txt")
    with pytest.raises(ValueError, match="Unsafe package member path"):
        renderer.render(package, "../notes.txt")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "34"])
def test_vehicle_viewport_requires_finite_numeric_camera_values(tmp_path, value):
    package = tmp_path / "package"
    package.mkdir()
    (package / "fixture.yft").write_bytes(b"RSC7" + (b"\0" * 60))
    renderer = VehicleViewportRenderer(
        tmp_path, artifact_root=tmp_path / "cache",
    )
    with pytest.raises(ValueError, match="finite number"):
        renderer.render(package, "fixture.yft", yaw=value)

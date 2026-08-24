from __future__ import annotations

import io

import pytest
from lxml import etree
from PIL import Image, ImageChops

from allin1_sdk import native_assets


def _scene(*, repeated_triangles: int = 1) -> native_assets.NativeModelScene:
    left = native_assets._ModelGeometry(
        vertices=(
            (-1.5, -1.0, 0.0), (-0.1, -1.0, 0.0),
            (-0.1, 1.0, 0.0), (-1.5, 1.0, 0.0),
        ),
        triangles=((0, 1, 2), (0, 2, 3)) * repeated_triangles,
        lod="High",
        component="Body",
        material_index=0,
        material_name="vehicle_paint",
        texture_names=("body_diff",),
    )
    right = native_assets._ModelGeometry(
        vertices=(
            (0.1, -1.0, 0.25), (1.5, -1.0, 0.25),
            (1.5, 1.0, 0.25), (0.1, 1.0, 0.25),
        ),
        triangles=((0, 1, 2), (0, 2, 3)) * repeated_triangles,
        lod="High",
        component="Windows",
        material_index=1,
        material_name="vehicle_glass",
        texture_names=("glass_diff",),
    )
    return native_assets.NativeModelScene(
        "renderer-fixture.ydr", (left, right),
        materials=(
            native_assets.NativeModelMaterial(0, "vehicle_paint", ("body_diff",)),
            native_assets.NativeModelMaterial(1, "vehicle_glass", ("glass_diff",)),
        ),
    )


def _image(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as opened:
        return opened.convert("RGB").copy()


def test_native_model_render_modes_are_deterministic_and_distinct():
    scene = _scene()
    images: dict[str, bytes] = {}
    for mode in ("shaded", "materials", "wireframe"):
        rendered, metadata = scene.render(render_mode=mode)
        assert rendered.startswith(b"\x89PNG")
        assert scene.render(render_mode=mode)[0] == rendered
        assert metadata["model_render_mode"] == mode
        assert metadata["model_render_quality"] == "final"
        assert metadata["model_rendered_triangle_count"] == 4
        assert metadata["model_render_skipped_triangle_count"] == 0
        assert metadata["model_render_sampled"] is False
        assert metadata["model_render_material_count"] == 2
        assert metadata["model_render_depth_ordering"] == (
            "far-to-near triangle painter"
        )
        assert metadata["model_render_bounds_source"] == "cached selection bounds"
        assert metadata["model_render_view_box"] == (38, 76, 922, 624)
        assert "game shaders" in metadata["model_render_fidelity"]
        images[mode] = rendered

    assert len(set(images.values())) == 3
    # Wireframe is edge-only: it must affect materially fewer pixels than a
    # filled material-ID render over the same diagnostic background.
    wireframe = _image(images["wireframe"])
    material = _image(images["materials"])
    assert ImageChops.difference(wireframe, material).getbbox() is not None
    paint = native_assets._material_mode_color(
        (45, 132, 77), "0|vehicle_paint|body_diff",
    )
    material_pixels = {
        color: count for count, color in material.getcolors(maxcolors=1_000_000) or ()
    }
    wireframe_pixels = {
        color: count for count, color in wireframe.getcolors(maxcolors=1_000_000) or ()
    }
    assert material_pixels.get(paint, 0) > 1_000
    assert wireframe_pixels.get(paint, 0) == 0


def test_native_model_render_uses_semantic_material_colours():
    scene = _scene()
    rendered, metadata = scene.render(render_mode="materials")
    colors = _image(rendered).getcolors(maxcolors=1_000_000)
    assert colors is not None
    assert "glass" in metadata["model_render_semantic_materials"]
    assert "paint" in metadata["model_render_semantic_materials"]
    # A flat material pass deliberately assigns strong, stable material-ID
    # colours rather than reusing the restrained shaded palette.
    visible = {color for _count, color in colors}
    assert native_assets._material_mode_color(
        (45, 132, 77), "0|vehicle_paint|body_diff",
    ) in visible
    assert native_assets._material_mode_color(
        (78, 114, 134), "1|vehicle_glass|glass_diff",
    ) in visible


def test_native_model_shaded_fallbacks_stay_in_a_cohesive_neutral_range():
    colors = []
    for index, name in enumerate(("shader_alpha", "mesh_detail", "misc_surface")):
        geometry = native_assets._ModelGeometry(
            vertices=((0.0, 0.0, 0.0),), triangles=(), lod="High",
            component=f"Part {index}", material_index=index, material_name=name,
        )
        identity, semantic, color = native_assets._model_material_identity(geometry)
        assert identity == name
        assert semantic == "neutral fallback"
        assert max(color) - min(color) <= 10
        assert all(89 <= channel <= 115 for channel in color)
        colors.append(color)
    assert len(set(colors)) == 3


def test_native_model_render_honours_bounded_quality_and_override():
    scene = _scene(repeated_triangles=20)
    rendered, metadata = scene.render(
        render_mode="shaded", quality="interactive", triangle_budget=7,
    )
    assert rendered.startswith(b"\x89PNG")
    assert metadata["model_triangle_count"] == 80
    assert metadata["model_render_triangle_budget"] == 7
    assert metadata["model_rendered_triangle_count"] == 7
    assert metadata["model_render_skipped_triangle_count"] == 73
    assert metadata["model_render_sampled"] is True
    assert metadata["model_render_sample_underlay"] is True


def test_interactive_triangle_sample_uses_direct_indices_not_full_mesh_scan():
    class IndexOnlyTriangles:
        def __init__(self, count: int) -> None:
            self.count = count
            self.indexed = 0

        def __len__(self) -> int:
            return self.count

        def __getitem__(self, index: int) -> tuple[int, int, int]:
            if not 0 <= index < self.count:
                raise IndexError(index)
            self.indexed += 1
            return (0, 1, 2)

        def __iter__(self):
            raise AssertionError("interactive rendering must not scan omitted triangles")

    triangles = IndexOnlyTriangles(500_000)
    geometry = native_assets._ModelGeometry(
        vertices=((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0)),
        triangles=triangles,  # type: ignore[arg-type]
        lod="High", component="Body", material_name="vehicle_paint",
    )
    scene = native_assets.NativeModelScene("large-model.yft", (geometry,))

    rendered, metadata = scene.render(
        quality="interactive", triangle_budget=31,
    )

    assert rendered.startswith(b"\x89PNG")
    assert triangles.indexed == 31
    assert metadata["model_rendered_triangle_count"] == 31
    assert metadata["model_render_skipped_triangle_count"] == 499_969

    scene.render(yaw=73.0, quality="interactive", triangle_budget=31)
    assert triangles.indexed == 31


def test_orbit_frames_reuse_immutable_material_classification(monkeypatch):
    scene = _scene(repeated_triangles=10)
    expected_image, expected_metadata = scene.render(
        yaw=81.0, render_mode="shaded", quality="interactive",
    )

    def unexpected_reclassification(_geometry):
        raise AssertionError("scene material classification should be cached")

    monkeypatch.setattr(
        native_assets, "_model_material_identity", unexpected_reclassification,
    )
    first_image, first_metadata = scene.render(
        yaw=81.0, render_mode="shaded", quality="interactive",
    )
    second_image, second_metadata = scene.render(
        yaw=81.0, render_mode="shaded", quality="interactive",
    )

    assert first_image == second_image == expected_image
    assert first_metadata == second_metadata == expected_metadata


def test_render_image_fast_path_matches_png_pixels_and_metadata():
    scene = _scene(repeated_triangles=12)

    image, image_metadata = scene.render_image(
        yaw=57.0, pitch=19.0, render_mode="shaded", quality="interactive",
    )
    encoded, encoded_metadata = scene.render(
        yaw=57.0, pitch=19.0, render_mode="shaded", quality="interactive",
    )

    assert image.mode == "RGB"
    assert image.size == (960, 680)
    assert image.tobytes() == _image(encoded).tobytes()
    assert image_metadata == encoded_metadata
    # Each call owns its output image; callers may resize/crop it without
    # mutating the cached background or a later frame.
    image.putpixel((0, 0), (255, 0, 255))
    later, _metadata = scene.render_image(quality="interactive")
    assert later.getpixel((0, 0)) != (255, 0, 255)


def test_native_model_full_quality_renders_every_decoded_triangle():
    scene = _scene(repeated_triangles=20)
    _rendered, metadata = scene.render(render_mode="shaded", quality="full")
    assert metadata["model_render_quality"] == "full"
    assert metadata["model_render_triangle_budget"] == (
        native_assets.MAX_MODEL_TRIANGLES
    )
    assert metadata["model_rendered_triangle_count"] == 80
    assert metadata["model_render_skipped_triangle_count"] == 0
    assert metadata["model_render_sampled"] is False
    assert metadata["model_render_sample_underlay"] is False

    # Full is the only tier allowed to raise an explicit budget above the normal
    # 45k diagnostic cap, while remaining inside the decoded-scene guard.
    _image_data, overridden = scene.render(
        quality="full", triangle_budget=native_assets.MAX_RENDERED_TRIANGLES + 1,
    )
    assert overridden["model_render_triangle_budget"] == (
        native_assets.MAX_RENDERED_TRIANGLES + 1
    )


def test_native_model_recognizes_common_hashed_vehicle_shaders():
    expected = {
        "hash_F9FB7331": "paint",
        "hash_1D5F09CE": "tyre",
        "hash_FFE6FBEA": "decal",
        "hash_2A92AEE4": "interior",
        "hash_7C98D207": "glass",
        "hash_E515A6E7": "light",
    }
    for index, (name, semantic) in enumerate(expected.items()):
        geometry = native_assets._ModelGeometry(
            vertices=((0.0, 0.0, 0.0),), triangles=(), lod="High",
            component=f"Part {index}", material_index=index, material_name=name,
        )
        assert native_assets._model_material_identity(geometry)[1] == semantic


def test_native_model_decoder_preserves_uvs_and_sampler_roles():
    shader = etree.fromstring(b"""
    <Item><Name>vehicle_interior</Name><Parameters>
      <Item name="DiffuseSampler" type="Texture"><Name>seat_bc</Name></Item>
      <Item name="BumpSampler" type="Texture"><Name>seat_n</Name></Item>
      <Item name="SpecSampler" type="Texture"><Name>seat_s</Name></Item>
    </Parameters></Item>
    """)
    material = native_assets._model_material_record(shader, 0)
    vertex_buffer = etree.fromstring(b"""
    <VertexBuffer>
      <Layout><Position/><Normal/><Colour0/><TexCoord0/></Layout>
      <Data>
        0 0 0  0 0 1  255 255 255 255  0.25 0.75
        1 0 0  0 0 1  255 255 255 255  0.75 0.75
        0 1 0  0 0 1  255 255 255 255  0.25 0.25
      </Data>
    </VertexBuffer>
    """)

    geometry = native_assets._read_model_geometry(
        vertex_buffer, materials=(material,),
    )

    assert geometry is not None
    assert geometry.texcoords == ((0.25, 0.75), (0.75, 0.75), (0.25, 0.25))
    assert material.texture_names == ("seat_bc", "seat_n", "seat_s")
    assert material.texture_parameters == (
        ("DiffuseSampler", "seat_bc"),
        ("BumpSampler", "seat_n"),
        ("SpecSampler", "seat_s"),
    )


def test_fragment_wheel_children_use_bones_and_fill_empty_right_side(tmp_path):
    xml = tmp_path / "vehicle.yft.xml"
    xml.write_text("""<?xml version="1.0"?>
<Fragment>
 <Drawable>
  <ShaderGroup><Shaders><Item><Name>vehicle_tire</Name><Parameters>
   <Item name="DiffuseSampler" type="Texture"><Name>tire_a_d</Name></Item>
  </Parameters></Item></Shaders></ShaderGroup>
  <Skeleton><Bones>
   <Item><Name>chassis</Name><Tag value="0"/><Index value="0"/>
    <ParentIndex value="-1"/><Translation x="0" y="0" z="0"/>
    <Rotation x="0" y="0" z="0" w="1"/><Scale x="1" y="1" z="1"/></Item>
   <Item><Name>wheel_lf</Name><Tag value="101"/><Index value="1"/>
    <ParentIndex value="0"/><Translation x="-2" y="3" z="4"/>
    <Rotation x="0" y="0" z="0" w="1"/><Scale x="1" y="1" z="1"/></Item>
   <Item><Name>wheel_lr</Name><Tag value="102"/><Index value="2"/>
    <ParentIndex value="0"/><Translation x="-2" y="-3" z="4"/>
    <Rotation x="0" y="0" z="0" w="1"/><Scale x="1" y="1" z="1"/></Item>
   <Item><Name>wheel_rr</Name><Tag value="103"/><Index value="3"/>
    <ParentIndex value="0"/><Translation x="2" y="-3" z="4"/>
    <Rotation x="0" y="0" z="0" w="1"/><Scale x="1" y="1" z="1"/></Item>
   <Item><Name>wheel_rf</Name><Tag value="104"/><Index value="4"/>
    <ParentIndex value="0"/><Translation x="2" y="3" z="4"/>
    <Rotation x="0" y="0" z="0" w="1"/><Scale x="1" y="1" z="1"/></Item>
  </Bones></Skeleton>
 </Drawable>
 <Physics><LOD1><Children>
  <Item><BoneTag value="101"/><Drawable><Matrix>
   1 0 0
   0 1 0
   0 0 1
   0 0 0
  </Matrix><DrawableModelsHigh><Item><HasSkin value="0"/><Geometries><Item>
   <ShaderIndex value="0"/><VertexBuffer><Layout><Position/><TexCoord0/></Layout><Data>
    0.2 0.3 0.4  0 0
    0.4 0.3 0.4  1 0
    0.2 0.5 0.4  0 1
   </Data></VertexBuffer><IndexBuffer><Data>0 1 2</Data></IndexBuffer>
  </Item></Geometries></Item></DrawableModelsHigh></Drawable></Item>
  <Item><BoneTag value="102"/><Drawable><Matrix>
   1 0 0
   0 1 0
   0 0 1
   0 0 0
  </Matrix><DrawableModelsHigh><Item><HasSkin value="0"/><Geometries><Item>
   <ShaderIndex value="0"/><VertexBuffer><Layout><Position/><TexCoord0/></Layout><Data>
    0.1 0.2 0.3  0 0
    0.3 0.2 0.3  1 0
    0.1 0.4 0.3  0 1
   </Data></VertexBuffer><IndexBuffer><Data>0 1 2</Data></IndexBuffer>
  </Item></Geometries></Item></DrawableModelsHigh></Drawable></Item>
  <Item><BoneTag value="103"/><Drawable/></Item>
  <Item><BoneTag value="104"/><Drawable/></Item>
 </Children></LOD1></Physics>
</Fragment>
""", encoding="utf-8")

    scene, metadata, warning = native_assets._model_scene_from_xml(
        xml, "vehicle.yft",
    )

    assert warning is None
    assert scene is not None
    assert [item.component for item in scene.geometries] == [
        "wheel_lf", "wheel_rf", "wheel_lr", "wheel_rr",
    ]
    assert scene.geometries[0].vertices[0] == pytest.approx((-1.8, 3.3, 4.4))
    # GTA's right-wheel convention is a 180-degree Y rotation: X and Z flip,
    # Y and triangle winding remain unchanged.
    assert scene.geometries[1].vertices[0] == pytest.approx((1.8, 3.3, 3.6))
    assert scene.geometries[1].triangles == ((0, 1, 2),)
    assert scene.geometries[3].vertices[0] == pytest.approx((1.9, -2.8, 3.7))
    assert scene.geometries[0].texcoords == scene.geometries[1].texcoords
    assert all(item.material_name == "vehicle_tire" for item in scene.geometries)
    assert all(item.texture_names == ("tire_a_d",) for item in scene.geometries)
    assert len(scene.geometries) == 4
    assert metadata["model_component_count"] == 4
    assert metadata["model_fragment_child_transformed_geometry_count"] == 2
    assert metadata["model_fragment_mirrored_geometry_count"] == 2
    assert metadata["model_fragment_assembled_components"] == (
        "wheel_lf, wheel_lr, wheel_rf, wheel_rr"
    )


@pytest.mark.parametrize("mode", ["", "textured", "SHADOWS"])
def test_native_model_render_rejects_unknown_modes(mode):
    with pytest.raises(ValueError, match="render mode must be one of"):
        _scene().render(render_mode=mode)


@pytest.mark.parametrize("quality", ["", "ultra", "draft"])
def test_native_model_render_rejects_unknown_quality(quality):
    with pytest.raises(ValueError, match="render quality must be one of"):
        _scene().render(quality=quality)


@pytest.mark.parametrize(
    "budget", [0, -1, True, 1.5, native_assets.MAX_RENDERED_TRIANGLES + 1],
)
def test_native_model_render_rejects_unsafe_triangle_budgets(budget):
    with pytest.raises(ValueError, match="triangle budget must be an integer"):
        _scene().render(triangle_budget=budget)

import hashlib
import base64
import io

from PIL import Image
import pytest

from allin1_sdk import optimization_texture as optimization


def source(tmp_path, alpha=255):
    file = tmp_path / "texture.dds"
    image = Image.new("RGBA", (16, 8))
    image.putdata([(x*13 % 256, x*23 % 256, x*41 % 256, alpha) for x in range(128)])
    image.save(file, format="DDS")
    image.close()
    return file


@pytest.mark.parametrize("format_name", ["RGBA8", "DXT1", "DXT3", "DXT5"])
def test_candidates_measure_real_pixels_cost_and_keep_source_unchanged(tmp_path, format_name):
    file = source(tmp_path)
    original = file.read_bytes()
    result = optimization.preview(file, {"format": format_name, "mips": 5, "role": "color"})
    assert result["before"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert result["after"]["all_mips_decoded"]
    assert result["storage_delta_bytes"] == result["after"]["storage_bytes"] - 512
    assert result["preview_before"].startswith("data:image/png;base64,")
    assert result["preview_after"].startswith("data:image/png;base64,")
    assert result["runtime_status"] == "not_tested"
    if format_name == "RGBA8":
        assert result["quality"]["maximum_absolute_error_rgba"] == [0, 0, 0, 0]
    else:
        assert max(result["quality"]["maximum_absolute_error_rgba"][:3]) > 0
        assert result["storage_delta_bytes"] < 0
    assert file.read_bytes() == original


@pytest.mark.parametrize("role", ["unknown", "normal", "mask", "data", None])
def test_role_is_never_guessed(tmp_path, role):
    with pytest.raises(ValueError, match="material role"):
        optimization.preview(source(tmp_path), {"format":"DXT5", "mips":1, "role":role})


def test_alpha_loss_and_competing_output_are_blocked(tmp_path):
    file = source(tmp_path, 128)
    with pytest.raises(ValueError, match="alpha"):
        optimization.preview(file, {"format":"DXT1", "mips":1, "role":"color"})
    output = tmp_path / "candidate.dds"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        optimization.candidate(file, output, {"format":"RGBA8", "mips":1, "role":"color"})
    assert output.read_bytes() == b"existing"


def test_exact_regions_preserve_pixels_and_expose_missing_authored_mips(tmp_path):
    file = source(tmp_path, 128)
    destination = tmp_path/"candidate.dds"
    result = optimization.candidate(file, destination, {"format":"RGBA8","mips":5,"role":"color"},
        region={"mip":0,"x":7,"y":3,"channel":"alpha"})
    region = result["preview_region"]
    assert (region["width"],region["height"]) == (9,5)
    with Image.open(io.BytesIO(base64.b64decode(region["before"].split(",")[1]))) as image:
        assert image.size == (9,5) and image.getextrema() == (128,128)
    with Image.open(io.BytesIO(base64.b64decode(region["difference"].split(",")[1]))) as image:
        assert image.getextrema() == (0,0)
    generated = optimization.region_preview(file,destination,result["before"],result["after"],{"mip":2,"x":0,"y":0,"channel":"rgb"})
    assert generated["before"] is None and generated["difference"] is None and generated["after"]
    assert (generated["mip_width"],generated["mip_height"]) == (4,2)


@pytest.mark.parametrize("region",[{"mip":0,"x":16,"y":0,"channel":"rgba"},{"mip":True,"x":0,"y":0,"channel":"rgb"},
    {"mip":0,"x":0,"y":0,"channel":"guess"},{"mip":9,"x":0,"y":0,"channel":"rgb"}])
def test_outside_or_ambiguous_pixel_requests_rejected(tmp_path,region):
    with pytest.raises(ValueError):
        optimization.candidate(source(tmp_path),tmp_path/"out.dds",{"format":"RGBA8","mips":1,"role":"color"},region=region)

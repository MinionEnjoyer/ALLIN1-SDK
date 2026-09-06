import struct
import os
from pathlib import Path

from PIL import Image
import pytest

from allin1_sdk.texture_conversion import convert_dds
from allin1_sdk.texture_validation import inspect, mip_costs
from allin1_sdk import package_validation


@pytest.mark.parametrize("format_name",["RGBA8","DXT1","DXT3","DXT5"])
@pytest.mark.parametrize("size",[(16,8),(7,3),(1,1)])
def test_all_mips_and_storage_bytes_are_verified(tmp_path,format_name,size):
    original=tmp_path/"original.dds";output=tmp_path/"output.dds"
    Image.new("RGBA",size,(50,100,150,200)).save(original,format="DDS")
    convert_dds(original,output,format_name,max(size).bit_length())
    value=inspect(output)
    assert value["storage_bytes"]==output.stat().st_size-128
    assert value["all_mips_decoded"] and value["full_chain"]
    assert value["trailing_bytes"]==0


def test_missing_lower_mip_data_is_not_a_valid_texture(tmp_path):
    file=tmp_path/"truncated.dds"
    Image.new("RGBA",(8,8)).save(file,format="DDS")
    data=bytearray(file.read_bytes());struct.pack_into("<I",data,28,4);file.write_bytes(data)
    with pytest.raises(ValueError,match="truncated"):
        inspect(file)


def test_cube_and_extra_mip_levels_are_not_certified(tmp_path):
    file=tmp_path/"cube.dds";Image.new("RGBA",(4,4)).save(file,format="DDS")
    original=file.read_bytes();data=bytearray(original);struct.pack_into("<I",data,112,0x200);file.write_bytes(data)
    with pytest.raises(NotImplementedError,match="Cube"):
        inspect(file)
    data=bytearray(original);struct.pack_into("<I",data,28,6);file.write_bytes(data)
    with pytest.raises(ValueError,match="mip-chain"):
        inspect(file)


def test_block_rounding_and_residency_scope_are_explicit(tmp_path):
    rows=mip_costs(7,3,3,"D3DFMT_DXT1")
    assert [r["storage_bytes"] for r in rows]==[16,8,8]
    file=tmp_path/"texture.dds";Image.new("RGBA",(2,2)).save(file,format="DDS")
    value=inspect(file)
    assert not value["full_chain"] and "driver copies" in value["memory_scope"]


def package_fixture(tmp_path):
    root=tmp_path/"package";root.mkdir();(root/"assets").mkdir()
    (root/"car.ydr.xml").write_bytes((Path(__file__).parent/"fixtures/animation_skin.ydr.xml").read_bytes())
    (root/"vehicles.meta").write_text('<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>car</modelName><txdName>paint</txdName></Item></InitDatas></CVehicleModelInfo__InitDataList>')
    (root/"paint.ytd.xml").write_text('<TextureDictionary><Item><Name>fixture_diffuse</Name><FileName>diffuse.dds</FileName><Width value="1"/><Height value="1"/><MipLevels value="1"/><Format>D3DFMT_A8B8G8R8</Format></Item></TextureDictionary>')
    Image.new("RGBA",(1,1),(20,30,40,255)).save(root/"assets/diffuse.dds",format="DDS")
    # Use the writer's actual channel-mask spelling for the declared format.
    from allin1_sdk.texture_workspace import inspect_dds
    xml=root/"paint.ytd.xml"
    xml.write_text(xml.read_text().replace("D3DFMT_A8B8G8R8",inspect_dds(root/"assets/diffuse.dds").format))
    return root


def textures(report):
    return next(c for c in report["checks"] if c["category"]=="textures")


def test_explicit_dictionary_binding_resolves_verified_payload_and_cost(tmp_path):
    root=package_fixture(tmp_path)
    report=package_validation.inspect(str(root))
    assert textures(report)["status"]=="pass"
    assert report["texture_storage_bytes"]==4
    assert report["texture_costs"][0]["name"]=="fixture_diffuse"
    assert report["texture_costs"][0]["all_mips_decoded"]
    assert report["texture_resolutions"][0]["dictionary"]=="package:paint.ytd.xml"
    assert report["texture_resolutions"][0]["payload_sha256"]==report["texture_costs"][0]["sha256"]
    assert report["runtime_status"]=="not_tested"


@pytest.mark.parametrize("fault",["missing","truncated","declaration","traversal"])
def test_broken_payloads_never_become_resolved_passes(tmp_path,fault):
    root=package_fixture(tmp_path);file=root/"assets/diffuse.dds";xml=root/"paint.ytd.xml"
    if fault=="missing": file.unlink()
    elif fault=="truncated": file.write_bytes(file.read_bytes()[:128])
    elif fault=="declaration": xml.write_text(xml.read_text().replace('Width value="1"','Width value="2"'))
    else: xml.write_text(xml.read_text().replace("diffuse.dds","../../outside.dds"))
    report=package_validation.inspect(str(root))
    assert textures(report)["status"]=="fail"
    assert report["texture_storage_bytes"]==0
    assert "excluded" in report["texture_memory_scope"]


def test_no_declaration_is_not_replaced_with_a_filename_guess(tmp_path):
    root=package_fixture(tmp_path);(root/"vehicles.meta").unlink()
    assert textures(package_validation.inspect(str(root)))["status"]=="not_checked"


def test_duplicate_dictionary_candidates_have_no_inferred_winner(tmp_path):
    root=package_fixture(tmp_path);other=root/"alternate";other.mkdir();(other/"assets").mkdir()
    (other/"paint.ytd.xml").write_bytes((root/"paint.ytd.xml").read_bytes())
    (other/"assets/diffuse.dds").write_bytes((root/"assets/diffuse.dds").read_bytes())
    report=package_validation.inspect(str(root))
    assert any(f["code"]=="dictionary_context_unresolved" for f in textures(report)["findings"])
    assert report["texture_resolutions"]==[]


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST")!="1",reason="Requires the built native helper")
@pytest.mark.parametrize("edition",["Legacy","Enhanced"])
def test_generated_native_dictionary_resolves_through_actual_decoder(tmp_path,edition):
    from allin1_sdk.paths import project_root
    from allin1_sdk.processes import run_hidden
    root=package_fixture(tmp_path)
    xml=root/"paint.ytd.xml";output=root/"paint.ytd"
    result=run_hidden([str(project_root()/"tools/RpfPatcher/RpfPatcher.exe"),"asset-from-xml",str(xml),str(output),str(root/"assets"),"legacy" if edition=="Legacy" else "gen9"],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr or result.stdout
    xml.unlink() # Test-owned interchange input; keep only the built dictionary in the package.
    original=output.read_bytes()
    report=package_validation.inspect(str(root),edition=edition)
    assert textures(report)["status"]=="pass",textures(report)
    assert report["texture_storage_bytes"]==4
    assert report["texture_resolutions"][0]["dictionary"]=="package:paint.ytd"
    assert output.read_bytes()==original

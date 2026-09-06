from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

from lxml import etree
import pytest

from allin1_sdk import material_roles, optimization_package as optimization, package_validation
from allin1_sdk.addon_sdk import joaat
from test_artifact_identity import build
from test_optimization_package import request
from test_fragment_validation import fragment


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    monkeypatch.setattr(optimization.artifact_identity,'current',build)


def change_slot(source,slot,*,mixed=False):
    path=Path(source)/'car.ydr.xml';tree=etree.parse(str(path))
    parameter=tree.find('ShaderGroup/Shaders/Item/Parameters/Item')
    if mixed:
        extra=deepcopy(parameter);extra.set('name',slot);parameter.getparent().append(extra)
    else: parameter.set('name',slot)
    path.write_bytes(etree.tostring(tree))


@pytest.mark.parametrize('slot',['BumpSampler','SpecSampler','TintPaletteSampler',f'hash_{joaat("BumpSampler"):08X}'])
@pytest.mark.parametrize('mixed',[False,True])
def test_known_non_color_roles_block_before_encoder_even_if_declared_color(tmp_path,monkeypatch,slot,mixed):
    payload=request(tmp_path);change_slot(payload['source'],slot,mixed=mixed)
    catalogue=optimization.inspect({**payload,'settings':{'textures':[]}})
    usage=catalogue['choices'][0]['material_usage']
    assert usage['color_conversion_blocked']
    assert len(usage['usages'])==(2 if mixed else 1)
    def forbidden(*args,**kwargs): raise AssertionError('Encoder must not be reached')
    monkeypatch.setattr(optimization.optimization_texture,'candidate',forbidden)
    original=(Path(payload['source'])/'assets/diffuse.dds').read_bytes()
    with pytest.raises(ValueError,match='blocked by material usage'):
        optimization.inspect(payload)
    assert (Path(payload['source'])/'assets/diffuse.dds').read_bytes()==original


def test_color_hints_remain_declarations_and_are_recorded_in_receipt(tmp_path):
    payload=request(tmp_path)
    value=optimization.inspect(payload)
    usage=value['changes'][0]['material_usage']
    assert usage['roles']==['color_hint'] and not usage['color_conversion_blocked']
    assert usage['usages'][0]['slot']=='DiffuseSampler'
    export={**payload,'action':'export','destination':str(tmp_path/'output'),'expected_state_sha256':value['state_sha256']}
    optimization.apply(export)
    receipt=json.loads((tmp_path/'output/optimization.json').read_bytes())
    assert receipt['changes'][0]['material_usage']==usage
    assert receipt['before_report']['texture_resolutions'][0]['usages'][0]['role']=='color_hint'


def test_explicit_comparison_consumer_can_block_package_texture(tmp_path):
    payload=request(tmp_path);context=tmp_path/'context';context.mkdir()
    shutil.copyfile(Path(payload['source'])/'car.ydr.xml',context/'car.ydr.xml')
    change_slot(context,'BumpSampler')
    (context/'vehicles.meta').write_text('<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>car</modelName><txdName>paint</txdName></Item></InitDatas></CVehicleModelInfo__InitDataList>')
    report=package_validation.inspect(payload['source'],comparison=context)
    usage=material_roles.profile(report,'paint.ytd.xml','fixture_diffuse')
    assert usage['roles']==['color_hint','normal']
    assert any(row['sampler'].startswith('comparison:') for row in usage['usages'])
    with pytest.raises(ValueError,match='blocked by material usage'):
        optimization.inspect({**payload,'comparison':str(context)})


def test_fragment_child_shader_usage_cannot_hide_behind_primary_color_shader(tmp_path):
    payload=request(tmp_path);source=Path(payload['source'])
    root=fragment();child=root.find('Physics/LOD1/Children/Item/Drawable')
    group=deepcopy(root.find('Drawable/ShaderGroup'))
    group.find('Shaders/Item/Parameters/Item').set('name','BumpSampler');child.append(group)
    (source/'car.ydr.xml').unlink();(source/'car.yft.xml').write_bytes(etree.tostring(root))
    catalogue=optimization.inspect({**payload,'settings':{'textures':[]}})
    usage=catalogue['choices'][0]['material_usage']
    assert usage['color_conversion_blocked'] and 'normal' in usage['roles']
    assert any('/Physics/LOD1/Children/0/Drawable/' in row['sampler'] for row in usage['usages'])
    with pytest.raises(ValueError,match='blocked by material usage'): optimization.inspect(payload)


def test_unknown_slot_does_not_become_verified_color(tmp_path):
    payload=request(tmp_path);change_slot(payload['source'],'CustomShaderSlot')
    value=optimization.inspect(payload)
    assert value['changes'][0]['material_usage']['roles']==['unknown']
    assert 'not proof' in value['changes'][0]['material_usage']['scope']


def test_unresolved_dictionary_context_cannot_authorize_color_encoding(tmp_path,monkeypatch):
    payload=request(tmp_path);(Path(payload['source'])/'vehicles.meta').unlink()
    def forbidden(*args,**kwargs): raise AssertionError('Encoder must not be reached')
    monkeypatch.setattr(optimization.optimization_texture,'candidate',forbidden)
    with pytest.raises(ValueError,match='Resolve texture dictionary context'):
        optimization.inspect(payload)


@pytest.mark.skipif(os.environ.get('ALLIN1_NATIVE_RPF_TEST')!='1',reason='explicit native RPF gate')
@pytest.mark.parametrize('edition',['Legacy','Enhanced'])
def test_real_native_packed_normal_map_remains_blocked(tmp_path,monkeypatch,edition):
    import test_package_intake as fixtures
    original_fixture=fixtures.package_fixture
    def normal_fixture(root):
        package=original_fixture(root);change_slot(package,'BumpSampler');return package
    monkeypatch.setattr(fixtures,'package_fixture',normal_fixture)
    archive,game=fixtures.native_archive(tmp_path,edition,size=16)
    original=archive.read_bytes()
    payload={'source':str(archive),'edition':edition,'gta_path':str(game),'settings':{'textures':[]}}
    catalogue=optimization.inspect(payload)
    choice=catalogue['choices'][0]
    assert choice['material_usage']['color_conversion_blocked']
    assert 'normal' in choice['material_usage']['roles']
    payload['settings']['textures']=[{'dictionary':choice['dictionary'],'texture':choice['texture'],'format':'DXT5','mips':5,'role':'color'}]
    with pytest.raises(ValueError,match='blocked by material usage'): optimization.inspect(payload)
    assert archive.read_bytes()==original

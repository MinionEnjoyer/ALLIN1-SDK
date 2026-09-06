import hashlib
import os
from pathlib import Path

import pytest

from allin1_sdk import package_metadata, package_validation, metadata_validation
from allin1_sdk.addon_sdk import joaat
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
from test_native_relationships import YTYP, YMAP
from test_ped_ymt_inspector import _variation_xml


def test_native_decode_provenance_and_metadata_collisions(tmp_path, monkeypatch):
    root=tmp_path/'package';root.mkdir()
    for name in ('first.ytyp','second.ytyp'): (root/name).write_bytes(b'native')
    def decode(self,name,data,destination,**kwargs):
        edit=Path(destination)/'edit';edit.mkdir(parents=True)
        (edit/(name+'.xml')).write_bytes(YTYP)
    monkeypatch.setattr(NativeAssetInspector,'export_workspace_bytes',decode)
    report=package_validation.inspect(root,edition='Enhanced')
    assert report['definition_count']==2
    assert any(f['code']=='duplicate_definition' for c in report['checks'] for f in c['findings'])
    assert len(report['metadata_evidence'])==2
    for row in report['metadata_evidence']:
        assert row['native'] and row['status']=='xml_available' and row['definition_schema_supported']
        assert row['source_sha256']==hashlib.sha256(b'native').hexdigest()
        assert row['xml_sha256']==hashlib.sha256(YTYP).hexdigest()


@pytest.mark.parametrize('fault',['missing_edition','decode_error','xml_limit','native_limit','count_limit'])
def test_unavailable_native_metadata_never_counts_as_validated(tmp_path,monkeypatch,fault):
    root=tmp_path/'package';root.mkdir();(root/'peds.ymt').write_bytes(b'native')
    calls=[]
    def decode(self,name,data,destination,**kwargs):
        calls.append(name)
        if fault=='decode_error': raise ValueError('unsupported metadata')
        edit=Path(destination)/'edit';edit.mkdir(parents=True);(edit/(name+'.xml')).write_bytes(b'<unknown/>')
    monkeypatch.setattr(NativeAssetInspector,'export_workspace_bytes',decode)
    if fault=='xml_limit': monkeypatch.setattr(package_metadata,'MAX_XML_BYTES',3)
    if fault=='native_limit': monkeypatch.setattr(package_metadata,'MAX_NATIVE_BYTES',3)
    if fault=='count_limit': monkeypatch.setattr(package_metadata,'MAX_NATIVE_FILES',0)
    report=package_validation.inspect(root,edition=None if fault=='missing_edition' else 'Legacy')
    row=report['metadata_evidence'][0]
    assert row['status']=='not_checked' and row['xml_sha256'] is None
    codes={f['code'] for c in report['checks'] for f in c['findings']}
    assert {'native_metadata_unparsed','native_texture_relationships_unparsed'}<=codes
    if fault in ('missing_edition','native_limit','count_limit'): assert not calls


def test_decoded_xml_is_not_mistaken_for_known_schema(tmp_path):
    (tmp_path/'unknown.ymt.xml').write_text('<CPedVariationInfo/>')
    report=package_validation.inspect(tmp_path)
    row=report['metadata_evidence'][0]
    assert row['status']=='xml_available' and not row['definition_schema_supported']
    assert row['definition_count']==0
    assert report['files'][0]['coverage']=='metadata_schema_unmapped'


def test_comparison_native_definitions_are_explicit_context_and_originals_stay_unchanged(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'fixture.ytyp.xml').write_bytes(YTYP)
    comparison=tmp_path/'comparison';comparison.mkdir();(comparison/'fixture.ytyp').write_bytes(b'comparison')
    def decode(self,name,data,destination,**kwargs):
        assert data==b'comparison'
        edit=Path(destination)/'edit';edit.mkdir(parents=True);(edit/(name+'.xml')).write_bytes(YTYP)
    monkeypatch.setattr(NativeAssetInspector,'export_workspace_bytes',decode)
    report=package_validation.inspect(source,comparison=comparison,edition='Legacy')
    assert report['comparison_definition_count']==1
    assert any(f['code']=='external_override_candidate' for c in report['checks'] for f in c['findings'])
    assert report['metadata_evidence'][1]['source']=='comparison:fixture.ytyp'
    assert (comparison/'fixture.ytyp').read_bytes()==b'comparison'


def test_malformed_xml_and_source_drift_are_not_decoded_success(tmp_path,monkeypatch):
    source=tmp_path/'bad.ytyp.xml';source.write_bytes(b'<CMapTypes>')
    report=package_validation.inspect(tmp_path)
    assert report['metadata_evidence'][0]['status']=='invalid_xml_or_definitions'
    assert report['files'][0]['coverage']=='invalid_xml_or_definitions'
    source.unlink();native=tmp_path/'changing.ytyp';native.write_bytes(b'first')
    def decode(self,name,data,destination,**kwargs):
        native.write_bytes(b'changed')
        edit=Path(destination)/'edit';edit.mkdir(parents=True);(edit/(name+'.xml')).write_bytes(YTYP)
    monkeypatch.setattr(NativeAssetInspector,'export_workspace_bytes',decode)
    with pytest.raises(ValueError,match='changed during validation'):
        package_validation.inspect(tmp_path,edition='Legacy')


def test_map_hash_literals_are_not_rehashed_or_misclassified_as_colliding_names():
    known=metadata_validation.definitions(YTYP,'known')[0]
    raw=YTYP.replace(b'<name>chair</name>',f'<name>hash_{joaat("chair"):08X}</name>'.encode())
    unknown=metadata_validation.definitions(raw,'unknown')[0]
    assert known[0]['key']==unknown[0]['key']
    assert metadata_validation.collisions(known,unknown)[0]['code']=='external_override_candidate'
    assert metadata_validation.collisions(known+unknown)[0]['code']=='duplicate_definition'
    assert metadata_validation.definitions(YMAP,'map')[0][0]['namespace']=='map'


@pytest.mark.skipif(os.environ.get('ALLIN1_NATIVE_RPF_TEST')!='1',reason='explicit native converter gate')
@pytest.mark.parametrize('edition',['Legacy','Enhanced'])
@pytest.mark.parametrize('suffix,xml',[('.ytyp',YTYP),('.ymap',YMAP),('.ymt',_variation_xml())],ids=['archetypes','map','ped_variation'])
def test_actual_native_metadata_decode_in_unified_report(tmp_path,edition,suffix,xml):
    root=tmp_path/'package';root.mkdir()
    source=tmp_path/('fixture'+suffix+'.xml');source.write_bytes(xml)
    target=root/('fixture'+suffix)
    # The test owns a new META document, not an ambiguous existing YMT. The
    # helper's .ymap writer branch selects generic RSC/META; XML selects the
    # CPedVariationInfo root. Production YMT roundtrip still requires an original.
    writer_output=tmp_path/'meta-container.ymap' if suffix=='.ymt' else target
    helper=project_root()/'tools/RpfPatcher/RpfPatcher.exe'
    result=run_hidden([str(helper),'asset-from-xml',str(source),str(writer_output),str(tmp_path),'legacy' if edition=='Legacy' else 'gen9'],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr or result.stdout
    if writer_output!=target: writer_output.rename(target)
    original=target.read_bytes()
    report=package_validation.inspect(root,edition=edition)
    row=report['metadata_evidence'][0]
    assert row['status']=='xml_available',row
    assert row['source_sha256']==hashlib.sha256(original).hexdigest()
    assert len(row['xml_sha256'])==64 and row['xml_bytes']>0
    assert row['definition_schema_supported']==(suffix!='.ymt')
    assert report['definition_count']==(0 if suffix=='.ymt' else 1)
    assert target.read_bytes()==original

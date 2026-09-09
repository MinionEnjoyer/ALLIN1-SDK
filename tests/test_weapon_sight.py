import hashlib
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk import weapon_desktop, weapon_sight
from allin1_sdk.cli import main
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from test_weapon_authoring_core import _source
from test_weapon_desktop import tree_hashes

FIXTURE = Path(__file__).parent / "fixtures/animation_skin.ydr.xml"


def setup(tmp_path, monkeypatch):
    source = _source(tmp_path)
    (source / "stream").mkdir(exist_ok=True)
    (source / "stream/w_pi_author.ydr").write_bytes(b"test-native-model")
    (source / "stream/test.ycd").write_bytes(b"test-native-animation")
    calls = []
    def decode(self, name, data, **kwargs):
        calls.append((name, data, kwargs))
        return FIXTURE.read_bytes()
    monkeypatch.setattr(weapon_sight.NativeAssetInspector, "decode_xml_bytes", decode)
    return source, calls, dict(source=str(source), weapon="WEAPON_AUTHOR", expected_revision=None,
                              edition="enhanced", entry="stream/w_pi_author.ydr", sight_action="model")


def test_native_body_packet_and_sources_unchanged(tmp_path, monkeypatch):
    source, calls, request = setup(tmp_path, monkeypatch)
    before = tree_hashes(source)
    result = weapon_desktop.inspect(request)
    assert result["kind"] == "weapon_sight_model"
    assert result["native_sha256"] == hashlib.sha256(b"test-native-model").hexdigest()
    assert result["packet"]["vertex_count"] == 3
    assert result["packet"]["lod"] == "High"
    assert result["read_only"] and not result["game_write_performed"]
    assert len(calls) == 1 and tree_hashes(source) == before
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded(result) == result


@pytest.mark.parametrize("changes", [
    {"entry": "../outside.ydr"}, {"entry": "stream/w_pi_author_clip.ydr"},
    {"weapon": "WEAPON_MISSING"}, {"edition": "auto"}, {"lod": "All"},
    {"expected_revision": 2}, {"sight_action": "install"}, {"calibration_action": "list"},
])
def test_rejects_ambiguous_unrelated_and_stale_requests(tmp_path, monkeypatch, changes):
    source, calls, request = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        weapon_desktop.inspect({**request, **changes})
    assert not calls


def test_rechecks_bytes_after_decoder(tmp_path, monkeypatch):
    source, calls, request = setup(tmp_path, monkeypatch)
    def mutate(*args, **kwargs):
        (source / request["entry"]).write_bytes(b"changed")
        return FIXTURE.read_bytes()
    monkeypatch.setattr(weapon_sight.NativeAssetInspector, "decode_xml_bytes", mutate)
    with pytest.raises(ValueError, match="changed"):
        weapon_sight.inspect(request)


def test_workspace_scope_and_exact_revision(tmp_path, monkeypatch):
    source, calls, request = setup(tmp_path, monkeypatch)
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    before = tree_hashes(workspace.root)
    request.pop("source")
    result = weapon_sight.inspect({**request, "workspace": str(workspace.root), "expected_revision": 0})
    assert result["source"] == str(workspace.source)
    assert tree_hashes(workspace.root) == before


def test_animation_is_explicit_not_selected_by_weapon_name(tmp_path, monkeypatch):
    source, calls, request = setup(tmp_path, monkeypatch)
    sampled = []
    monkeypatch.setattr(weapon_sight.animation_samples, "analyze", lambda xml, selection, **kw: sampled.append((selection, kw)) or {"tracks": []})
    result = weapon_sight.inspect({**request, "sight_action": "animation", "entry": "stream/test.ycd", "selection": "clip:12345678"})
    assert result["kind"] == "weapon_sight_animation" and sampled == [("clip:12345678", {"inventory_only": False})]
    weapon_sight.inspect({**request, "sight_action": "animation", "entry": "stream/test.ycd"})
    assert sampled[-1] == (None, {"inventory_only": True})
    assert weapon_desktop.inspect({"source": str(source)})["animation_assets"] == ["stream/test.ycd"]


def test_cli_rejects_nonobject_and_is_agent_read_only():
    result = CliRunner().invoke(main, ["inspect-weapon-sights", "--payload", "[]"])
    assert result.exit_code != 0 and "object" in result.output
    from allin1_sdk.agent_api import READ_ONLY_COMMANDS
    assert "inspect-weapon-sights" in READ_ONLY_COMMANDS


def component_setup(tmp_path, monkeypatch):
    source, calls, request = setup(tmp_path, monkeypatch)
    def decode(self, name, data, **kwargs):
        calls.append(name)
        return FIXTURE.read_bytes().replace(b'<Name>tip</Name>', b'<Name>WAPClip</Name>')
    monkeypatch.setattr(weapon_sight.NativeAssetInspector, 'decode_xml_bytes', decode)
    return source, calls, {**request, 'component': 'COMPONENT_AUTHOR_CLIP', 'component_entry': 'stream/w_pi_author_clip.ydr'}


def test_exact_component_and_broker_preserve_full_assembly(tmp_path, monkeypatch):
    source, calls, request = component_setup(tmp_path, monkeypatch)
    before = tree_hashes(source)
    result = weapon_sight.inspect(request)
    child = result['attachment']
    assert child['parent_index'] == child['child_index'] == 1
    assert child['parent_bone'] == child['child_bone'] == 'WAPClip'
    assert child['component_type'] == 'CWeaponComponentClipInfo'
    assert child['packet']['vertex_count'] == 3
    assert child['native_sha256'] == hashlib.sha256((source / request['component_entry']).read_bytes()).hexdigest()
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded({'result': result})['result'] == result
    assert tree_hashes(source) == before


@pytest.mark.parametrize('changes', [
    {'component': 'COMPONENT_UNKNOWN'}, {'component_entry': 'stream/w_pi_author.ydr'},
    {'component_entry': '../outside.ydr'}, {'component_entry': []}, {'component': None},
    {'component': 'COMPONENT_AUTHOR_SCOPE'},
    {'sight_action': 'animation', 'entry': 'stream/test.ycd'},
])
def test_invalid_component_rejected_before_decode(tmp_path, monkeypatch, changes):
    source, calls, request = component_setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        weapon_sight.inspect({**request, **changes})
    assert not calls


def test_declared_missing_bone_is_not_guessed(tmp_path, monkeypatch):
    source, calls, request = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='attachment bones'):
        weapon_sight.inspect({**request, 'component':'COMPONENT_AUTHOR_CLIP', 'component_entry':'stream/w_pi_author_clip.ydr'})


@pytest.mark.parametrize('target', ['child', 'metadata'])
def test_attachment_bytes_and_metadata_rechecked(tmp_path, monkeypatch, target):
    source, calls, request = component_setup(tmp_path, monkeypatch)
    def decode(self, name, data, **kwargs):
        if name.endswith('_clip.ydr'):
            if target == 'child':
                (source / request['component_entry']).write_bytes(b'changed')
            else:
                path = source / 'weaponcomponents.meta'
                path.write_text(path.read_text().replace('<AttachBone>WAPClip</AttachBone>', '<AttachBone>OTHER</AttachBone>'))
        return FIXTURE.read_bytes().replace(b'<Name>tip</Name>', b'<Name>WAPClip</Name>')
    monkeypatch.setattr(weapon_sight.NativeAssetInspector, 'decode_xml_bytes', decode)
    with pytest.raises(ValueError, match='Component source changed'):
        weapon_sight.inspect(request)


def test_sight_profile_does_not_raise_default_playback_limits(monkeypatch):
    from allin1_sdk import animation_model
    monkeypatch.setattr(animation_model, 'MAX_VERTICES', 2)
    assert animation_model.analyze(FIXTURE.read_bytes())['view_unavailable']
    assert animation_model.analyze(FIXTURE.read_bytes(), sight=True)['vertex_count'] == 3
    monkeypatch.setattr(animation_model, 'SIGHT_MAX_VERTICES', 2)
    limited = animation_model.analyze(FIXTURE.read_bytes(), sight=True)
    assert limited['view_unavailable'] and not limited['meshes']
    with pytest.raises(ValueError, match='profile'):
        animation_model.analyze(FIXTURE.read_bytes(), sight='unlimited')


def test_agent_summary_is_small_and_not_a_fake_render_packet(tmp_path, monkeypatch):
    import json
    source, calls, request = component_setup(tmp_path, monkeypatch)
    result = weapon_sight.inspect({**request, 'summary_only': True})
    assert result['summary_only']
    assert len(json.dumps(result)) < 20000
    for packet in (result['packet'], result['attachment']['packet']):
        assert packet['vertex_count'] == 3 and packet['bone_count'] == 2 and packet['mesh_count'] == 1
        assert not packet['meshes'] and not packet['bones'] and 'Summary only' in packet['view_unavailable']
        assert packet['mesh_labels'] == ['Mesh 1: ped.sps']
    from allin1_sdk.agent_api import execute_request
    response = execute_request({'id': 'sight', 'action': 'execute', 'command': 'inspect-weapon-sights',
                                'args': ['--payload', json.dumps({**request, 'summary_only': True})]})
    assert response['ok'] and response['risk'] == 'read_only'
    assert response['result']['data_available'] and not response['result']['output_truncated']
    assert response['result']['data'] == result
    calls.clear()
    with pytest.raises(ValueError, match='boolean'):
        weapon_sight.inspect({**request, 'summary_only': 'yes'})
    assert not calls

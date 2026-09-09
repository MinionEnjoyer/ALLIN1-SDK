import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from allin1_sdk import animation_samples as samples, workspace_desktop
from allin1_sdk.processes import run_hidden

FIXTURE = Path(__file__).parent / "fixtures/native_animation.ycd.xml"
NATIVE = pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Native helper opt-in")


@NATIVE
def test_explicit_inventory_never_auto_samples_and_keeps_bad_clips_visible():
    from copy import deepcopy
    from lxml import etree
    inventory = samples.analyze(FIXTURE.read_bytes(), inventory_only=True)
    assert inventory['selected'] is None and not inventory['sampled']
    assert inventory['times'] == inventory['tracks'] == []
    root = etree.fromstring(FIXTURE.read_bytes())
    bad = deepcopy(root.find('Animations/Item'))
    bad.find('Hash').text = 'hash_55555555'
    # A legal XML encoding with an unsupported five-component channel.
    channels = bad.find('Sequences/Item/SequenceData/Item/Channels')
    for _ in range(2):
        channels.append(deepcopy(channels[-1]))
    root.find('Animations').append(bad)
    clip = deepcopy(root.find('Clips/Item'))
    clip.find('Hash').text = 'hash_00000001'
    clip.find('AnimationHash').text = 'hash_55555555'
    clip.find('Name').text = 'bad_clip'
    root.find('Clips').append(clip)
    xml = etree.tostring(root)
    inventory = samples.analyze(xml, inventory_only=True)
    choices = {row['key']: row for row in inventory['choices']}
    assert 'omitted' in choices['clip:00000001']['error']
    assert choices['clip:11111111']['error'] is None
    with pytest.raises(ValueError, match='omitted'):
        samples.analyze(xml, 'clip:00000001')
    valid = samples.analyze(xml, 'clip:11111111')
    assert valid['tracks'] and valid['selected'] == 'clip:11111111'


@pytest.mark.parametrize('mode,selection', [('yes', None), (True, 'clip:11111111')])
def test_invalid_inventory_requests_fail_before_native_decode(mode, selection):
    with pytest.raises(ValueError, match='Inventory'):
        samples.analyze(FIXTURE.read_bytes(), selection, inventory_only=mode)


@NATIVE
def test_fixture_rate_endpoints_and_transport_contract():
    packet = samples.analyze(FIXTURE.read_bytes())
    assert packet["selected"] == "clip:11111111" and packet["duration"] == .5
    assert len(packet["times"]) == 240
    assert packet["tracks"][0]["values"][0] == .5 and packet["tracks"][0]["values"][-4] == 1.5
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded({"session": {"animation": packet}})["session"]["animation"] == packet
    assert json.loads((Path(__file__).resolve().parents[1] / "desktop/src/nativeAnimationFixture.json").read_text()) == packet


@NATIVE
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_actual_ycd_decode_workspace_selection_and_rebuild(tmp_path, edition):
    from allin1_sdk.native_assets import NativeAssetInspector
    inspector = NativeAssetInspector(Path(__file__).resolve().parents[1])
    source = tmp_path / "fixture.ycd"
    generated = run_hidden([str(inspector.patcher), "asset-from-xml", str(FIXTURE), str(source), str(tmp_path), "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=60)
    assert generated.returncode == 0, generated.stderr
    context = {"module": "native", "source": str(source), "edition": edition}
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    source_session = workspace_desktop.inspect(context)
    assert source_session["animation"]["tracks"][0]["values"][-4] == 1.5
    bound = workspace_desktop.inspect({**context, "document": {"model_xml": str(FIXTURE.with_name("animation_skin.ydr.xml"))}})
    assert bound["animation_model"]["bones"][1]["tag"] == 42
    assert bound["animation"] == source_session["animation"]
    shared = workspace_desktop.inspect({**context, "document": {"model_xml": str(FIXTURE.with_name("animation_skin.ydr.xml")),
        "skeleton_xml": str(FIXTURE.with_name("animation_skin.ydr.xml")), "skeleton_drawable": "0"}})
    assert shared["animation_model"]["skeleton_binding"]["selected"] == "0"
    assert shared["animation_model"]["bones"] == bound["animation_model"]["bones"]
    invalid_model = workspace_desktop.inspect({**context, "document": {"model_xml": str(tmp_path / "missing.xml")}})
    assert invalid_model["animation"] == source_session["animation"]
    assert any("model binding unavailable" in warning for warning in invalid_model["warnings"])
    def perform(context, action, **fields):
        request = {**context, "action": action, "expected_state_sha256": workspace_desktop.inspect(context)["state_sha256"], **fields}
        review = workspace_desktop.review(request)
        return workspace_desktop.apply({**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    exported = perform(context, "export", destination=str(tmp_path / "workspace"))
    assert exported["session"]["animation"] == source_session["animation"]
    editable = {"module": "native", "workspace": str(tmp_path / "workspace")}
    raw = workspace_desktop.inspect({**editable, "document": {"animation": "animation:22222222"}})
    assert raw["animation"]["duration"] == 2 and raw["animation"]["tracks"][0]["values"][-4] == 2
    xml_path = Path(raw["xml_path"])
    edited = xml_path.read_text().replace('<Rate value="2"', '<Rate value="1"')
    saved = perform(editable, "save_xml", document={"language": "xml", "chunks": [edited]})
    assert saved["session"]["animation"]["duration"] == 1
    perform(editable, "build", destination=str(tmp_path / "rebuilt.ycd"))
    rebuilt = workspace_desktop.inspect({**context, "source": str(tmp_path / "rebuilt.ycd")})
    assert rebuilt["animation"] == saved["session"]["animation"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


@NATIVE
@pytest.mark.parametrize("old,new", [('value="3"', 'value="0"'), ('0 1 2</Values>', '0 1</Values>'), ('Track value="1"', 'Track value="256"'), ('Type value="RawFloat"', 'Type value="Unknown"'), ('Duration value="2"', 'Duration value="NaN"')])
def test_malformed_animation_fails_instead_of_silent_wrapping(old, new):
    with pytest.raises(ValueError, match="Animation decoder failed"):
        samples.analyze(FIXTURE.read_text().replace(old, new).encode())


@NATIVE
def test_cached_quaternion_xml_matches_normalized_identity():
    xml = FIXTURE.read_text().replace('<Item><Type value="StaticQuaternion"/><Value x="0" y="0" z="0" w="1"/></Item>',
        '<Item><Type value="StaticFloat"/><Value value="0"/></Item>'*3 + '<Item><Type value="CachedQuaternion1"/><QuatIndex value="3"/></Item>')
    packet = samples.analyze(xml.encode())
    assert packet["tracks"][1]["values"] == [0, 0, 0, 1]*240


@NATIVE
def test_full_quaternion_with_trailing_type2_cache_keeps_stored_sign():
    original = '<Item><Type value="StaticQuaternion"/><Value x="0" y="0" z="0" w="1"/></Item>'
    full = ''.join(f'<Item><Type value="StaticFloat"/><Value value="{v}"/></Item>' for v in (.6, 0, 0, -.8))
    cache = '<Item><Type value="CachedQuaternion2"/><QuatIndex value="0"/></Item>'
    packet = samples.analyze(FIXTURE.read_text().replace(original, full + cache).encode())
    assert packet['tracks'][1]['values'] == pytest.approx([.6, 0, 0, -.8]*240)
    # A fifth real component or different cache kind is still unsupported.
    for invalid in (full + '<Item><Type value="StaticFloat"/><Value value="1"/></Item>',
                    full + cache.replace('CachedQuaternion2', 'CachedQuaternion1')):
        with pytest.raises(ValueError, match='omitted'):
            samples.analyze(FIXTURE.read_text().replace(original, invalid).encode())


@pytest.mark.parametrize("selection", ["../escape", "animation:not-a-hash", 1, [], "clip:22222222 --x"])
def test_selection_is_an_exact_key_not_a_path_or_argument(selection):
    with pytest.raises(ValueError, match="exact"):
        samples.analyze(b"<ClipDictionary/>", selection)


def test_missing_helper_and_failed_or_timed_out_decoder(tmp_path, monkeypatch):
    monkeypatch.setattr(samples, "project_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="current RpfPatcher"):
        samples.analyze(b"<ClipDictionary/>")
    helper = tmp_path / "tools/RpfPatcher/RpfPatcher.exe"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"fixture")
    monkeypatch.setattr(samples, "run_hidden", lambda *a, **kw: SimpleNamespace(returncode=5, stdout="", stderr="bad XML"))
    with pytest.raises(ValueError, match="bad XML"):
        samples.analyze(b"<ClipDictionary/>")
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("decoder", 30)
    monkeypatch.setattr(samples, "run_hidden", timeout)
    with pytest.raises(ValueError, match="unavailable"):
        samples.analyze(b"<ClipDictionary/>")


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("tracks", [None]), ("times", [0, -1]), ("duration", float("nan")), ("choices", [{}])])
def test_invalid_packets_are_rejected(field, value):
    packet = {"schema_version": 1, "read_only": True, "scope": "fixture", "selected": None, "choices": [], "tracks": [], "times": [], "duration": 0}
    packet[field] = value
    with pytest.raises(ValueError):
        samples.validate(packet)

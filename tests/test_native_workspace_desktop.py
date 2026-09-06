import hashlib
import json
import io
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk import native_workspace_desktop as native
from allin1_sdk import workspace_desktop as desktop


def workspace(root, suffix=".ymt"):
    (root / "original").mkdir(parents=True)
    (root / "edit/assets").mkdir(parents=True)
    name = "asset" + suffix
    original = b"original-resource"
    (root / "original" / name).write_bytes(original)
    (root / "edit" / (name + ".xml")).write_text("<root><value>1</value></root>", encoding="utf-8")
    (root / "native-workspace.json").write_text(json.dumps({"schema_version": 1, "operation": "native_asset_workspace", "edition": "Legacy",
        "source": {"name": name, "suffix": suffix, "snapshot": "original/" + name, "size": len(original), "sha256": hashlib.sha256(original).hexdigest()},
        "xml": {"path": "edit/" + name + ".xml"}, "dependencies": []}), encoding="utf-8")
    return {"module": "native", "workspace": str(root)}


def request(context, action, **fields):
    return {**context, "action": action, "expected_state_sha256": desktop.inspect(context)["state_sha256"], **fields}


def apply(review_request):
    evidence = desktop.review(review_request)
    return desktop.apply({**review_request, "review_sha256": evidence["review_sha256"], "authoring_confirmed": True})


def test_native_xml_save_is_reviewed_preserves_original_and_backup(tmp_path):
    context = workspace(tmp_path / "native")
    before = desktop.inspect(context)
    assert before["xml_editable"] and before["xml_chunks"] == ["<root><value>1</value></root>"]
    change = request(context, "save_xml", document={"language": "xml", "chunks": ["<root><value>2</value></root>"]})
    result = apply(change)
    assert result["module"] == "native" and result["session"]["module"] == "native"
    assert result["session"]["xml_chunks"] == ["<root><value>2</value></root>"]
    assert Path(result["backup"]).read_text() == before["xml_chunks"][0]
    assert (tmp_path / "native/original/asset.ymt").read_bytes() == b"original-resource"
    assert result["game_write_performed"] is False


def test_asset_report_exports_exact_reviewed_identity_without_touching_assets(tmp_path):
    context = workspace(tmp_path / "native", ".ydr")
    xml = tmp_path / "native/edit/asset.ydr.xml"
    xml.write_bytes((Path(__file__).parent / "fixtures/animation_skin.ydr.xml").read_bytes())
    before = desktop.inspect(context)
    assert before["asset_validation"]["static_status"] == "incomplete"
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded(before) == before
    output = tmp_path / "report.json"
    change = request(context, "export_validation", destination=str(output))
    result = apply(change)
    assert json.loads(output.read_bytes()) == before["asset_validation"]
    assert result["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert xml.read_bytes() == (Path(__file__).parent / "fixtures/animation_skin.ydr.xml").read_bytes()
    assert result["game_write_performed"] is False
    with pytest.raises(ValueError):
        desktop.review(change)


def test_asset_report_export_rejects_a_changed_workspace(tmp_path):
    context = workspace(tmp_path / "native", ".ydr")
    xml = tmp_path / "native/edit/asset.ydr.xml"
    xml.write_bytes((Path(__file__).parent / "fixtures/animation_skin.ydr.xml").read_bytes())
    output = tmp_path / "report.json"
    change = request(context, "export_validation", destination=str(output))
    pending = desktop.review(change)
    xml.write_bytes(xml.read_bytes().replace(b"allin1_animation_skin", b"changed"))
    with pytest.raises(ValueError, match="changed"):
        desktop.apply({**change, "review_sha256": pending["review_sha256"], "authoring_confirmed": True})
    assert not output.exists()


def test_report_publication_preserves_a_competing_file(tmp_path, monkeypatch):
    context = workspace(tmp_path / "native", ".ydr")
    xml = tmp_path / "native/edit/asset.ydr.xml"
    xml.write_bytes((Path(__file__).parent / "fixtures/animation_skin.ydr.xml").read_bytes())
    output = tmp_path / "report.json"
    change = request(context, "export_validation", destination=str(output))
    link = native.os.link
    def race(source, target):
        Path(target).write_bytes(b"other writer")
        link(source, target)
    monkeypatch.setattr(native.os, "link", race)
    with pytest.raises(FileExistsError):
        apply(change)
    assert output.read_bytes() == b"other writer"
    assert not list(tmp_path.glob(".allin1-validation-*"))


def test_changed_xml_or_dependency_invalidates_review(tmp_path):
    context = workspace(tmp_path / "native")
    change = request(context, "save_xml", document={"language": "xml", "chunks": ["<root/>"]})
    evidence = desktop.review(change)
    (tmp_path / "native/edit/assets/new.bin").write_bytes(b"new dependency")
    with pytest.raises(ValueError, match="changed"):
        desktop.apply({**change, "review_sha256": evidence["review_sha256"], "authoring_confirmed": True})


@pytest.mark.parametrize("mutation", ["snapshot", "xml_path", "entity"])
def test_unsafe_native_workspace_or_xml_is_rejected(tmp_path, mutation):
    context = workspace(tmp_path / "native")
    if mutation == "snapshot":
        (tmp_path / "native/original/asset.ymt").write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="snapshot"):
            desktop.inspect(context)
    elif mutation == "xml_path":
        target = tmp_path / "native/native-workspace.json"
        manifest = json.loads(target.read_text())
        manifest["xml"]["path"] = "../outside.xml"
        target.write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="paths"):
            desktop.inspect(context)
    else:
        with pytest.raises(ValueError, match="entity|DTDs"):
            desktop.review(request(context, "save_xml", document={"language": "xml", "chunks": ['<!DOCTYPE root SYSTEM "file:///secret"><root/>']}))


def test_dependency_export_is_exact_and_refuses_overwrites(tmp_path):
    context = workspace(tmp_path / "native", ".awc")
    dependency = tmp_path / "native/edit/assets/track.wav"
    dependency.write_bytes(b"fixture-wave")
    session = desktop.inspect(context)
    assert session["dependencies"][0]["kind"] == "audio"
    target = tmp_path / "export.wav"
    result = apply(request(context, "export_dependency", destination=str(target), document={"dependency": "track.wav"}))
    assert target.read_bytes() == dependency.read_bytes()
    assert result["output_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="new destination"):
        desktop.review(request(context, "export_dependency", destination=str(target), document={"dependency": "track.wav"}))
    with pytest.raises(ValueError):
        desktop.review(request(context, "export_dependency", destination=str(tmp_path / "escape.wav"), document={"dependency": "../track.wav"}))


def test_build_calls_retained_converter_and_requires_parse_receipt(tmp_path, monkeypatch):
    from allin1_sdk import artifact_identity
    from test_artifact_identity import build as identity
    monkeypatch.setattr(artifact_identity, "current", identity)
    context = workspace(tmp_path / "native")
    monkeypatch.setattr(native.NativeAssetInspector, "_require_patcher", lambda self: None)
    def build(self, root, target):
        target.write_bytes(b"rebuilt")
        receipt = target.with_name(target.name + ".allin1.json")
        artifact = artifact_identity.manifest(identity(), {"source.ymt": "a"*64},
            {target.name: hashlib.sha256(b"rebuilt").hexdigest()}, edition="Legacy")
        receipt.write_text(json.dumps({"validation": {"reparsed": True, "semantic_xml_match": True}, "artifact": artifact}))
        return target, receipt
    monkeypatch.setattr(native.NativeAssetInspector, "build_workspace", build)
    target = tmp_path / "candidate.ymt"
    result = apply(request(context, "build", destination=str(target)))
    assert result["validation"]["reparsed"] is True
    assert result["session"]["live_game_acceptance"] == "not_tested"
    assert target.read_bytes() == b"rebuilt"
    assert result["provenance"]["build_fingerprint"] == identity()["build_fingerprint"]


def test_build_refuses_game_or_workspace_outputs_and_wrong_extension(tmp_path, monkeypatch):
    context = workspace(tmp_path / "native")
    game = tmp_path / "game"
    game.mkdir()
    context["gta_path"] = str(game)
    monkeypatch.setattr(native.NativeAssetInspector, "_require_patcher", lambda self: None)
    for target in (game / "asset.ymt", tmp_path / "native/candidate.ymt", tmp_path / "candidate.ytd"):
        with pytest.raises(ValueError):
            desktop.review(request(context, "build", destination=str(target)))


def test_large_xml_remains_buildable_but_never_silently_truncated_for_editing(tmp_path):
    context = workspace(tmp_path / "native")
    (tmp_path / "native/edit/asset.ymt.xml").write_text("<root>" + "x" * 70000 + "</root>")
    result = desktop.inspect(context)
    assert not result["xml_editable"] and not result["xml_chunks"]
    assert "externally" in result["warnings"][0]


def test_loose_source_requires_explicit_edition(tmp_path, monkeypatch):
    source = tmp_path / "asset.ymt"
    source.write_bytes(b"native")
    with pytest.raises(ValueError, match="edition"):
        desktop.inspect({"module": "native", "source": str(source)})
    monkeypatch.setattr(native.NativeAssetInspector, "inspect_bytes", lambda *args, **kwargs: SimpleNamespace(metadata={}, warnings=[], structured_text="<root/>", collision_scene=None))
    result = desktop.inspect({"module": "native", "source": str(source), "edition": "Enhanced"})
    assert result["edition"] == "Enhanced" and result["workspace"] is None


def pcm_wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(48000)
        writer.writeframes(b"\x01\x00\x02\x00" * 10)
    return buffer.getvalue()


def test_audio_preview_is_normalized_in_broker_cache_and_channel_exact(tmp_path, monkeypatch):
    context = workspace(tmp_path / "native", ".awc")
    samples = pcm_wav()
    (tmp_path / "native/edit/assets/track.wav").write_bytes(samples)
    monkeypatch.setenv("ALLIN1_PREVIEW_DIR", str(tmp_path / "cache"))
    result = desktop.inspect({**context, "document": {"dependency": "track.wav", "channel": 1}})
    artifact = result["audio"]
    assert Path(artifact["path"]).parent == tmp_path / "cache"
    assert artifact["channels"] == 2 and artifact["selected_channel"] == 1
    with wave.open(artifact["path"], "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.readframes(10) == b"\x02\x00" * 10
    assert (tmp_path / "native/edit/assets/track.wav").read_bytes() == samples


def test_invalid_audio_is_not_published_and_playback_cannot_escape_workspace(tmp_path, monkeypatch):
    context = workspace(tmp_path / "native", ".awc")
    (tmp_path / "native/edit/assets/track.wav").write_bytes(b"not wav")
    monkeypatch.setenv("ALLIN1_PREVIEW_DIR", str(tmp_path / "cache"))
    result = desktop.inspect({**context, "document": {"dependency": "track.wav"}})
    assert not result.get("audio") and any("unavailable" in warning for warning in result["warnings"])
    assert not list((tmp_path / "cache").glob("*.wav"))
    with pytest.raises(ValueError):
        desktop.inspect({**context, "document": {"dependency": "../../private.wav"}})


def test_mixed_preview_cache_remains_bounded(tmp_path, monkeypatch):
    from allin1_sdk import asset_preview
    monkeypatch.setattr(asset_preview, "MAX_CACHE_FILES", 1)
    cache = asset_preview.PreviewArtifactStore(tmp_path / "cache")
    first = cache.write_png(b"image-fixture")
    audio = cache.write_wav(pcm_wav())
    assert not Path(first["path"]).exists() and Path(audio["path"]).is_file()
    with pytest.raises(ValueError, match="channel"):
        cache.write_wav(pcm_wav(), channel=True)

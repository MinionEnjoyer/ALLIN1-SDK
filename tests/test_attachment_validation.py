from pathlib import Path

from lxml import etree
import pytest

from allin1_sdk import attachment_validation as attachment, package_validation

FIXTURE = Path(__file__).parent / "fixtures/animation_skin.ydr.xml"
WEAPON = b'<CWeaponInfoBlob><Infos><Item><Infos><Item type="CWeaponInfo"><Name>WEAPON_TEST</Name><Model>body</Model><AmmoInfo ref="AMMO_TEST"/><AttachPoints><Item><AttachBone>tip</AttachBone><Components><Item><Name>COMPONENT_TEST</Name></Item></Components></Item></AttachPoints></Item></Infos></Item></Infos></CWeaponInfoBlob>'
COMPONENT = b'<CWeaponComponentInfoBlob><Infos><Item><Name>COMPONENT_TEST</Name><Model>clip</Model><AttachBone>tip</AttachBone></Item></Infos></CWeaponComponentInfoBlob>'


def test_actual_metadata_links_are_not_definition_or_ammo_references():
    components, links = attachment.definitions(WEAPON, "weapons.meta")
    assert not components and len(links) == 1
    assert links[0] == dict(weapon="WEAPON_TEST", model="body", bone="tip", component="COMPONENT_TEST", source="weapons.meta")
    components, links = attachment.definitions(COMPONENT, "components.meta")
    assert not links and components[0]["model"] == "clip"
    assert attachment.definitions(b'<Unknown><AttachBone>tip</AttachBone></Unknown>', "other") == ([], [])


def test_parent_composed_frame_and_exact_tag():
    owner = etree.fromstring(FIXTURE.read_bytes())
    root = owner.find("Skeleton/Bones/Item")
    root.find("Translation").set("x", "3")
    frame = attachment.anchor(owner, "TIP")
    assert frame["bone_index"] == 1 and frame["bone_tag"] == 42
    assert [row[3] for row in frame["skeleton_matrix"]] == [3, 0, 1, 1]
    assert [row[3] for row in frame["local_matrix"]] == [0, 0, 1, 1]


def test_anchor_composes_parent_rotation_and_scale_not_just_offsets():
    owner = etree.fromstring(FIXTURE.read_bytes())
    root = owner.find("Skeleton/Bones/Item")
    # A 180-degree X rotation and doubled local Z put the child at Z=-2.
    root.find("Rotation").set("x", "1")
    root.find("Rotation").set("w", "0")
    root.find("Scale").set("z", "2")
    frame = attachment.anchor(owner, "tip")
    assert frame["skeleton_matrix"][2][3] == -2
    assert frame["skeleton_matrix"][1][1] == -1
    assert frame["local_matrix"][2][3] == 1


def test_composed_transform_rejects_numeric_collapse():
    owner = etree.fromstring(FIXTURE.read_bytes())
    for node in owner.findall("Skeleton/Bones/Item"):
        for axis in "xyz": node.find("Scale").set(axis, "0.00001")
    with pytest.raises(ValueError, match="numerically unstable"):
        attachment.anchor(owner, "tip")


@pytest.mark.parametrize("selector,attribute,value", [
    ("Index", "value", "7"), ("ParentIndex", "value", "1"),
    ("Tag", "value", "0"), ("Translation", "x", "nan"),
    ("Scale", "x", "0"), ("Rotation", "w", "0"), ("Rotation", "w", "2"),
])
def test_invalid_transform_or_hierarchy_never_silently_normalizes(selector, attribute, value):
    owner = etree.fromstring(FIXTURE.read_bytes())
    owner.findall("Skeleton/Bones/Item")[1].find(selector).set(attribute, value)
    with pytest.raises(ValueError): attachment.anchor(owner, "tip")


def test_missing_ambiguous_and_shared_anchor_are_distinct():
    owner = etree.fromstring(FIXTURE.read_bytes())
    with pytest.raises(ValueError, match="found 0"): attachment.anchor(owner, "WAPClip")
    owner.find("Skeleton/Bones/Item/Name").text = "tip"
    with pytest.raises(ValueError, match="found 2"): attachment.anchor(owner, "tip")
    owner.remove(owner.find("Skeleton"))
    with pytest.raises(LookupError, match="shared rig"): attachment.anchor(owner, "tip")


@pytest.mark.parametrize("data", [b'<!DOCTYPE x><x/>', WEAPON.replace(b'<AttachBone>tip</AttachBone>', b'<AttachBone/>')])
def test_invalid_metadata_rejected(data):
    with pytest.raises(ValueError): attachment.definitions(data, "input")


def test_non_rendered_component_is_not_invented_as_an_asset():
    data = COMPONENT.replace(b'<Model>clip</Model>', b'<Model null="true"/>')
    assert attachment.definitions(data, "components") == ([], [])


def test_rejected_context_does_not_retain_over_limit_partial_records():
    context = attachment.Attachments(lambda *args: None)
    context.links = [{}] * 8000
    with pytest.raises(ValueError, match="8,000"):
        context.metadata(COMPONENT, "components")
    assert not context.components


def package_fixture(root):
    root.mkdir()
    for name in ("body", "clip"):
        (root / f"{name}.ydr.xml").write_bytes(FIXTURE.read_bytes())
    (root / "weapons.meta").write_bytes(WEAPON)
    (root / "components.meta").write_bytes(COMPONENT)
    return root


def codes(report):
    check = next(c for c in report["checks"] if c["category"] == "attachments")
    return {f["code"] for f in check["findings"]}


def test_package_resolves_exact_sources_but_not_runtime_assembly(tmp_path):
    root = package_fixture(tmp_path / "package")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    report = package_validation.inspect(str(root))
    evidence = report["attachment_bindings"]
    assert len(evidence) == 1
    assert evidence[0]["parent_source"] == "body.ydr.xml"
    assert evidence[0]["child_source"] == "clip.ydr.xml"
    assert evidence[0]["skeleton_matrix"][2][3] == 1
    assert {"attachment_anchor_resolved", "attachment_runtime_unverified"} <= codes(report)
    assert report["runtime_status"] == "not_tested"
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize("variant,expected", [("missing", "attachment_model_unresolved"), ("duplicate", "attachment_component_unresolved"), ("owner", "attachment_owner_ambiguous"), ("anchor", "attachment_anchor_invalid"), ("mismatch", "attachment_bone_declarations_differ")])
def test_package_never_guesses_binding(tmp_path, variant, expected):
    root = package_fixture(tmp_path / "package")
    if variant == "missing": (root / "clip.ydr.xml").unlink()
    elif variant == "duplicate": (root / "duplicate.meta").write_bytes(COMPONENT)
    elif variant == "owner":
        model = etree.fromstring(FIXTURE.read_bytes()); model.tag = "Item"
        data = etree.tostring(model)
        (root / "body.ydr.xml").write_bytes(b'<DrawableDictionary>' + data + data + b'</DrawableDictionary>')
    elif variant == "anchor": (root / "weapons.meta").write_bytes(WEAPON.replace(b'>tip<', b'>missing<'))
    else: (root / "components.meta").write_bytes(COMPONENT.replace(b'>tip<', b'>different<'))
    report = package_validation.inspect(str(root))
    assert expected in codes(report)
    assert bool(report["attachment_bindings"]) == (variant == "mismatch")

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

import allin1_sdk.material_progression as progression
from allin1_sdk.addon_importer import AddonPackageInspector
from allin1_sdk.cli import main
from allin1_sdk.material_progression import audit_material_progressions
from allin1_sdk.mods import ModIntegrationService, open_mod_package
from allin1_sdk.native_assets import resolve_shader_name
from allin1_sdk.rpf_tools import RpfEntryRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PROJECT = PROJECT_ROOT.parent / "GTAV-SUPPRESSORS-ENHANCED"
PRIVATE_PACKAGE = (
    PRIVATE_PROJECT / "mods" / "realistic-suppressors" / "dist"
    / "Suppressors-Enhanced-ALLIN1-1.1.0.zip"
)
ENHANCED_GAME = Path(
    r"D:\Programs\Steam\steamapps\common\Grand Theft Auto V Enhanced"
)


def _entry(name: str, number: int) -> RpfEntryRecord:
    return RpfEntryRecord(
        id=f"entry-{number}", archive_path="x64/models/content.rpf",
        path=name, name=name, kind="file", size=64, stored_size=64,
    )


class _Index:
    edition = "Enhanced"

    def __init__(self) -> None:
        self.entries = (
            _entry("thermal_01.ydr", 1), _entry("thermal_02.ydr", 2),
            _entry("thermal.ydr", 3), _entry("thermal.ytd", 4),
            _entry("thermal.ytyp", 5),
        )


class _Service:
    patcher = Path("unused.exe")
    gta_path = Path("unused-game")

    def extract_many(self, _index, entries, destination):
        target = Path(destination)
        target.mkdir()
        result = []
        for number, entry in enumerate(entries, start=1):
            path = target / f"{number:04d}{entry.suffix}"
            path.write_bytes(b"asset")
            result.append(path)
        return tuple(result)


def _fake_converted(
    inputs: list[Path], output: Path, _edition: str, *, scenario: str,
) -> tuple[Path, ...]:
    multipliers = {
        "good": (0.10, 0.20, 0.30),
        "bad": (0.20, 0.10, 0.30),
        "structural": (0.10, 0.20, 0.30),
        "similar": (0.0, 0.0, 0.0),
    }[scenario]
    textures = {
        "good": ("gradient_01", "gradient_02", "gradient_03"),
        "bad": ("gradient_01", "gradient_01", "gradient_03"),
        "structural": ("gradient_01", "missing_gradient", "gradient_03"),
        "similar": ("gradient_01", "gradient_02", "gradient_03"),
    }[scenario]
    xml_files = []
    for number, _source in enumerate(inputs, start=1):
        xml = output / f"{number:04d}.xml"
        xml_files.append(xml)
        if number <= 3:
            shader = (
                "vehicle_basic.sps"
                if scenario == "structural" and number == 3
                else "hash_59B24D3D"
            )
            extra_geometry = (
                "<Item><VertexBuffer><Data>0 0 0\n1 0 0\n0 1 0</Data></VertexBuffer>"
                "<IndexBuffer><Data>0 1 2</Data></IndexBuffer></Item>"
                if scenario == "structural" and number == 2 else ""
            )
            xml.write_text(
                "<Drawable><ShaderGroup><Shaders><Item>"
                f"<Name>{shader}</Name><Parameters>"
                f"<Item name='DiffuseSampler' type='Texture'><Name>{textures[number - 1]}</Name></Item>"
                f"<Item name='emissiveMultiplier' type='Vector' x='{multipliers[number - 1]}'/>"
                "</Parameters></Item></Shaders></ShaderGroup>"
                "<DrawableModelsHigh><Item><Geometries><Item>"
                "<VertexBuffer><Data>0 0 0\n1 0 0\n0 1 0</Data></VertexBuffer>"
                "<IndexBuffer><Data>0 1 2</Data></IndexBuffer>"
                f"</Item>{extra_geometry}</Geometries></Item></DrawableModelsHigh></Drawable>",
                encoding="utf-8",
            )
        elif number == 4:
            xml.write_text(
                "<TextureDictionary><Item/><Item/><Item/></TextureDictionary>",
                encoding="utf-8",
            )
            assets = output / "assets-0004"
            assets.mkdir()
            for level, name in enumerate(("gradient_01", "gradient_02", "gradient_03"), start=1):
                image = Image.new("RGBA", (16, 4))
                if scenario == "structural" and level == 1:
                    pixels = [(96, 96, 96, 255)] * 64
                elif scenario == "structural" and level == 3:
                    pixels = [(0, 0, 0, 0)] * 64
                elif scenario == "similar":
                    pixels = [(0, 0, 0, 0)] * 64
                else:
                    floor = 52 if scenario == "bad" and level == 1 else 0
                    pixels = [
                        (level * 50, level * 20, 0, max(floor, round(255 * level * x / 48)))
                        for _y in range(4) for x in range(16)
                    ]
                image.putdata(pixels)
                image.save(assets / f"{name}.dds", format="DDS")
        else:
            xml.write_text(
                "<CMapTypes><archetypes><Item/><Item/><Item/></archetypes></CMapTypes>",
                encoding="utf-8",
            )
    return tuple(xml_files)


@pytest.mark.parametrize("bad", [False, True])
def test_material_progression_audit_resolves_and_compares_tiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: bool,
) -> None:
    monkeypatch.setattr(
        progression, "_run_batch_conversion",
        lambda service, inputs, output, edition: _fake_converted(
            inputs, output, edition, scenario="bad" if bad else "good",
        ),
    )
    report = audit_material_progressions(
        _Service(), _Index(), tmp_path / "audit", source="fixture/dlc.rpf",
    )[0]
    assert report.model_count == 3
    assert report.texture_count == 3
    assert report.archetype_count == 3
    assert report.preview_png is not None
    assert report.tiers[0].shader == "weapon_emissivestrong_alpha.sps"
    codes = {item.code for item in report.findings}
    if bad:
        assert {
            "first_tier_alpha_floor_high", "identical_texture_bindings",
            "non_monotonic_emissive",
        } <= codes
    else:
        assert not {
            "first_tier_alpha_floor_high", "identical_texture_bindings",
            "non_monotonic_emissive", "missing_texture_reference",
        }.intersection(codes)


@pytest.mark.parametrize(("scenario", "expected"), [
    ("structural", {
        "missing_texture_reference", "first_tier_alpha_floor_high",
        "first_tier_luminance_floor_high", "non_monotonic_alpha",
        "tier_geometry_changed", "tier_shader_changed",
    }),
    ("similar", {"neighboring_tiers_too_similar"}),
])
def test_material_progression_audit_covers_each_diagnostic_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    scenario: str, expected: set[str],
) -> None:
    monkeypatch.setattr(
        progression, "_run_batch_conversion",
        lambda service, inputs, output, edition: _fake_converted(
            inputs, output, edition, scenario=scenario,
        ),
    )
    report = audit_material_progressions(
        _Service(), _Index(), tmp_path / "audit", source="fixture/dlc.rpf",
    )[0]
    assert expected <= {item.code for item in report.findings}


def test_known_suppressor_shader_hash_is_resolved() -> None:
    assert resolve_shader_name("hash_59B24D3D") == "weapon_emissivestrong_alpha.sps"
    assert resolve_shader_name("vehicle_basic.sps") == "vehicle_basic.sps"


def test_schema_two_package_declares_script_vanilla_and_visual_relationships(
    tmp_path: Path,
) -> None:
    package = tmp_path / "scripted-enhancement"
    payload = package / "payload"
    payload.mkdir(parents=True)
    (payload / "Enhancement.dll").write_bytes(b"MZ")
    (payload / "dlc.rpf").write_bytes(b"RPF7 fixture")
    content = {
        "schema_version": 1,
        "api_version": 1,
        "id": "test.scripted-enhancement",
        "name": "Scripted Enhancement",
        "version": "1.0.0",
        "capabilities": ["weapon.components.lifecycle"],
        "systems": [{
            "id": "scripted-enhancement", "name": "Scripted Enhancement",
            "category": "Weapons", "settings": [],
        }],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": [{
            "path": "scripts/Test/Enhancement.dll",
            "entry_point": "Test.Enhancement.Controller",
        }]},
        "workbench": {"weapon_enhancements": [{
            "id": "test.suppressor-heat",
            "name": "Suppressor heat",
            "mode": "scripted_vanilla_components",
            "weapon_components": [{
                "weapon_name": "WEAPON_PISTOL", "weapon_hash": "0x1B06D571",
                "component_name": "COMPONENT_AT_PI_SUPP_02",
                "component_hash": "0x65EA7EBB",
            }],
            "script_entry_points": ["Test.Enhancement.Controller"],
            "visual_assets": [{
                "dlc_pack": "test_heat",
                "archive": "x64/models/cdimages/test_heat.rpf",
                "families": ["pistol"], "levels": 3,
                "model_pattern": "test_heat_{family}_{level:02d}.ydr",
                "base_model_pattern": "test_heat_{family}.ydr",
                "texture_dictionary": "test_heat.ytd",
                "texture_pattern": "test_heat_gradient_{level:02d}",
                "archetype_dictionary": "test_heat.ytyp",
                "base_level_uses_unsuffixed": True,
            }],
        }]},
    }
    (package / "allin1.content.json").write_text(
        json.dumps(content), encoding="utf-8",
    )
    (package / "mod.toml").write_text(
        '''schema_version = 2
id = "test.scripted-enhancement"
name = "Scripted Enhancement"
version = "1.0.0"
type = "mixed"
editions = ["enhanced"]
dependencies = ["shvdn"]
conflicts = []
dlc_packs = ["test_heat"]

[allin1]
api_version = 1
content = "allin1.content.json"
requires = []

[[files]]
source = "payload/Enhancement.dll"
destination = "scripts/Test/Enhancement.dll"

[[files]]
source = "payload/dlc.rpf"
destination = "mods/update/x64/dlcpacks/test_heat/dlc.rpf"

[[files]]
source = "allin1.content.json"
destination = "scripts/Test/allin1.content.json"
''', encoding="utf-8",
    )
    scan = AddonPackageInspector().inspect(package)
    assert len(scan.weapons) == 0
    assert len(scan.weapon_enhancements) == 1
    enhancement = scan.weapon_enhancements[0]
    assert enhancement.mode == "scripted_vanilla_components"
    assert enhancement.weapon_components[0].weapon_hash == "0x1B06D571"
    assert enhancement.weapon_components[0].component_hash == "0x65EA7EBB"
    assert enhancement.script_entry_points == ("Test.Enhancement.Controller",)
    assert enhancement.visual_assets[0].base_model_pattern == "test_heat_{family}.ydr"
    assert scan.scripted_weapon_systems[0].relationships_declared is True


@pytest.mark.skipif(
    not PRIVATE_PACKAGE.is_file() or not ENHANCED_GAME.is_dir(),
    reason="private suppressor fixture and Enhanced GTA installation are local-only",
)
def test_private_suppressor_package_is_an_end_to_end_regression_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with open_mod_package(PRIVATE_PACKAGE) as manifest:
        assert manifest.schema_version == 2
        assert manifest.extension is not None
        # Prove the same SDK lifecycle that the launcher uses can install the
        # schema-2 package without touching the real game installation.
        game = tmp_path / "GTA V Enhanced"
        game.mkdir()
        (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
        service = ModIntegrationService(game)
        monkeypatch.setattr(service, "_check_dependencies", lambda _manifest: None)
        monkeypatch.setattr(service, "_set_dlc_registration", lambda _pack, _enabled: True)
        status = service.install(manifest)
        assert status.installed and status.mod_id == "realistic-suppressors"
        assert (game / "mods/update/x64/dlcpacks/rs_suppressor_heat/dlc.rpf").is_file()

    scan = AddonPackageInspector(PROJECT_ROOT, ENHANCED_GAME).inspect(PRIVATE_PACKAGE)
    assert "scripted_weapon_enhancement" in scan.package_kinds
    assert len(scan.scripted_weapon_systems) == 1
    assert not any(item.code == "opaque_rpf" for item in scan.findings)
    assert scan.rpf_archives[0].suffix_counts[".ydr"] == 120
    assert scan.rpf_archives[0].suffix_counts[".ytd"] == 1
    assert scan.rpf_archives[0].suffix_counts[".ytyp"] == 1
    report = scan.material_progressions[0]
    assert (report.model_count, report.texture_count, report.archetype_count) == (120, 24, 120)
    assert report.preview_png is not None
    assert report.tiers[0].texture == "rs_suppressor_heat_gradient_01"
    assert report.tiers[0].emissive_multiplier == pytest.approx(0.0045044404)
    assert report.tiers[11].texture == "rs_suppressor_heat_gradient_12"
    assert report.tiers[11].emissive_multiplier == pytest.approx(0.3077861)
    assert report.tiers[23].texture == "rs_suppressor_heat_gradient_24"
    assert report.tiers[23].emissive_multiplier == pytest.approx(1.0)

    cli_result = CliRunner().invoke(main, [
        "inspect-workbench", str(PRIVATE_PACKAGE), "--category", "weapons",
        "--gta-path", str(ENHANCED_GAME),
    ])
    assert cli_result.exit_code == 0, cli_result.output
    payload = json.loads(cli_result.output)
    assert payload["operation"] == "inspect_workbench"
    assert payload["summary"]["rpf_native_assets"] == 122
    assert payload["summary"]["material_progressions"] == 1
    cli_tiers = payload["material_progressions"][0]["tiers"]
    assert cli_tiers[0]["shader"] == "weapon_emissivestrong_alpha.sps"
    assert cli_tiers[11]["emissive_multiplier"] == pytest.approx(0.3077861)
    assert cli_tiers[23]["texture"] == "rs_suppressor_heat_gradient_24"

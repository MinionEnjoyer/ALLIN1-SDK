from __future__ import annotations

import json
import zipfile
from pathlib import Path

from click.testing import CliRunner

from allin1_sdk import vehicle_quick_import
from allin1_sdk.addon_importer import (
    PackageEntry,
    PackageRegistrationRecord,
    PackageScan,
    RpfPackageRecord,
    VehicleRecord,
)
from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.cli import main


LEGACY_RPF = b"quick-import-cli-legacy"
ENHANCED_RPF = b"quick-import-cli-enhanced"


def _source(root: Path) -> Path:
    source = root / "vehicle.zip"
    with zipfile.ZipFile(source, "w") as package:
        package.writestr("Legacy/lunga/dlc.rpf", LEGACY_RPF)
        package.writestr("Enhanced/lunga/dlc.rpf", ENHANCED_RPF)
    return source


def _vehicle(edition: str, member: str) -> VehicleRecord:
    return VehicleRecord(
        source=f"{member}!data/vehicles.meta",
        model_name="lunga",
        txd_name="lunga",
        handling_id="lunga",
        game_name="LUNGA",
        make_name="null",
        audio_name_hash="T20",
        layout="LAYOUT_STANDARD",
        vehicle_type="VEHICLE_TYPE_CAR",
        vehicle_class="VC_SUPER",
        edition=edition,
    )


def _scan(source: Path) -> PackageScan:
    legacy = "Legacy/lunga/dlc.rpf"
    enhanced = "Enhanced/lunga/dlc.rpf"
    return PackageScan(
        source=source,
        source_kind="zip",
        entries=(
            PackageEntry(legacy, len(LEGACY_RPF)),
            PackageEntry(enhanced, len(ENHANCED_RPF)),
        ),
        findings=(),
        weapons=(),
        ammo=(),
        animation_weapons=(),
        shop_weapons=(),
        vehicles=(
            _vehicle("legacy", legacy),
            _vehicle("enhanced", enhanced),
        ),
        registrations=(
            PackageRegistrationRecord(
                f"{legacy}!content.xml", "single-player-content",
                ("dlc_lunga",), ("vehicles.meta",),
            ),
            PackageRegistrationRecord(
                f"{enhanced}!content.xml", "single-player-content",
                ("dlc_lunga",), ("vehicles.meta",),
            ),
        ),
        rpf_archives=(
            RpfPackageRecord(legacy, "legacy", 2, 10, {".yft": 1}),
            RpfPackageRecord(enhanced, "enhanced", 2, 10, {".yft": 1}),
        ),
    )


class _Inspector:
    def __init__(self, scan: PackageScan) -> None:
        self.scan = scan

    def inspect(self, source: Path) -> PackageScan:
        assert source.resolve() == self.scan.source.resolve()
        return self.scan


def _fixture(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    source = _source(tmp_path)
    scan = _scan(source)
    monkeypatch.setattr(
        vehicle_quick_import,
        "AddonPackageInspector",
        lambda *_args, **_kwargs: _Inspector(scan),
    )
    game = tmp_path / "game"
    game.mkdir()
    return source, game


def test_quick_import_commands_have_fail_closed_agent_risks():
    catalog = {item["name"]: item for item in command_catalog()}

    assert catalog["inspect-vehicle-quick-import"]["risk"] == "read_only"
    assert catalog["prepare-vehicle-quick-import"]["risk"] == "authoring_write"
    parameters = {
        item["name"]: item
        for item in catalog["prepare-vehicle-quick-import"]["parameters"]
    }
    assert parameters["listing_assignments"]["multiple"] is True
    assert parameters["edition"]["choices"] == ["legacy", "enhanced"]


def test_cli_inspects_and_prepares_default_launcher_library_without_game_write(
    monkeypatch, tmp_path: Path,
):
    source, game = _fixture(monkeypatch, tmp_path)
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    runner = CliRunner()

    inspected = runner.invoke(main, [
        "inspect-vehicle-quick-import", str(source),
        "--gta-path", str(game), "--preferred-edition", "legacy",
    ])
    assert inspected.exit_code == 0, inspected.output
    inspection = json.loads(inspected.output)
    assert inspection["operation"] == "inspect_vehicle_quick_import"
    assert inspection["available_editions"] == ["legacy", "enhanced"]
    assert inspection["suggested_edition"] == "legacy"

    prepared = runner.invoke(main, [
        "prepare-vehicle-quick-import", str(source),
        "--edition", "enhanced", "--gta-path", str(game),
        "--package-id", "fixture.quickcar",
        "--package-name", "Quick Car",
        "--set", "lunga.name=Huayra Codalunga",
        "--set", "lunga.manufacturer=Pagani",
        "--set", "lunga.price=2350000",
        "--set", "lunga.traffic_enabled=false",
    ])
    assert prepared.exit_code == 0, prepared.output
    payload = json.loads(prepared.output)
    expected = (local / "ALLIN1" / "Packages" / "fixture.quickcar").resolve()
    assert payload["operation"] == "prepare_vehicle_quick_import"
    assert payload["game_write_performed"] is False
    assert payload["launcher_install_required"] is True
    assert payload["launcher_library"] is True
    assert Path(payload["package"]["package_root"]) == expected
    listing = payload["package"]["plan"]["catalog"]["vehicles"][0]
    assert listing["name"] == "Huayra Codalunga"
    assert listing["manufacturer"] == "Pagani"
    assert listing["price"] == 2_350_000
    assert listing["traffic"]["enabled"] is False
    assert not any(game.iterdir())


def test_prepare_rejects_malformed_or_untyped_listing_assignments(
    monkeypatch, tmp_path: Path,
):
    source, game = _fixture(monkeypatch, tmp_path)
    runner = CliRunner()
    base = [
        "prepare-vehicle-quick-import", str(source),
        "--edition", "enhanced", "--gta-path", str(game),
        "--destination", str(tmp_path / "output"),
    ]

    malformed = runner.invoke(main, [*base, "--set", "bad"])
    assert malformed.exit_code != 0
    assert "MODEL.FIELD=VALUE" in malformed.output
    untyped = runner.invoke(main, [*base, "--set", "lunga.price=many"])
    assert untyped.exit_code != 0
    assert "must be an integer" in untyped.output
    unsupported = runner.invoke(main, [*base, "--set", "lunga.hash=123"])
    assert unsupported.exit_code != 0
    assert "Unsupported listing assignment" in unsupported.output


def test_agent_api_executes_inspection_and_authoring_without_game_write_opt_in(
    monkeypatch, tmp_path: Path,
):
    source, game = _fixture(monkeypatch, tmp_path)
    audit = tmp_path / "agent-audit.jsonl"

    inspected = execute_request({
        "id": "inspect", "action": "execute",
        "command": "inspect-vehicle-quick-import",
        "args": [str(source), "--gta-path", str(game)],
    }, audit_path=audit)
    assert inspected["ok"] is True
    assert inspected["risk"] == "read_only"
    inspection = json.loads(inspected["result"]["output"])
    assert inspection["operation"] == "inspect_vehicle_quick_import"

    destination = tmp_path / "agent-package"
    prepared = execute_request({
        "id": "prepare", "action": "execute",
        "command": "prepare-vehicle-quick-import",
        "args": [
            str(source), "--edition", "enhanced",
            "--gta-path", str(game),
            "--package-id", "fixture.agentcar",
            "--destination", str(destination),
            "--set", "lunga.traffic_enabled=false",
            "--set", "lunga.free_price_confirmed=true",
        ],
    }, audit_path=audit)
    assert prepared["ok"] is True
    assert prepared["risk"] == "authoring_write"
    result = json.loads(prepared["result"]["output"])
    assert result["game_write_performed"] is False
    assert result["package"]["package_root"] == str(destination.resolve())
    assert destination.is_dir()
    assert not any(game.iterdir())

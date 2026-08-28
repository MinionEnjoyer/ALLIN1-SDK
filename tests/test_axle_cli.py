from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    PRESET_STEER_DRIVE_REAR,
    detect_axle_configuration,
    retarget_axle_configuration,
)
from allin1_sdk.cli import main
from allin1_sdk.axle_runtime_bundler import (
    VehicleAxleBuildInput,
    compatibility_configuration,
)


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def _config(tmp_path: Path) -> tuple[Path, object]:
    bones = tuple(
        Bone(name, (x, y, 0.0))
        for left, right, y in (
            ("wheel_lf", "wheel_rf", 8.0),
            ("wheel_lm1", "wheel_rm1", 4.0),
            ("wheel_lr", "wheel_rr", 0.0),
        )
        for name, x in ((left, -1.25), (right, 1.25))
    )
    config = detect_axle_configuration(
        "allin1_cli_bus", bones, preset=PRESET_STEER_DRIVE_REAR,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    path = tmp_path / "bus.json"
    path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    return path, config


def test_story_runtime_config_export_translates_workbench_document(
    tmp_path: Path,
) -> None:
    path, config = _config(tmp_path)
    story = retarget_axle_configuration(config, "story-legacy")
    path.write_text(json.dumps(story.to_dict()), encoding="utf-8")
    output = tmp_path / "metrobus.axles.json"

    result = CliRunner().invoke(main, [
        "export-story-axle-runtime-config", str(path),
        "--output", str(output), "--acknowledge-edit",
    ])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["operation"] == "export_story_axle_runtime_config"
    assert report["target"] == "story-legacy"
    assert report["game_write_performed"] is False
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["modelName"] == "allin1_cli_bus"
    assert payload["expectedWheelCount"] == 6
    assert payload["compatibility"] == {"story-legacy": True}
    assert payload["wheelIndexMapping"]["by_bone"] == {
        "wheel_lf": 0,
        "wheel_rf": 1,
        "wheel_lr": 2,
        "wheel_rr": 3,
        "wheel_lm1": 4,
        "wheel_rm1": 5,
    }

    duplicate = CliRunner().invoke(main, [
        "export-story-axle-runtime-config", str(path),
        "--output", str(output), "--acknowledge-edit",
    ])
    assert duplicate.exit_code != 0
    assert "--update" in duplicate.output


def test_story_runtime_config_live_output_requires_agent_api_authority(
    tmp_path: Path,
) -> None:
    path, config = _config(tmp_path)
    story = retarget_axle_configuration(config, "story-legacy")
    path.write_text(json.dumps(story.to_dict()), encoding="utf-8")
    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    output = game / "VehicleWorkbenchAxles" / "configs" / "metrobus.axles.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"old": true}\n', encoding="utf-8")
    arguments = [
        str(path), "--output", str(output), "--update", "--acknowledge-edit",
    ]

    direct = CliRunner().invoke(main, [
        "export-story-axle-runtime-config", *arguments,
    ])
    assert direct.exit_code != 0
    assert "--acknowledge-game-write" in direct.output
    assert json.loads(output.read_text(encoding="utf-8")) == {"old": True}

    acknowledged_arguments = [*arguments, "--acknowledge-game-write"]
    denied = execute_request({
        "id": "live-config-denied", "action": "execute",
        "command": "export-story-axle-runtime-config",
        "args": acknowledged_arguments,
    }, audit_path=tmp_path / "agent-audit.jsonl")
    assert denied["ok"] is False
    assert denied["risk"] == "game_write"
    assert "--allow-game-writes" in denied["error"]
    assert json.loads(output.read_text(encoding="utf-8")) == {"old": True}

    missing_acknowledgement = execute_request({
        "id": "live-config-missing-ack", "action": "execute",
        "command": "export-story-axle-runtime-config", "args": arguments,
    }, allow_game_writes=True, audit_path=tmp_path / "agent-audit.jsonl")
    assert missing_acknowledgement["ok"] is False
    assert missing_acknowledgement["risk"] == "game_write"
    assert "--acknowledge-game-write" in (
        missing_acknowledgement["result"]["output"]
    )
    assert json.loads(output.read_text(encoding="utf-8")) == {"old": True}

    legacy_temporary = output.with_name(f".{output.name}.tmp")
    legacy_temporary.write_text("do-not-touch", encoding="utf-8")
    allowed = execute_request({
        "id": "live-config-allowed", "action": "execute",
        "command": "export-story-axle-runtime-config",
        "args": acknowledged_arguments,
    }, allow_game_writes=True, audit_path=tmp_path / "agent-audit.jsonl")
    assert allowed["ok"] is True, allowed
    assert allowed["risk"] == "game_write"
    report = json.loads(allowed["result"]["output"])
    assert report["game_write_performed"] is True
    assert json.loads(output.read_text(encoding="utf-8"))["modelName"] == (
        "allin1_cli_bus"
    )
    assert legacy_temporary.read_text(encoding="utf-8") == "do-not-touch"

    declared_game = tmp_path / "declared-game-root"
    declared_game.mkdir()
    declared_output = declared_game / "scripts" / "metrobus.axles.json"
    explicit = CliRunner().invoke(main, [
        "export-story-axle-runtime-config", str(path),
        "--output", str(declared_output), "--gta-path", str(declared_game),
        "--acknowledge-edit",
    ])
    assert explicit.exit_code != 0
    assert "--acknowledge-game-write" in explicit.output
    assert not declared_output.exists()

    explicit_acknowledged = CliRunner().invoke(main, [
        "export-story-axle-runtime-config", str(path),
        "--output", str(declared_output), "--gta-path", str(declared_game),
        "--acknowledge-game-write", "--acknowledge-edit",
    ])
    assert explicit_acknowledged.exit_code == 0, explicit_acknowledged.output
    explicit_report = json.loads(explicit_acknowledged.output)
    assert explicit_report["game_write_performed"] is True
    assert declared_output.is_file()


def test_axle_bundle_live_destination_is_staged_only_and_path_sensitive(
    tmp_path: Path,
) -> None:
    path, _configuration = _config(tmp_path)
    game = tmp_path / "Grand Theft Auto V Enhanced"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    output = game / "axle-bundle"
    arguments = [
        str(path), "--target", "fivem-legacy", "--output-dir", str(output),
        "--acknowledge-edit",
    ]

    direct = CliRunner().invoke(main, ["build-axle-runtime-bundle", *arguments])
    assert direct.exit_code != 0
    assert "staged-only" in direct.output
    assert not output.exists()

    denied = execute_request({
        "id": "live-bundle-denied", "action": "execute",
        "command": "build-axle-runtime-bundle", "args": arguments,
    }, audit_path=tmp_path / "agent-audit.jsonl")
    assert denied["ok"] is False
    assert denied["risk"] == "game_write"
    assert "--allow-game-writes" in denied["error"]

    still_refused = execute_request({
        "id": "live-bundle-authorized", "action": "execute",
        "command": "build-axle-runtime-bundle", "args": arguments,
    }, allow_game_writes=True, audit_path=tmp_path / "agent-audit.jsonl")
    assert still_refused["ok"] is False
    assert still_refused["risk"] == "game_write"
    assert "staged-only" in still_refused["result"]["output"]
    assert not output.exists()


def test_prefab_and_bundle_commands_are_structured_and_api_classified(tmp_path: Path) -> None:
    runner = CliRunner()
    listed = runner.invoke(main, [
        "list-axle-prefabs", "--axle-count", "3", "--category", "bus",
    ])
    assert listed.exit_code == 0, listed.output
    assert {item["id"] for item in json.loads(listed.output)["prefabs"]} >= {
        "6x2_rear_steer_bus",
    }

    path, _configuration = _config(tmp_path)
    planned = runner.invoke(main, [
        "plan-axle-runtime-bundle", str(path), "--target", "fivem-legacy",
    ])
    assert planned.exit_code == 0, planned.output
    payload = json.loads(planned.output)
    assert payload["targets"][0]["status"] == "ready"
    assert payload["targets"][0]["configurations"][0]["expectedWheelCount"] == 6

    output = tmp_path / "bundle"
    built = runner.invoke(main, [
        "build-axle-runtime-bundle", str(path), "--target", "fivem-legacy",
        "-o", str(output), "--acknowledge-edit",
    ])
    assert built.exit_code == 0, built.output
    assert (output / "fivem-legacy" / "axle-runtime" / "fxmanifest.lua").is_file()

    risks = {item["name"]: item["risk"] for item in command_catalog()}
    assert risks["list-axle-prefabs"] == "read_only"
    assert risks["plan-axle-runtime-bundle"] == "read_only"
    assert risks["build-axle-runtime-bundle"] == "authoring_write"
    assert risks["inspect-story-axle-runtimes"] == "read_only"
    assert risks["plan-axle-oiv"] == "authoring_write"
    assert risks["build-axle-oiv"] == "authoring_write"
    assert risks["export-story-axle-runtime-config"] == "authoring_write"

    story = runner.invoke(main, [
        "inspect-story-axle-runtimes",
        "--game-build", "story-legacy=build-123",
    ])
    assert story.exit_code == 0, story.output
    story_payload = json.loads(story.output)
    assert story_payload["implicit_profiles_loaded"] is False
    assert story_payload["targets"]["story-legacy"] == {
        "requested_game_build": "build-123",
        "profile": None,
        "build_mapped": False,
        "package_eligible_for_build": False,
        "reason": "No explicit Story runtime profile was supplied",
    }

    plan_help = runner.invoke(main, ["plan-axle-runtime-bundle", "--help"])
    assert plan_help.exit_code == 0
    assert "--story-profile" in plan_help.output
    assert "--game-build" in plan_help.output
    assert "--skeleton-xml" in plan_help.output
    assert "Required for signed/schema-2 steering" in plan_help.output


def test_oiv_cli_preview_and_build_use_same_persisted_identity(tmp_path: Path) -> None:
    path, configuration = _config(tmp_path)
    del path
    stage = tmp_path / "stage"
    (stage / "vehicle").mkdir(parents=True)
    (stage / "configs").mkdir()
    archive = stage / "vehicle" / "dlc.rpf"
    archive.write_bytes(b"RPF7fixture-rpf")
    runtime_payload = compatibility_configuration(
        VehicleAxleBuildInput(
            configuration=configuration,
            configuration_id=configuration.configuration_id,
            model_hash=configuration.model_hash,
            minimum_runtime_version=configuration.minimum_runtime_version,
        ),
        "story-legacy",
    )
    (stage / "configs" / "allin1_cli_bus.json").write_text(
        json.dumps(runtime_payload), encoding="utf-8",
    )
    report = stage / "vehicle-validation-report.json"
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    report.write_text(json.dumps({
        "schema_version": 1,
        "operation": "vehicle_addon_package_build",
        "status": "validated",
        "editions": ["legacy"],
        "payload": {"path": "vehicle/dlc.rpf", "sha256": archive_sha},
        "safety": {
            "source_unchanged": True,
            "output_was_new": True,
            "stock_game_files_modified": False,
            "manifest_payload_validated": True,
        },
    }), encoding="utf-8")
    native_report = stage / "native-rpf-validation.json"
    native_report.write_text(json.dumps({
        "schema_version": 1,
        "operation": "validate_story_vehicle_rpf",
        "status": "validated",
        "archive_sha256": archive_sha,
        "edition": "legacy",
        "archive_count": 1,
        "entry_count": 5,
        "model_assets": {"allin1_cli_bus": {"yft": True, "ytd": True}},
        "required_metadata": {
            "vehicles.meta": True,
            "handling.meta": True,
            "carvariations.meta": True,
        },
        "game_write_performed": False,
    }), encoding="utf-8")
    (stage / "compatibility-manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "target": "story-legacy",
            "game_write_performed": False,
            "vehicle_artifacts": [{
                "path": "vehicle/dlc.rpf",
                "sha256": archive_sha,
                "asset_edition": "legacy",
                "asset_format": "legacy-rpf7-gen8",
                "validation_status": "validated",
                "validation_report": "vehicle-validation-report.json",
                "validation_report_sha256": hashlib.sha256(
                    report.read_bytes()
                ).hexdigest(),
                "native_validation_report": "native-rpf-validation.json",
                "native_validation_report_sha256": hashlib.sha256(
                    native_report.read_bytes()
                ).hexdigest(),
            }],
        }),
        encoding="utf-8",
    )
    request = {
        "staging_root": str(stage),
        "target": "story-legacy",
        "mode": "vehicle-only",
        "metadata": {
            "project_id": "com.allin1.cli-bus-project",
            "package_id": "com.allin1.cli-bus",
            "name": "CLI Bus",
            "version": "1.0.0",
            "author": "ALLIN1 test",
            "description": "CLI OIV integration fixture.",
            "workbench_version": "0.5.5",
        },
        "vehicle_dlcs": [{
            "dlc_pack_name": "vwb_cli_bus",
            "archive_path": "vehicle/dlc.rpf",
            "vehicle_models": ["allin1_cli_bus"],
            "asset_edition": "legacy",
        }],
        "axle_configurations": [{
            "model_name": "allin1_cli_bus",
            "model_hash": configuration.model_hash,
            "source_path": "configs/allin1_cli_bus.json",
            "schema_version": 1,
            "minimum_runtime_version": "1.0.0",
        }],
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    identities = tmp_path / "identities.json"
    runner = CliRunner()
    previewed = runner.invoke(main, [
        "plan-axle-oiv", str(request_path), "--identity-store", str(identities),
    ])
    assert previewed.exit_code == 0, previewed.output
    preview_guid = json.loads(previewed.output)["package_guid"]
    output = tmp_path / "cli-bus.oiv"
    built = runner.invoke(main, [
        "build-axle-oiv", str(request_path), "--identity-store", str(identities),
        "-o", str(output), "--acknowledge-edit",
    ])
    assert built.exit_code == 0, built.output
    assert json.loads(built.output)["package_guid"] == preview_guid
    assert output.is_file()

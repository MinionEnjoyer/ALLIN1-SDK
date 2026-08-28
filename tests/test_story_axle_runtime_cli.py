import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from allin1_sdk import cli as sdk_cli
from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.cli import main


def _toolchain_report(source_root: Path) -> SimpleNamespace:
    return SimpleNamespace(to_dict=lambda: {
        "ready": True,
        "platform": "nt",
        "source_root": str(source_root),
        "cmake_path": "C:/tools/cmake.exe",
        "cmake_version": "3.31.0",
        "ctest_path": "C:/tools/ctest.exe",
        "visual_studio_path": "C:/BuildTools",
        "problems": [],
    })


def test_inspect_story_axle_toolchain_uses_typed_builder_report(
    tmp_path, monkeypatch,
):
    source = tmp_path / "native-source"
    source.mkdir()
    observed = {}

    def inspect(*, source_root=None):
        observed["source_root"] = source_root
        return _toolchain_report(source)

    monkeypatch.setattr(sdk_cli, "inspect_native_axle_toolchain", inspect)
    result = CliRunner().invoke(main, [
        "inspect-story-axle-toolchain", "--source-root", str(source),
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "inspect_story_axle_toolchain"
    assert payload["ready"] is True
    assert Path(payload["source_root"]) == source
    assert observed["source_root"] == source


def test_build_story_axle_runtime_requires_acknowledgement_and_maps_settings(
    tmp_path, monkeypatch,
):
    output = tmp_path / "controller-candidate"
    observed = {}

    def build(request, *, source_root=None):
        observed["request"] = request
        observed["source_root"] = source_root
        return SimpleNamespace(to_dict=lambda: {
            "schema_version": 1,
            "operation": "build_story_axle_runtime",
            "output": str(output),
            "built_targets": list(request.targets),
        })

    monkeypatch.setattr(sdk_cli, "build_story_axle_runtime_candidate", build)
    runner = CliRunner()
    arguments = [
        "build-story-axle-runtime",
        "--target", "story-legacy",
        "--output-dir", str(output),
        "--configuration-directory", "scripts/Transit/configs",
        "--log-file", "scripts/Transit/Axles.log",
        "--discovery-interval-ms", "400",
        "--recovery-interval-ms", "2400",
        "--runtime-disabled",
        "--no-restore-on-unload",
        "--no-archives",
        "--build-id", "fixture:legacy-1",
    ]

    refused = runner.invoke(main, arguments)
    assert refused.exit_code != 0
    assert "--acknowledge-edit" in refused.output
    assert "request" not in observed

    built = runner.invoke(main, [*arguments, "--acknowledge-edit"])
    assert built.exit_code == 0, built.output
    payload = json.loads(built.output)
    assert payload["operation"] == "build_story_axle_runtime"
    assert payload["built_targets"] == ["story-legacy"]
    request = observed["request"]
    assert request.output_directory == output
    assert request.targets == ("story-legacy",)
    assert request.configurations == ()
    assert request.build_id == "fixture:legacy-1"
    assert request.create_archives is False
    assert request.settings.to_runtime_json() == {
        "schemaVersion": 2,
        "enabled": False,
        "discoveryIntervalMs": 400,
        "recoveryIntervalMs": 2400,
        "restoreOnUnload": False,
        "configurationDirectory": "scripts/Transit/configs",
        "logFile": "scripts/Transit/Axles.log",
    }
    assert observed["source_root"] is None


def test_story_axle_controller_commands_are_guarded_in_agent_api(
    tmp_path, monkeypatch,
):
    source = tmp_path / "native-source"
    source.mkdir()
    output = tmp_path / "controller-candidate"
    monkeypatch.setattr(
        sdk_cli, "inspect_native_axle_toolchain",
        lambda *, source_root=None: _toolchain_report(source),
    )
    monkeypatch.setattr(
        sdk_cli, "build_story_axle_runtime_candidate",
        lambda request, *, source_root=None: SimpleNamespace(to_dict=lambda: {
            "schema_version": 1,
            "operation": "build_story_axle_runtime",
            "output": str(request.output_directory),
            "built_targets": list(request.targets),
        }),
    )
    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["inspect-story-axle-toolchain"]["risk"] == "read_only"
    assert catalog["build-story-axle-runtime"]["risk"] == "authoring_write"

    inspected = execute_request({
        "id": "toolchain", "action": "execute",
        "command": "inspect-story-axle-toolchain",
        "args": ["--source-root", str(source)],
    }, audit_path=tmp_path / "audit.jsonl")
    assert inspected["ok"] is True
    assert inspected["risk"] == "read_only"

    missing_acknowledgement = execute_request({
        "id": "build-refused", "action": "execute",
        "command": "build-story-axle-runtime",
        "args": ["--output-dir", str(output)],
    }, audit_path=tmp_path / "audit.jsonl")
    assert missing_acknowledgement["ok"] is False
    assert missing_acknowledgement["risk"] == "authoring_write"
    assert "--acknowledge-edit" in missing_acknowledgement["result"]["output"]

    built = execute_request({
        "id": "build", "action": "execute",
        "command": "build-story-axle-runtime",
        "args": [
            "--target", "story-enhanced", "--output-dir", str(output),
            "--acknowledge-edit",
        ],
    }, audit_path=tmp_path / "audit.jsonl")
    assert built["ok"] is True
    assert built["risk"] == "authoring_write"
    assert json.loads(built["result"]["output"])["built_targets"] == [
        "story-enhanced",
    ]

    game = tmp_path / "Grand Theft Auto V Enhanced"
    game.mkdir()
    game_output = game / "controller-candidate"
    denied_game_path = execute_request({
        "id": "live-game-build", "action": "execute",
        "command": "build-story-axle-runtime",
        "args": [
            "--output-dir", str(game_output),
            "--gta-path", str(game),
            "--acknowledge-edit",
        ],
    }, audit_path=tmp_path / "audit.jsonl")
    assert denied_game_path["ok"] is False
    assert denied_game_path["risk"] == "game_write"
    assert "--allow-game-writes" in denied_game_path["error"]

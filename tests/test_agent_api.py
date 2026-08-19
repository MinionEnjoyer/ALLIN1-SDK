import hashlib
import io
import json
import sys

from allin1_sdk.agent_api import (
    command_catalog,
    command_risk,
    execute_request,
    serve_stdio,
)
from allin1_sdk import agent_host, cli as sdk_cli


def test_catalog_is_structured_and_classifies_risk():
    catalog = {item["name"]: item for item in command_catalog()}
    assert "agent-api" not in catalog
    assert catalog["validate"]["risk"] == "read_only"
    assert catalog["link"]["risk"] == "authoring_write"
    assert catalog["extract-rpf-subtree"]["risk"] == "authoring_write"
    assert catalog["diff-rpf"]["risk"] == "authoring_write"
    assert catalog["plan-rpf-batch"]["risk"] == "authoring_write"
    assert catalog["plan-rpf-sync"]["risk"] == "authoring_write"
    assert catalog["export-native-workspace"]["risk"] == "authoring_write"
    assert catalog["build-native-workspace"]["risk"] == "authoring_write"
    assert catalog["build-binary-workspace"]["risk"] == "authoring_write"
    assert catalog["build-gxt2-workspace"]["risk"] == "authoring_write"
    assert catalog["build-rpf-tree"]["risk"] == "authoring_write"
    assert catalog["catalog-rpfs"]["risk"] == "authoring_write"
    assert catalog["search-rpf-catalog"]["risk"] == "read_only"
    assert catalog["export-rpf-native-workspace"]["risk"] == "authoring_write"
    assert catalog["export-rpf-binary-workspace"]["risk"] == "authoring_write"
    assert catalog["export-rpf-gxt2-workspace"]["risk"] == "authoring_write"
    assert catalog["plan-rpf-native-workspace"]["risk"] == "authoring_write"
    assert catalog["plan-rpf-binary-workspace"]["risk"] == "authoring_write"
    assert catalog["plan-rpf-gxt2-workspace"]["risk"] == "authoring_write"
    assert catalog["list-gxt2-entries"]["risk"] == "read_only"
    assert catalog["set-gxt2-text"]["risk"] == "authoring_write"
    assert catalog["inspect-binary-workspace"]["risk"] == "read_only"
    assert catalog["patch-binary-workspace"]["risk"] == "authoring_write"
    assert catalog["verify-rpf-archive"]["risk"] == "authoring_write"
    assert catalog["list-ytd-textures"]["risk"] == "authoring_write"
    assert catalog["replace-ytd-texture"]["risk"] == "authoring_write"
    assert catalog["undo-ytd-texture-edit"]["risk"] == "authoring_write"
    assert catalog["apply-rpf-plan"]["risk"] == "game_write"
    assert catalog["install-package"]["risk"] == "game_write"
    assert catalog["list-installed-packages"]["risk"] == "read_only"
    assert catalog["uninstall-package"]["risk"] == "game_write"
    assert catalog["validate"]["parameters"][0]["kind"] == "argument"
    assert command_risk("unknown-future-command") == "read_only"


def test_ping_catalog_and_validation_errors(tmp_path):
    ping = execute_request({"id": 1, "action": "ping"}, audit_path=tmp_path / "a.jsonl")
    assert ping["ok"] is True
    assert ping["result"]["transport"] == "jsonl-stdio"
    assert ping["result"]["game_writes_enabled"] is False
    assert execute_request([], audit_path=tmp_path / "a.jsonl")["ok"] is False
    assert execute_request({"action": "bad"}, audit_path=tmp_path / "a.jsonl")["ok"] is False
    assert execute_request({"action": "execute", "args": []}, audit_path=tmp_path / "a.jsonl")["ok"] is False
    assert execute_request({"action": "execute", "command": "missing"}, audit_path=tmp_path / "a.jsonl")["ok"] is False
    assert execute_request({"action": "execute", "command": "agent-api"}, audit_path=tmp_path / "a.jsonl")["ok"] is False
    invalid_args = execute_request(
        {"action": "execute", "command": "list", "args": [1]},
        audit_path=tmp_path / "a.jsonl",
    )
    assert invalid_args["ok"] is False
    assert execute_request({"action": "catalog"})["result"]


def test_execute_uses_cli_without_shell_and_audits(tmp_path):
    audit = tmp_path / "audit.jsonl"
    result = execute_request(
        {"id": "list-1", "action": "execute", "command": "list", "args": []},
        audit_path=audit,
    )
    assert result["ok"] is True
    assert result["result"]["exit_code"] == 0
    assert result["risk"] == "read_only"
    record = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert record["request_id"] == "list-1"
    assert record["command"] == "list"


def test_game_write_requires_process_opt_in_and_command_acknowledgement(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    denied = execute_request({
        "id": 2, "action": "execute", "command": "apply-rpf-plan",
        "args": [str(plan)],
    }, audit_path=audit)
    assert denied["ok"] is False
    assert "--allow-game-writes" in denied["error"]
    allowed_process = execute_request({
        "id": 3, "action": "execute", "command": "apply-rpf-plan",
        "args": [str(plan)],
    }, allow_game_writes=True, audit_path=audit)
    assert allowed_process["ok"] is False
    assert "--acknowledge-write" in allowed_process["result"]["output"]


def test_api_lists_and_uninstalls_receipt_owned_package(tmp_path, monkeypatch):
    game = tmp_path / "Grand Theft Auto V Enhanced"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    plugin = game / "StraightToStoryMode.asi"
    plugin.write_bytes(b"managed-plugin")
    receipts = game / "scripts" / ".allin1" / "mods"
    receipts.mkdir(parents=True)
    receipt = receipts / "test.straight-to-story-mode.json"
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "id": "test.straight-to-story-mode",
        "name": "Straight To Story Mode",
        "version": "1.1",
        "type": "asi",
        "enabled": True,
        "files": [{"destination": "StraightToStoryMode.asi", "backup": None}],
    }), encoding="utf-8")
    monkeypatch.setattr(sdk_cli, "_running_gta_processes", lambda: ())
    audit = tmp_path / "audit.jsonl"

    listed = execute_request({
        "id": "packages", "action": "execute",
        "command": "list-installed-packages",
        "args": ["--gta-path", str(game)],
    }, audit_path=audit)
    assert listed["ok"] is True
    assert "test.straight-to-story-mode" in listed["result"]["output"]

    denied = execute_request({
        "id": "uninstall-denied", "action": "execute",
        "command": "uninstall-package",
        "args": ["test.straight-to-story-mode", "--gta-path", str(game),
                 "--acknowledge-write"],
    }, audit_path=audit)
    assert denied["ok"] is False
    assert "--allow-game-writes" in denied["error"]

    uninstalled = execute_request({
        "id": "uninstall", "action": "execute",
        "command": "uninstall-package",
        "args": ["test.straight-to-story-mode", "--gta-path", str(game),
                 "--acknowledge-write"],
    }, allow_game_writes=True, audit_path=audit)
    assert uninstalled["ok"] is True
    assert "receipt rollback" in uninstalled["result"]["output"]
    assert not plugin.exists()
    assert not receipt.exists()


def test_api_installs_validated_package_with_explicit_approval(tmp_path, monkeypatch):
    game = tmp_path / "Grand Theft Auto V Enhanced"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    package = tmp_path / "package"
    package.mkdir()
    payload = package / "ApiPackage.asi"
    payload.write_bytes(b"api-managed-package")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = package / "mod.toml"
    manifest.write_text(f'''schema_version = 1
id = "test.api-package"
name = "API Package"
version = "1.0"
type = "asi"
editions = ["enhanced"]
dependencies = []
conflicts = []

[[files]]
source = "ApiPackage.asi"
destination = "ApiPackage.asi"
sha256 = "{digest}"
''', encoding="utf-8")
    monkeypatch.setattr(sdk_cli, "_running_gta_processes", lambda: ())
    args = [str(manifest), "--gta-path", str(game), "--acknowledge-write"]

    denied = execute_request({
        "id": "install-denied", "action": "execute",
        "command": "install-package", "args": args,
    }, audit_path=tmp_path / "audit.jsonl")
    assert denied["ok"] is False
    assert "--allow-game-writes" in denied["error"]

    missing_acknowledgement = execute_request({
        "id": "install-no-ack", "action": "execute",
        "command": "install-package", "args": args[:-1],
    }, allow_game_writes=True, audit_path=tmp_path / "audit.jsonl")
    assert missing_acknowledgement["ok"] is False
    assert "--acknowledge-write" in missing_acknowledgement["result"]["output"]

    installed = execute_request({
        "id": "install", "action": "execute",
        "command": "install-package", "args": args,
    }, allow_game_writes=True, audit_path=tmp_path / "audit.jsonl")
    assert installed["ok"] is True
    assert "rollback ownership verified" in installed["result"]["output"]
    assert (game / "ApiPackage.asi").read_bytes() == payload.read_bytes()
    assert (
        game / "scripts" / ".allin1" / "mods" / "test.api-package.json"
    ).is_file()


def test_stdio_protocol_recovers_from_bad_and_large_requests(tmp_path):
    source = io.StringIO(
        '{bad json}\n'
        + json.dumps({"id": 4, "action": "ping"}) + "\n"
        + ("x" * (256 * 1024 + 1)) + "\n"
    )
    destination = io.StringIO()
    serve_stdio(source, destination, audit_path=tmp_path / "audit.jsonl")
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert responses[0]["ok"] is False
    assert responses[1]["id"] == 4 and responses[1]["ok"] is True
    assert responses[2]["error"] == "request exceeds the size limit"


def test_packaged_agent_host_forwards_stdio_and_write_policy(monkeypatch):
    captured = {}

    def fake_serve(source, destination, *, allow_game_writes=False):
        captured.update({
            "source": source, "destination": destination,
            "allow_game_writes": allow_game_writes,
        })

    monkeypatch.setattr(agent_host, "serve_stdio", fake_serve)
    agent_host.main(["--allow-game-writes"])
    assert captured == {
        "source": sys.stdin, "destination": sys.stdout,
        "allow_game_writes": True,
    }

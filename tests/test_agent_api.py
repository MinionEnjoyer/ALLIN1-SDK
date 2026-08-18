import io
import json
import sys

from allin1_sdk.agent_api import (
    command_catalog,
    command_risk,
    execute_request,
    serve_stdio,
)
from allin1_sdk import agent_host


def test_catalog_is_structured_and_classifies_risk():
    catalog = {item["name"]: item for item in command_catalog()}
    assert "agent-api" not in catalog
    assert catalog["validate"]["risk"] == "read_only"
    assert catalog["link"]["risk"] == "authoring_write"
    assert catalog["apply-rpf-plan"]["risk"] == "game_write"
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

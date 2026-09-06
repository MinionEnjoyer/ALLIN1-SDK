"""Real disposable workflows through all three public automation entry points."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk import automation
from allin1_sdk.agent_api import execute_request, command_catalog, _parameter_arguments
from allin1_sdk.cli import main
from allin1_sdk.node_query import query_nodes, node_category
from test_map_contract import map_payload
from test_sdk_tools import _oiv_folder
from test_render_desktop import renderer
from test_runtime_desktop import host
from test_vehicle_identity_desktop import copied

COMMANDS = {"inspect": "inspect-authoring-workspace", "review": "review-authoring-action", "apply": "apply-authoring-action"}


@pytest.fixture(params=["python", "cli", "agent"])
def transport(request, tmp_path):
    def call(operation, payload, *, acknowledge=True):
        if request.param == "python":
            if operation == "apply" and acknowledge:
                payload = {**payload, "authoring_confirmed": True}
            return getattr(automation, operation + "_authoring")(payload)
        if request.param == "cli":
            arguments = [COMMANDS[operation], "--request-json", json.dumps(payload)]
            if operation == "apply" and acknowledge:
                arguments.append("--acknowledge-authoring")
            result = CliRunner().invoke(main, arguments)
            if result.exit_code:
                raise ValueError(result.output)
            return json.loads(result.output)
        parameters = {"request_json": json.dumps(payload)}
        if operation == "apply":
            parameters["acknowledge_authoring"] = acknowledge
        response = execute_request({"id": "test", "action": "execute", "command": COMMANDS[operation], "parameters": parameters}, audit_path=tmp_path / "audit.jsonl")
        if not response["ok"]:
            raise ValueError(response.get("error") or response["result"]["output"])
        assert response["result"]["data_available"] and not response["result"]["output_truncated"]
        return response["result"]["data"]
    return call


def commit(call, request):
    review = call("review", request)
    assert review["review_only"] and not review["game_write_performed"]
    result = call("apply", {**request, "review_sha256": review["review_sha256"]})
    assert not result["game_write_performed"]
    return result


def test_graph_and_build_flow_complete_happy_path(transport, tmp_path):
    source = tmp_path / "Source tree with spaces"; source.mkdir()
    (source / "car.yft").write_bytes(b"fixture bytes, no native decode requested")
    (source / "car.ytd").write_bytes(b"texture fixture")
    initial = transport("inspect", {"module": "graph", "source": str(source)})
    graph = tmp_path / "package graph.json"
    session = commit(transport, {"module": "graph", "action": "create", "document": initial["document"], "destination": str(graph)})["session"]
    document = session["document"]
    model = next(n for n in document["nodes"] if n.get("name") == "car.yft")
    model.update(name="renamed.yft", x=900, y=300)
    session = commit(transport, {"module": "graph", "workspace": str(graph), "action": "save", "document": document, "expected_state_sha256": session["state_sha256"]})["session"]
    match = query_nodes(graph, query="renamed.yft")["nodes"][0]
    assert match["category"] == "model" and match["ancestor_ids"] == ["root"]
    output = tmp_path / "loose output"
    commit(transport, {"module": "graph", "workspace": str(graph), "action": "materialize", "destination": str(output), "expected_state_sha256": session["state_sha256"]})
    assert (output / "renamed.yft").read_bytes() == (source / "car.yft").read_bytes()
    initial = transport("inspect", {"module": "program", "graph": str(graph), "template": "loose-export"})
    document = initial["document"]
    program_output = tmp_path / "flow output"
    next(n for n in document["nodes"] if n["id"] == "materialize")["config"] = {"output": str(program_output)}
    program = tmp_path / "program.json"
    session = commit(transport, {"module": "program", "action": "create", "document": document, "destination": str(program)})["session"]
    context = {"module": "program", "workspace": str(program), "expected_state_sha256": session["state_sha256"]}
    plan = tmp_path / "plan.json"
    commit(transport, {**context, "action": "plan", "destination": str(plan)})
    assert json.loads(plan.read_text())["status"] == "ready" and not program_output.exists()
    receipt = tmp_path / "run.json"
    result = commit(transport, {**context, "action": "run", "destination": str(receipt)})
    assert result["execution"]["status"] == "verified"
    assert json.loads(receipt.read_text())["safety"]["stock_game_files_modified"] is False
    assert (program_output / "renamed.yft").read_bytes() == (source / "car.yft").read_bytes()
    assert query_nodes(program, module="program", query="materialize")["nodes"][0]["id"] == "materialize"


@pytest.mark.parametrize("language,before,after", [("xml", "<root/>", "<root><test/></root>"), ("lua", "return 1", "return 2")])
def test_code_save_recovery_and_copy(transport, tmp_path, language, before, after):
    source = tmp_path / ("source." + language); source.write_text(before)
    context = {"module": "code", "source": str(source)}
    session = transport("inspect", context)
    result = commit(transport, {**context, "action": "save", "document": {"language": language, "chunks": [after]}, "expected_state_sha256": session["state_sha256"]})
    assert source.read_text() == after and Path(result["backup"]).read_text() == before
    output = tmp_path / ("copy." + language)
    commit(transport, {**context, "action": "save_copy", "document": {"language": language, "chunks": [after]}, "expected_state_sha256": result["session"]["state_sha256"], "destination": str(output)})
    assert output.read_text() == after


def test_binary_create_patch_undo_and_verified_export(transport, tmp_path):
    source = tmp_path / "source.bin"; source.write_bytes(b"\x01\x02\x03")
    context = {"module": "binary", "source": str(source)}
    initial = transport("inspect", context)
    root = tmp_path / "editable"
    session = commit(transport, {**context, "action": "create", "expected_state_sha256": initial["state_sha256"], "destination": str(root)})["session"]
    context = {"module": "binary", "workspace": str(root)}
    patch = {**context, "action": "patch", "offset": 1, "expected_hex": "02", "replacement_hex": "ff"}
    session = commit(transport, {**patch, "expected_state_sha256": session["state_sha256"]})["session"]
    session = commit(transport, {**context, "action": "undo", "expected_state_sha256": session["state_sha256"]})["session"]
    session = commit(transport, {**patch, "expected_state_sha256": session["state_sha256"]})["session"]
    output = tmp_path / "output.bin"
    commit(transport, {**context, "action": "build", "expected_state_sha256": session["state_sha256"], "destination": str(output)})
    assert output.read_bytes() == b"\x01\xff\x03" and source.read_bytes() == b"\x01\x02\x03"


def test_map_create_save_reopen(transport, tmp_path):
    descriptor = tmp_path / "maps.json"
    session = commit(transport, {"module": "maps", "action": "create", "destination": str(descriptor), "document": map_payload()})["session"]
    document = session["document"]; document["name"] = "Agent authored map"
    context = {"module": "maps", "descriptor": str(descriptor)}
    commit(transport, {**context, "action": "save", "document": document, "expected_state_sha256": session["state_sha256"]})
    assert transport("inspect", context)["document"]["name"] == "Agent authored map"


def test_recipe_conversion_and_data_export(transport, tmp_path):
    source = _oiv_folder(tmp_path)
    context = {"module": "recipe", "source": str(source)}
    session = transport("inspect", context)
    result = commit(transport, {**context, "action": "managed", "destination": str(tmp_path / "managed"), "expected_state_sha256": session["state_sha256"]})
    assert result["file_count"] == 3 and not result["archive_write_performed"]
    metadata = tmp_path / "sample.meta"; metadata.write_text("<root/>")
    context = {"module": "data_tools", "task": "meta_roundtrip", "source": str(metadata)}
    session = transport("inspect", context)
    result = commit(transport, {**context, "action": "export", "destination": str(tmp_path / "reports"), "expected_state_sha256": session["state_sha256"]})
    assert result["outputs"] and metadata.read_text() == "<root/>"


def test_approvals_stale_reviews_and_game_boundaries(transport, tmp_path):
    source = tmp_path / "source.xml"; source.write_text("<root/>")
    context = {"module": "code", "source": str(source)}
    session = transport("inspect", context)
    body = {**context, "action": "save", "document": {"language": "xml", "chunks": ["<changed/>"]}, "expected_state_sha256": session["state_sha256"]}
    review = transport("review", body)
    pending = {**body, "review_sha256": review["review_sha256"]}
    with pytest.raises(ValueError, match="acknowledge|confirmation"):
        transport("apply", pending, acknowledge=False)
    with pytest.raises(ValueError, match="Review changed"):
        transport("apply", {**pending, "document": {"language": "xml", "chunks": ["<different/>"]}})
    source.write_text("<external/>")
    with pytest.raises(ValueError, match="changed"):
        transport("apply", pending)
    assert source.read_text() == "<external/>"
    game = tmp_path / "GTA"; game.mkdir(); (game / "GTA5.exe").write_bytes(b"fixture")
    session = transport("inspect", context)
    with pytest.raises(ValueError, match="outside GTA"):
        transport("review", {**body, "action": "save_copy", "expected_state_sha256": session["state_sha256"], "destination": str(game / "output.xml")})


def test_catalog_matches_real_modules_and_commands():
    catalog = automation.authoring_catalog()
    assert {module["module"] for module in catalog["modules"]} == automation.workspace.MODULES
    modules = {module["module"]: module for module in catalog["modules"]}
    assert modules["optimization"]["actions"] == ["export", "recover"]
    assert "export_validation" in modules["native"]["actions"]
    for module in modules.values():
        assert set(module["input_choices"]) <= automation.workspace._INSPECT_FIELDS
    commands = {item["name"]: item for item in command_catalog()}
    for operation in catalog["operations"].values():
        assert commands[operation["cli"]]["risk"] == operation["risk"]
    assert {item["name"] for item in commands["assistant"]["subcommands"]} == {"context", "prompt", "review", "status", "stop"}
    root = Path(__file__).resolve().parents[1]
    desktop = json.loads((root / "desktop/module-happy-paths.json").read_text())
    assert {route["workbench"] for route in catalog["workbench_routes"]} == {item["module"] for item in desktop["modules"]}
    for route in catalog["workbench_routes"]:
        for name in route.get("commands", []):
            assert name in commands
        if "module" in route:
            assert route["module"] in automation.WORKFLOWS


@pytest.mark.parametrize("value", ['[]', '{"module":"graph","module":"code"}', '{"module":"graph","document":NaN}', '{}'])
def test_invalid_payload_has_no_writes(tmp_path, value):
    request = tmp_path / "request.json"; request.write_text(value)
    result = CliRunner().invoke(main, ["review-authoring-action", "--request-file", str(request)])
    assert result.exit_code != 0
    assert list(tmp_path.iterdir()) == [request]


def test_request_file_and_named_agent_parameters(tmp_path):
    source = tmp_path / "source.xml"; source.write_text("<root/>")
    request = tmp_path / "request with spaces.json"
    request.write_text(json.dumps({"module": "code", "source": str(source)}))
    result = execute_request({"action": "execute", "command": "inspect-authoring-workspace", "parameters": {"request_file": str(request)}}, audit_path=tmp_path / "audit.jsonl")
    assert result["ok"] and result["result"]["data"]["validation"]["valid"]
    for parameters in [{"missing": 1}, {"request_json": []}, {"request_file": "\0"}]:
        assert not execute_request({"action": "execute", "command": "inspect-authoring-workspace", "parameters": parameters})["ok"]
    assert not execute_request({"action": "execute", "command": "authoring-catalog", "args": [], "parameters": {}})["ok"]


def test_parameter_encoder_preserves_types_and_option_like_data():
    import click
    command = click.Command("sample", params=[click.Argument(["source"]), click.Option(["--label"]), click.Option(["--active/--inactive"], default=True), click.Option(["--field"], multiple=True), click.Option(["--pair"], nargs=2)])
    args = _parameter_arguments(command, {"source": "--not-an-option", "label": "--also-data", "active": False, "field": ["a", "b"], "pair": [2, 3]})
    parsed = command.make_context("sample", args).params
    assert parsed == {"source": "--not-an-option", "label": "--also-data", "active": False, "field": ("a", "b"), "pair": ("2", "3")}
    with pytest.raises(ValueError, match="boolean"):
        _parameter_arguments(command, {"active": "true"})


def test_node_categories_match_frontend_semantics():
    kinds = [("archive", "root.rpf", "archive"), ("directory", "data", "directory"), ("file", "model.yft", "model"), ("file", "texture.ytd", "texture"), ("file", "handling.meta", "metadata"), ("vehicle", "bus", "relationship"), ("build_rpf", "build", "step")]
    for kind, name, category in kinds:
        assert node_category({"id": "one", "type": kind, "name": name}) == category
    assert node_category({"id": "one"}, "findings") == "neutral"


def test_render_transport_retains_receipt_and_explicit_native_test_boundary(transport, renderer, tmp_path):
    # External renderer is a controlled fixture; this is NOT live Blender acceptance.
    frame = transport("inspect", renderer)
    assert frame["game_acceptance"] == "NOT TESTED"
    output = tmp_path / "export.png"
    result = commit(transport, {"module": "render", "action": "export", "render_id": frame["render_id"], "expected_state_sha256": frame["state_sha256"], "destination": str(output)})
    assert output.is_file() and result["output_sha256"] == frame["render_record"]["output_sha256"]


def test_runtime_transport_blocks_failed_preflight(transport, host, monkeypatch, tmp_path):
    from dataclasses import replace
    from allin1_sdk import runtime_desktop
    source, report = host
    monkeypatch.setattr(runtime_desktop.runtime, "inspect_native_axle_toolchain", lambda **kwargs: replace(report, ready=False, problems=("CMake missing",)))
    session = transport("inspect", {"module": "runtime"})
    with pytest.raises(ValueError, match="CMake|ready|preflight|toolchain"):
        transport("review", {"module": "runtime", "action": "build", "targets": ["story-legacy", "story-enhanced"], "destination": str(tmp_path / "build"), "expected_state_sha256": session["state_sha256"]})
    assert not (tmp_path / "build").exists()


def test_vehicle_identity_transport_preserves_original_pack(transport, copied):
    workspace, source = copied
    before = automation.workspace._inventory(source)
    context = {"module": "vehicle_identity", "workspace": str(workspace.root), "model": "authorcar"}
    session = transport("inspect", context)
    result = commit(transport, {**context, "action": "migrate", "new_model": "autocar", "new_handling": "AUTOHAND", "expected_revision": session["revision"], "expected_state_sha256": session["state_sha256"]})
    assert result["vehicle_session"]["selected_model"] == "autocar"
    assert automation.workspace._inventory(source) == before


def test_query_pagination_color_filter_and_cli_json(tmp_path):
    from allin1_sdk.rpf_graph import RpfPackageGraph
    source = tmp_path / "source"; source.mkdir()
    for name in ["car2.yft", "car10.yft", "car.ytd"]:
        (source / name).write_bytes(b"fixture")
    graph = RpfPackageGraph.create_from_folder(source, tmp_path / "graph.json")
    before = graph.read_bytes()
    first = query_nodes(graph, category="model", limit=1)
    assert first["nodes"][0]["name"] == "car2.yft" and first["next_offset"] == 1
    second = query_nodes(graph, category="model", offset=1, limit=1)
    assert second["nodes"][0]["name"] == "car10.yft" and second["next_offset"] is None
    response = execute_request({"action": "execute", "command": "query-node-graph", "parameters": {"source": str(graph), "query": "car.ytd", "sort": "color"}}, audit_path=tmp_path / "audit.jsonl")
    assert response["ok"] and response["result"]["data"]["nodes"][0]["category"] == "texture"
    assert graph.read_bytes() == before
    assert not query_nodes(graph, query="missing")["nodes"]


def test_program_cli_deep_link_validates_before_launch(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from allin1_sdk import cli
    from allin1_sdk.rpf_graph import RpfPackageGraph
    from allin1_sdk.rpf_program import RpfPackageProgram
    graph = RpfPackageGraph.create_empty("root.rpf", tmp_path / "graph.json")
    program = RpfPackageProgram.create(graph, tmp_path / "flow.json", template="validate")
    calls = []
    monkeypatch.setattr(cli, "_frozen_desktop_executable", lambda path: tmp_path / "SDK shell.exe")
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command, **kwargs: calls.append(command) or SimpleNamespace(pid=123))
    result = CliRunner().invoke(main, ["open-rpf-program", str(program), "--focus-node", "validate"])
    assert result.exit_code == 0, result.output
    assert calls[0] == [str(tmp_path / "SDK shell.exe"), "--rpf-program", str(program.resolve()), "--graph-node", "validate"]
    missing = CliRunner().invoke(main, ["open-rpf-program", str(program), "--focus-node", "missing"])
    assert missing.exit_code != 0 and len(calls) == 1


def test_stdio_agent_process_can_discover_and_execute_without_a_gui(tmp_path):
    import os
    import subprocess
    import sys
    source = tmp_path / "source.xml"; source.write_text("<root/>")
    requests = [{"id": 1, "action": "execute", "command": "authoring-catalog", "parameters": {}},
                {"id": 2, "action": "execute", "command": "inspect-authoring-workspace", "parameters": {"request_json": json.dumps({"module": "code", "source": str(source)})}}]
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    process = subprocess.run([sys.executable, "-m", "allin1_sdk.agent_host"], input="\n".join(json.dumps(r) for r in requests) + "\n", capture_output=True, text=True, timeout=30, env=env, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(responses) == 2 and all(r["ok"] for r in responses)
    assert responses[1]["result"]["data"]["validation"]["valid"]


def test_update_check_is_shared_and_never_installs(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from allin1_sdk import self_update
    from allin1_sdk.desktop_protocol import dispatch_operation
    monkeypatch.setattr(self_update, "fetch_latest_release", lambda: SimpleNamespace(version="0.6.4", name="SDK", page_url="https://example.invalid", archive_name="sdk.zip", archive_size=3))
    response = execute_request({"action": "execute", "command": "check-sdk-update", "parameters": {}}, audit_path=tmp_path / "audit.jsonl")
    assert response["ok"]
    assert response["result"]["data"] == dispatch_operation("check_update", {})[1]


def test_named_report_output_uses_path_sensitive_risk(tmp_path):
    from allin1_sdk.rpf_graph import RpfPackageGraph
    graph = RpfPackageGraph.create_empty("root.rpf", tmp_path / "graph.json")
    game = tmp_path / "GTA"; game.mkdir(); (game / "GTA5.exe").write_bytes(b"fixture")
    report = game / "report.json"
    request = {"action": "execute", "command": "inspect-rpf-graph", "parameters": {"graph": str(graph), "output": str(report)}}
    response = execute_request(request, audit_path=tmp_path / "audit.jsonl")
    assert not response["ok"] and response["risk"] == "game_write" and not report.exists()
    request["parameters"]["output"] = str(tmp_path / "report.json")
    response = execute_request(request, audit_path=tmp_path / "audit.jsonl")
    assert response["ok"] and response["risk"] == "authoring_write"
    assert (tmp_path / "report.json").is_file()


def test_truncated_json_is_never_exposed_as_structured_data(monkeypatch, tmp_path):
    from allin1_sdk import agent_api
    monkeypatch.setattr(agent_api, "MAX_OUTPUT_CHARS", 30)
    result = execute_request({"action": "execute", "command": "authoring-catalog"}, audit_path=tmp_path / "audit.jsonl")
    assert result["ok"] and result["result"]["output_truncated"]
    assert not result["result"]["data_available"] and result["result"]["data"] is None


def test_parallel_agent_callers_do_not_mix_capture_streams(monkeypatch, tmp_path):
    import click
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from allin1_sdk import agent_api

    active = 0
    peak = 0
    guard = threading.Lock()

    @click.group()
    def group():
        pass

    @group.command("capture-fixture")
    @click.argument("label")
    def capture(label):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            click.echo('{"label":', nl=False)
            time.sleep(0.02)
            click.echo(json.dumps(label) + "}")
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(agent_api, "_cli_group", lambda: group)
    monkeypatch.setitem(agent_api.COMMAND_RISKS, "capture-fixture", "read_only")
    def run(index):
        return execute_request({"id": index, "action": "execute", "command": "capture-fixture", "parameters": {"label": str(index)}}, audit_path=tmp_path / f"audit-{index}.jsonl")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(8)))
    assert peak == 1
    for index, result in enumerate(results):
        assert result["ok"] and result["result"]["data"] == {"label": str(index)}

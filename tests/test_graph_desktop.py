import json
import hashlib
import os
from pathlib import Path

import pytest

from allin1_sdk import workspace_desktop as desktop, desktop_protocol as protocol
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_program import RpfPackageProgram


def request(context, action, **extra):
    body = {**context, "action": action, **extra}
    if action != "create":
        body["expected_state_sha256"] = desktop.inspect(context)["state_sha256"]
    review = desktop.review(body)
    return {**body, "review_sha256": review["review_sha256"], "authoring_confirmed": True}


@pytest.fixture
def graph(tmp_path):
    source = tmp_path / "source tree"
    source.mkdir()
    (source / "example.bin").write_bytes(b"binary payload")
    initial = desktop.inspect({"module": "graph", "source": str(source)})
    assert not initial["workspace"]
    target = tmp_path / "graph.json"
    desktop.apply(request({"module": "graph"}, "create", document=initial["document"], destination=str(target)))
    return {"module": "graph", "workspace": str(target)}, source


def test_graph_happy_path_import_edit_validate_and_materialize(graph, tmp_path):
    context, source = graph
    session = desktop.inspect(context)
    document = session["document"]
    document["nodes"][1]["name"] = "renamed.bin"
    document["nodes"][1]["x"] = 460
    body = request(context, "save", document=document)
    assert desktop.inspect(context)["document"]["nodes"][1]["name"] == "example.bin"
    saved = desktop.apply(body)["session"]
    assert saved["document"]["nodes"][1]["name"] == "renamed.bin"
    output = tmp_path / "materialized tree"
    built = desktop.apply(request(context, "materialize", destination=str(output)))
    assert (output / "renamed.bin").read_bytes() == b"binary payload"
    assert (source / "example.bin").read_bytes() == b"binary payload"
    assert not built["game_write_performed"]


def test_graph_source_refresh_is_explicit_and_stale_plans_fail(graph, tmp_path):
    context, source = graph
    destination = tmp_path / "output"
    pending = request(context, "materialize", destination=str(destination))
    (source / "example.bin").write_bytes(b"changed by its author")
    assert desktop.inspect(context)["issues"]
    with pytest.raises(ValueError, match="changed"):
        desktop.apply(pending)
    assert not destination.exists()
    refreshed = desktop.apply(request(context, "refresh"))
    assert not refreshed["session"]["issues"]
    desktop.apply(request(context, "materialize", destination=str(destination)))
    assert (destination / "example.bin").read_bytes() == b"changed by its author"


@pytest.mark.parametrize("damage", ["cycle", "collision", "traversal"])
def test_invalid_graph_drafts_never_touch_saved_document(graph, damage):
    context, _ = graph
    before = Path(context["workspace"]).read_bytes()
    document = desktop.inspect(context)["document"]
    if damage == "cycle":
        document["edges"].append({"parent": document["nodes"][1]["id"], "child": "root"})
    elif damage == "collision":
        document["nodes"].append({**document["nodes"][1], "id": "duplicate"})
        document["edges"].append({"parent": "root", "child": "duplicate"})
    else:
        document["nodes"][1]["name"] = "../outside.bin"
    with pytest.raises(ValueError):
        request(context, "save", document=document)
    assert Path(context["workspace"]).read_bytes() == before


def test_program_happy_path_template_configure_plan_execute_and_receipt(graph, tmp_path):
    graph_context, source = graph
    session = desktop.inspect({"module": "program", "graph": graph_context["workspace"], "template": "loose-export"})
    document = session["document"]
    assert any("missing configuration" in issue for issue in session["issues"])
    output = tmp_path / "program output"
    next(node for node in document["nodes"] if node["id"] == "materialize")["config"] = {"output": str(output)}
    target = tmp_path / "program.json"
    desktop.apply(request({"module": "program"}, "create", document=document, destination=str(target)))
    context = {"module": "program", "workspace": str(target)}
    assert not desktop.inspect(context)["issues"]
    plan = tmp_path / "flow-plan.json"
    desktop.apply(request(context, "plan", destination=str(plan)))
    assert not output.exists()
    assert json.loads(plan.read_text())["status"] == "ready"
    receipt = tmp_path / "flow-execution.json"
    result = desktop.apply(request(context, "run", destination=str(receipt)))
    assert result["execution"]["status"] == "verified"
    assert (output / "example.bin").read_bytes() == (source / "example.bin").read_bytes()
    assert json.loads(receipt.read_text())["safety"]["stock_game_files_modified"] is False
    with pytest.raises(FileExistsError, match="exists"):
        request(context, "run", destination=str(tmp_path / "replay.json"))


def test_program_rejects_outside_canary_temp_links_before_any_output(graph, tmp_path):
    graph_context, _ = graph
    document = desktop.inspect({"module": "program", "graph": graph_context["workspace"], "template": "loose-export"})["document"]
    output = tmp_path / "output"
    next(node for node in document["nodes"] if node["id"] == "materialize")["config"] = {"output": str(output)}
    target = tmp_path / "program.json"
    desktop.apply(request({"module": "program"}, "create", document=document, destination=str(target)))
    canary = tmp_path / "canary"
    canary.write_bytes(b"preserve outside data")
    os.link(canary, tmp_path / ".output.tmp")
    with pytest.raises(ValueError, match="Hard-linked"):
        request({"module": "program", "workspace": str(target)}, "run", destination=str(tmp_path / "run.json"))
    assert not output.exists()
    assert canary.read_bytes() == b"preserve outside data"


def test_graph_and_program_protocol_responses_fit_schema_without_truncation(graph, tmp_path):
    context, _ = graph
    risk, result = protocol.dispatch_operation("inspect_authoring_workspace", context)
    assert risk == "read_only" and result["module"] == "graph"
    risk, program = protocol.dispatch_operation("inspect_authoring_workspace", {"module": "program", "graph": context["workspace"], "template": "verified-build"})
    assert risk == "read_only" and program["node_specs"]["build_rpf"]["required_config"] == ["gta_path", "output"]


def test_program_draft_cannot_hide_traversal_by_normalizing_output_first(graph, tmp_path):
    context, _ = graph
    document = desktop.inspect({"module": "program", "graph": context["workspace"], "template": "loose-export"})["document"]
    next(node for node in document["nodes"] if node["id"] == "materialize")["config"] = {"output": str(tmp_path / "nested" / ".." / "escape")}
    with pytest.raises(ValueError, match="traversal"):
        request({"module": "program"}, "create", document=document, destination=str(tmp_path / "program.json"))
    assert not (tmp_path / "program.json").exists()


def test_reviewed_preview_bundle_preserves_real_failure_counts(graph, tmp_path):
    context, source = graph
    bundle = tmp_path / "previews"
    result = desktop.apply(request(context, "preview_bundle", destination=str(bundle)))
    assert result["preview_summary"]["processed"] == 1
    assert result["preview_summary"]["failed"] == 0
    report = json.loads((bundle / "preview-report.json").read_text())
    assert report["assets"][0]["source_sha256"] == hashlib.sha256((source / "example.bin").read_bytes()).hexdigest()
    assert (bundle / report["assets"][0]["preview"]).is_file()


@pytest.mark.parametrize("archive", [False, True])
def test_package_import_relationship_review_and_exact_reopen(tmp_path, archive):
    import zipfile
    from test_vehicle_authoring import _source
    from allin1_sdk.desktop_protocol import dispatch_operation
    from allin1_sdk.workspace_desktop import _inventory
    source = _source(tmp_path)
    if archive:
        packed = tmp_path / "vehicle.zip"
        with zipfile.ZipFile(packed, "w") as output:
            for item in source.rglob("*"):
                if item.is_file():
                    output.write(item, item.relative_to(source).as_posix())
        source = packed
    destination = tmp_path / "Package relationships"
    request = {"module": "graph", "action": "import_package", "source": str(source), "destination": str(destination)}
    _, review = dispatch_operation("review_workspace_action", request)
    assert not destination.exists()
    assert all(Path(node["source"]).is_relative_to(destination) for node in review["document"]["nodes"] if node.get("source"))
    assert "allin1-package-graph-review-" not in str(review["document"])
    _, applied = dispatch_operation("apply_workspace_action", {**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    session = applied["session"]
    assert session["document"]["origin"]["type"] == "mod_package_import"
    assert [item["name"] for item in session["document"]["semantic"]["entities"]] == ["authorcar"]
    root = Path(session["workspace"]).parent
    before = _inventory(root)
    analysis = {"module": "graph", "action": "analyze", "workspace": session["workspace"], "expected_state_sha256": session["state_sha256"]}
    _, proposal = dispatch_operation("review_workspace_action", analysis)
    assert proposal["summary"]["entities"] == 1
    assert _inventory(root) == before
    _, result = dispatch_operation("apply_workspace_action", {**analysis, "review_sha256": proposal["review_sha256"], "authoring_confirmed": True})
    assert result["session"]["document"]["semantic"]["relations"]
    assert not result["game_write_performed"]


def test_package_relationship_analysis_rejects_changed_retained_tree(tmp_path):
    from test_vehicle_authoring import _source
    from allin1_sdk.package_graph import PackageGraphWorkspace
    from allin1_sdk.workspace_desktop import _inventory
    project = PackageGraphWorkspace(tmp_path / "Imported").import_package(_source(tmp_path))
    context = {"module": "graph", "workspace": str(project.graph)}
    session = desktop.inspect(context)
    request = {**context, "action": "analyze", "expected_state_sha256": session["state_sha256"]}
    review = desktop.review(request)
    (project.workspace / "retained-note.txt").write_text("changed after review")
    before = _inventory(project.workspace)
    with pytest.raises(ValueError, match="Review changed"):
        desktop.apply({**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert _inventory(project.workspace) == before

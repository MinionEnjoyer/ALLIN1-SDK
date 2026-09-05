"""Offline React access to the existing package-graph and build-program domains."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_program import RpfPackageProgram, NODE_SPECS, PROGRAM_TEMPLATES
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.workspace_desktop import path, digest, file_hash, _inventory
from allin1_sdk.release_paths import no_links, relative_path, strict_json
from allin1_sdk.paths import project_root


def _read_document(source):
    selected = path(source, writable=True)
    if not selected.is_file() or selected.stat().st_size > 128 * 1024:
        raise ValueError("Choose a graph/program JSON file within 128 KiB")
    document = strict_json(selected.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Graph/program document must be an object")
    return selected, document


def _graph_inputs(document, *, verify=True):
    if not isinstance(document, dict):
        raise ValueError("Graph document must be an object")
    if isinstance(document.get("origin"), dict):
        path(document["origin"].get("path"))
    nodes = document.get("nodes")
    if not isinstance(nodes, list) or len(nodes) > 400:
        raise ValueError("Desktop graph is limited to 400 nodes per authoring document")
    identities, issues = {}, []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Graph nodes must be objects")
        if node.get("type") not in {"file", "sealed_archive"}:
            continue
        source = path(node.get("source"))
        if not source.is_file():
            raise ValueError("Graph source must be a regular file")
        current = {"size": source.stat().st_size, "sha256": file_hash(source)}
        identities[str(source)] = current
        if current["size"] != node.get("size") or current["sha256"] != node.get("sha256"):
            issues.append(f"{node.get('name')}: source changed; refresh source identities explicitly")
    state = RpfPackageGraph._normalize(document, verify_sources=verify)
    return state, identities, issues


def _context(payload, *, verify=False):
    selected, document = _read_document(payload.get("workspace"))
    if payload["module"] == "graph":
        state, sources, issues = _graph_inputs(document, verify=verify)
    else:
        graph, graph_document = _read_document(document.get("package_graph"))
        _, sources, issues = _graph_inputs(graph_document, verify=verify)
        sources[str(graph)] = {"sha256": file_hash(graph)}
        # Validate lexical paths before domain code can erase junction ancestry.
        _program_paths(document)
        state = RpfPackageProgram._normalize(document, verify_graph=verify)
        issues += list(state["issues"])
    fingerprint = digest({"document": file_hash(selected), "inputs": sources})
    return selected, document, state, sources, issues, fingerprint


def _program_paths(document):
    for node in document.get("nodes", []):
        for key, value in node.get("config", {}).items():
            if key != "label" and value:
                if not isinstance(value, str):
                    raise ValueError("Program paths must be strings")
                authored = Path(value)
                if not authored.is_absolute() or ".." in authored.parts:
                    raise ValueError("Program paths must be absolute without traversal")
                no_links(authored)
                for part in authored.parts[1:]:
                    relative_path(part)


def _archive_inputs(source, game):
    archive = path(source)
    if not archive.is_file() or archive.suffix.casefold() != ".rpf":
        raise ValueError("Choose an RPF archive")
    selected_game = path(game)
    service = RpfExplorerService(project_root(), selected_game)
    before = file_hash(archive)
    index = service.index(archive)
    if index.warnings or len(index.entries) > 399 or sum(entry.size for entry in index.entries) > 512 * 1024**2:
        raise ValueError("Graph intake requires a warning-free archive within 400 nodes and 512 MiB")
    if file_hash(archive) != before:
        raise ValueError("Archive changed during graph intake")
    return archive, service, index, before


def _portable_proposal(value, old_root, new_root):
    if isinstance(value, dict):
        return {key: _portable_proposal(item, old_root, new_root) for key, item in value.items()
                if key not in {"created_utc", "updated_utc", "generated_utc"}}
    if isinstance(value, list):
        return [_portable_proposal(item, old_root, new_root) for item in value]
    if isinstance(value, str) and value.startswith(str(old_root) + chr(92)):
        return str(new_root) + value[len(str(old_root)):]
    if isinstance(value, str) and value.startswith(str(old_root) + "/"):
        return str(new_root) + value[len(str(old_root)):]
    return value


def _package_import_review(payload):
    from allin1_sdk.package_graph import PackageGraphWorkspace
    from allin1_sdk.recipe_desktop import _source_identity
    source = path(payload.get("source"))
    identity = _source_identity(source)
    destination = path(payload.get("destination"), new=True, writable=True)
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Package graph output must be separate from its source")
    with tempfile.TemporaryDirectory(prefix="allin1-package-graph-review-") as temporary:
        temporary_root = Path(temporary)
        project = PackageGraphWorkspace(temporary_root).import_package(source)
        document = strict_json(project.graph.read_bytes())
        _graph_inputs(document)
        document = _portable_proposal(document, temporary_root, destination)
        _bounded_document(document)
        outputs = [str(destination / project.workspace.relative_to(temporary_root))]
    if identity != _source_identity(source):
        raise ValueError("Package changed during graph import review")
    return {"action": "import_package", "source": str(source), "destination": str(destination),
            "state_sha256": identity, "document": document, "outputs": outputs}


def _relationship_review(selected, original):
    from allin1_sdk.package_relations import PackageRelationshipAnalyzer
    # Analysis reads retained siblings as well as graph nodes. Bind and validate
    # that whole owned tree before the resolver can follow any path.
    before = _inventory(selected.parent)
    report = PackageRelationshipAnalyzer.analyze(selected, persist=False)
    report.pop("generated_utc", None)
    proposed = {**original, "semantic": report}
    _bounded_document(proposed)
    _graph_inputs(proposed)
    if before != _inventory(selected.parent):
        raise ValueError("Retained package sources changed during relationship analysis")
    return {"document": proposed, "retained_tree_sha256": digest(before),
            "outputs": [str(selected)], "summary": report["summary"]}


def _bounded_document(document):
    if len(document["nodes"]) > 400 or len(json.dumps(document).encode()) > 128 * 1024:
        raise ValueError("Graph/program exceeds the desktop document limit")


def _import_review(payload):
    archive, service, index, identity = _archive_inputs(payload.get("archive"), payload.get("gta_path"))
    destination = path(payload.get("destination"), new=True, writable=True)
    with tempfile.TemporaryDirectory(prefix="allin1-graph-intake-review-") as temporary:
        copied = Path(temporary) / "imported"
        graph = RpfPackageGraph.import_archive(index, service, copied)
        document = strict_json(graph.read_bytes())
        _graph_inputs(document)
        # The displayed proposal contains final paths, never ephemeral ones.
        for node in document["nodes"]:
            if node.get("source"):
                node["source"] = str(destination / Path(node["source"]).relative_to(copied))
        document.pop("created_utc", None)
        document.pop("updated_utc", None)
        _bounded_document(document)
    if file_hash(archive) != identity:
        raise ValueError("Archive changed while preparing the import review")
    return {"action": "import_archive", "source": str(archive), "destination": str(destination),
            "state_sha256": digest({"archive_sha256": identity, "edition": index.edition}),
            "archive_sha256": identity, "edition": index.edition, "document": document,
            "outputs": [str(destination / "rpf-graph.json"), str(destination / "source"), str(destination / "rpf-graph-import.json")]}


def _expansion_review(payload, selected, state):
    node = state["nodes"].get(payload.get("node_id"))
    if node is None or node["type"] != "sealed_archive":
        raise ValueError("Select a sealed archive node")
    _, service, _, identity = _archive_inputs(node["source"], payload.get("gta_path"))
    expansion = no_links(selected.parent / "expanded-rpfs")
    destination = no_links(expansion / f"{node['id']}.rpf.source")
    if destination.exists():
        raise ValueError("Archive expansion already exists")
    path(str(selected.with_name(f".{selected.name}.tmp")), new=True, writable=True)
    with tempfile.TemporaryDirectory(prefix="allin1-graph-expansion-review-") as temporary:
        graph = Path(temporary) / "graph.json"
        graph.write_bytes(selected.read_bytes())
        report = RpfPackageGraph.expand_sealed_archive(graph, node["id"], service)
        document = strict_json(graph.read_bytes())
        _graph_inputs(document)
        for item in document["nodes"]:
            if item.get("source") and Path(item["source"]).is_relative_to(temporary):
                item["source"] = str(selected.parent / Path(item["source"]).relative_to(temporary))
        document.pop("updated_utc", None)
        _bounded_document(document)
    return {"destination": str(destination), "document": document, "expanded_source_sha256": identity,
            "outputs": [str(selected), str(destination)], "added_nodes": report["added_nodes"]}


def inspect(payload):
    module = payload["module"]
    if module == "graph" and payload.get("source"):
        folder = path(payload["source"])
        inventory = _inventory(folder)
        with tempfile.TemporaryDirectory(prefix="allin1-graph-review-") as temporary:
            graph = RpfPackageGraph.create_from_folder(folder, Path(temporary) / "graph.json", allow_sealed_rpfs=True)
            document = strict_json(graph.read_text(encoding="utf-8"))
        if inventory != _inventory(folder):
            raise ValueError("Graph source folder changed during inspection")
        _graph_inputs(document)
        return {"workspace": None, "source": str(folder), "document": document,
                "state_sha256": digest(inventory), "issues": []}
    if module == "program" and payload.get("graph"):
        graph, document = _read_document(payload["graph"])
        _, sources, _ = _graph_inputs(document)
        with tempfile.TemporaryDirectory(prefix="allin1-program-template-") as temporary:
            program = RpfPackageProgram.create(graph, Path(temporary) / "program.json", template=payload.get("template", "validate"))
            document = strict_json(program.read_text(encoding="utf-8"))
        state = RpfPackageProgram._normalize(document, verify_graph=True)
        return {"workspace": None, "document": document, "state_sha256": digest({"graph": file_hash(graph), "inputs": sources}),
                "issues": list(state["issues"]), "node_specs": {key: json.loads(json.dumps(asdict(value))) for key, value in NODE_SPECS.items()}}
    selected, document, _, _, issues, fingerprint = _context(payload)
    result = {"workspace": str(selected), "document": document, "state_sha256": fingerprint, "issues": issues}
    if module == "program":
        result["node_specs"] = {key: json.loads(json.dumps(asdict(value))) for key, value in NODE_SPECS.items()}
    if payload.get("source_file"):
        source = path(payload["source_file"])
        if not source.is_file() or source.stat().st_size > 512 * 1024**2:
            raise ValueError("Choose a bounded regular graph source file")
        result["source_node"] = {"source": str(source), "name": source.name, "size": source.stat().st_size, "sha256": file_hash(source),
                                 "type": "sealed_archive" if source.suffix.casefold() == ".rpf" else "file"}
    return result


def _new_json(value):
    destination = path(value, new=True, writable=True)
    if destination.suffix.casefold() != ".json":
        raise ValueError("Choose a .json destination")
    return destination


def _safe_outputs(state, source, destination):
    result = []
    for configured in state["configured_outputs"]:
        output = path(str(configured), new=True, writable=True)
        path(str(output.with_name(f".{output.name}.tmp")), new=True, writable=True)
        if output == destination or output.is_relative_to(destination) or destination.is_relative_to(output):
            raise ValueError("Program report overlaps a configured output")
        if source.is_relative_to(output):
            raise ValueError("Program output overlaps its source")
        result.append(str(output))
    return result


def review(payload):
    module, action = payload["module"], payload.get("action")
    if module == "graph" and action == "import_package":
        return _package_import_review(payload)
    if module == "graph" and action == "import_archive":
        return _import_review(payload)
    if action not in {"create", "save", "materialize", "build", "refresh", "plan_origin", "plan", "run", "expand", "preview_bundle", "analyze"}:
        raise ValueError("Unknown graph/program action")
    if action == "create":
        selected = _new_json(payload.get("destination"))
        document = payload.get("document")
        if not isinstance(document, dict):
            raise ValueError("A graph/program document is required")
        fingerprint = None
        sources = {}
    else:
        selected, original, state, sources, issues, fingerprint = _context(payload, verify=action not in {"save", "refresh"})
        if payload.get("expected_state_sha256") != fingerprint:
            raise ValueError("Graph/program input changed; reopen it before review")
        document = payload.get("document", original)
    details = {"action": action, "source": str(selected), "state_sha256": fingerprint}
    if action in {"create", "save"}:
        if module == "graph":
            state, sources, issues = _graph_inputs(document)
        else:
            graph, graph_document = _read_document(document.get("package_graph"))
            _, sources, _ = _graph_inputs(graph_document)
            sources[str(graph)] = {"sha256": file_hash(graph)}
            _program_paths(document)
            state = RpfPackageProgram._normalize(document, verify_graph=True)
            issues = list(state["issues"])
        _bounded_document(document)
        details.update(destination=str(selected), nodes=len(document["nodes"]), issues=issues, document=document)
    elif module == "graph" and action == "refresh":
        changed = []
        for node in document["nodes"]:
            if node.get("source") in sources:
                current = sources[node["source"]]
                if current["sha256"] != node["sha256"] or current["size"] != node["size"]:
                    changed.append({"node": node["id"], "before": node["sha256"], "after": current["sha256"]})
        if not changed:
            raise ValueError("All graph source identities are already current")
        details["changes"] = changed
        # Older shared writer uses a fixed temporary name; fail closed first.
        path(str(selected.with_name(f".{selected.name}.tmp")), new=True, writable=True)
    elif module == "graph" and action == "analyze":
        details.update(_relationship_review(selected, original))
    elif module == "graph" and action == "expand":
        details.update(_expansion_review(payload, selected, state))
    elif module == "graph" and action in {"materialize", "build", "plan_origin", "preview_bundle"}:
        destination = path(payload.get("destination"), new=True, writable=True)
        if action not in {"materialize", "preview_bundle"} or payload.get("gta_path"):
            path(payload.get("gta_path"))
        outputs = [str(destination)]
        if action == "build":
            if destination.suffix.casefold() != ".rpf" or state["sealed_archive_count"]:
                raise ValueError("Build requires a .rpf output and no sealed archive nodes")
            report = RpfArchiveBuilder.validation_path(destination)
            path(str(report), new=True, writable=True)
            path(str(report.with_name(f".{report.name}.tmp")), new=True, writable=True)
            outputs.append(str(report))
        elif action == "plan_origin":
            if document.get("origin", {}).get("type") != "rpf_archive_import":
                raise ValueError("Origin planning requires an imported RPF graph")
            _new_json(str(destination))
            path(str(destination.with_name(f"{destination.stem}.payload")), new=True, writable=True)
        details.update(destination=str(destination), outputs=outputs)
    elif module == "program" and action in {"plan", "run"}:
        ready = RpfPackageProgram._ready_state(selected)
        destination = _new_json(payload.get("destination"))
        outputs = _safe_outputs(ready, selected, destination)
        details.update(destination=str(destination), outputs=outputs, execution_order=list(ready["order"]))
    else:
        raise ValueError("Action is not available for this authoring module")
    details["inputs_sha256"] = digest(sources)
    return details


def _write_document(destination, document, *, new, expected=None):
    content = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if new:
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(content)
        return
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=".graph-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        no_links(destination)
        if file_hash(destination) != expected:
            raise ValueError("Authoring document changed before save")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def apply(payload):
    module, action = payload["module"], payload["action"]
    if module == "graph" and action == "import_package":
        from allin1_sdk.package_graph import PackageGraphWorkspace
        from allin1_sdk.recipe_desktop import _source_identity
        source = path(payload["source"])
        before = _source_identity(source)
        project = PackageGraphWorkspace(payload["destination"]).import_package(source)
        if before != _source_identity(source):
            raise ValueError("Package changed during import; the graph must be revalidated")
        from allin1_sdk.workspace_desktop import inspect as inspect_workspace
        return {"session": inspect_workspace({"module": "graph", "workspace": str(project.graph)})}
    if module == "graph" and action == "import_archive":
        _, service, index, _ = _archive_inputs(payload["archive"], payload["gta_path"])
        selected = RpfPackageGraph.import_archive(index, service, payload["destination"])
        from allin1_sdk.workspace_desktop import inspect as inspect_workspace
        return {"session": inspect_workspace({"module": module, "workspace": str(selected)})}
    selected = Path(payload["destination"] if action == "create" else payload["workspace"])
    if action in {"create", "save"}:
        before = file_hash(selected) if action == "save" else None
        _write_document(selected, payload["document"], new=action == "create", expected=before)
    elif action == "analyze":
        original = strict_json(selected.read_bytes())
        proposed = _relationship_review(selected, original)["document"]
        _write_document(selected, proposed, new=False, expected=file_hash(selected))
    elif action == "refresh":
        RpfPackageGraph.refresh_sources(selected)
    elif module == "graph" and action == "expand":
        service = RpfExplorerService(project_root(), payload["gta_path"])
        RpfPackageGraph.expand_sealed_archive(selected, payload["node_id"], service)
    elif module == "graph":
        output = Path(payload["destination"])
        if action == "materialize":
            RpfPackageGraph.materialize(selected, output)
            return {"output": str(output), "output_sha256": digest(_inventory(output))}
        if action == "preview_bundle":
            from allin1_sdk.rpf_graph_previews import render_graph_preview_bundle
            output, report = render_graph_preview_bundle(selected, output, project_root(), game_path=payload.get("gta_path"))
            evidence = strict_json(report.read_bytes())
            return {"output": str(output), "report": str(report), "output_sha256": digest(_inventory(output)), "preview_summary": evidence["summary"]}
        builder = RpfArchiveBuilder(project_root(), payload["gta_path"])
        if action == "build":
            output, report = RpfPackageGraph.build(selected, builder, output)
        else:
            output, report = RpfPackageGraph.plan_origin_changes(selected, builder, builder.service, output)
        return {"output": str(output), "report": str(report), "output_sha256": file_hash(output)}
    elif action in {"plan", "run"}:
        output, report = (RpfPackageProgram.plan(selected, payload["destination"]) if action == "plan"
                          else RpfPackageProgram.execute(selected, project_root(), payload["destination"]))
        return {"output": str(output), "output_sha256": file_hash(output), "execution": report}
    from allin1_sdk.workspace_desktop import inspect as inspect_workspace
    return {"session": inspect_workspace({"module": module, "workspace": str(selected)})}

"""Validated visual package graphs for provenance-safe RPF authoring."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import stat
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from allin1_sdk.rpf_builder import (
    MAX_RPF_BUILD_BYTES,
    MAX_RPF_BUILD_DEPTH,
    MAX_RPF_BUILD_FILES,
    MAX_RPF_BUILD_FILE_BYTES,
    RpfArchiveBuilder,
)


RPF_GRAPH_SCHEMA = 1
RPF_GRAPH_OPERATION = "rpf_package_graph"
RPF_GRAPH_NODE_TYPES = frozenset({"archive", "directory", "file"})
MAX_RPF_GRAPH_NODES = MAX_RPF_BUILD_FILES * 2
MAX_RPF_GRAPH_COORDINATE = 1_000_000
MAX_RPF_GRAPH_TREE_DEPTH = 256
_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _safe_name(name: object, *, archive: bool = False) -> str:
    if not isinstance(name, str):
        raise ValueError("RPF graph node names must be strings")
    value = name
    if (
        not value or value in {".", ".."} or ":" in value
        or any(character in '<>"/\\|?*' for character in value)
        or any(ord(character) < 32 for character in value)
        or value.rstrip(" .") != value
        or len(value.encode("utf-16-le")) // 2 > 255
        or value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(f"Unsafe or non-materializable RPF graph node name: {value!r}")
    if archive and not value.casefold().endswith(".rpf"):
        raise ValueError(f"RPF graph archive nodes must end in .rpf: {value!r}")
    if not archive and value.casefold().endswith(".rpf.source"):
        raise ValueError("RPF graph nodes use authored .rpf names, not .rpf.source")
    return value


def _safe_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("RPF graph node ids must be strings")
    node_id = value
    if (
        not node_id or len(node_id) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in node_id)
    ):
        raise ValueError(f"Unsafe RPF graph node id: {node_id!r}")
    return node_id


def _number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"RPF graph {label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"RPF graph {label} must be numeric") from exc
    if not math.isfinite(result) or not (
        -MAX_RPF_GRAPH_COORDINATE <= result <= MAX_RPF_GRAPH_COORDINATE
    ):
        raise ValueError(f"RPF graph {label} exceeds the coordinate limit")
    return result


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    temporary.replace(path)


def _stable_node_id(kind: str, logical_path: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\0{logical_path.casefold()}".encode("utf-8")
    ).hexdigest()[:20]
    return f"{kind[0]}_{digest}"


class RpfPackageGraph:
    """Create, mutate, validate, materialize, and build an RPF package graph."""

    @staticmethod
    def _new_payload(root_name: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": RPF_GRAPH_SCHEMA,
            "operation": RPF_GRAPH_OPERATION,
            "created_utc": now,
            "updated_utc": now,
            "root_id": "root",
            "nodes": [{
                "id": "root", "type": "archive",
                "name": _safe_name(root_name, archive=True),
                "x": 80.0, "y": 80.0,
            }],
            "edges": [],
        }

    @staticmethod
    def _publish_new(destination: str | Path, payload: dict[str, Any]) -> Path:
        output = Path(destination).expanduser().resolve()
        if output.suffix.casefold() != ".json":
            raise ValueError("RPF graph output must use a .json extension")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"RPF graph already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        RpfPackageGraph._normalize(payload, verify_sources=True)
        _write_json_atomic(output, payload)
        return output

    @classmethod
    def create_empty(
        cls, root_name: str, destination: str | Path,
    ) -> Path:
        return cls._publish_new(destination, cls._new_payload(root_name))

    @classmethod
    def create_from_folder(
        cls, source_folder: str | Path, destination: str | Path, *,
        root_name: str = "",
    ) -> Path:
        source = Path(source_folder).expanduser().resolve()
        if not source.is_dir() or _is_link(source):
            raise ValueError(f"RPF graph source must be a real directory: {source}")
        if not root_name:
            if source.name.casefold().endswith(".rpf.source"):
                root_name = source.name[:-len(".source")]
            else:
                root_name = f"{source.name}.rpf"
        payload = cls._new_payload(root_name)
        nodes: list[dict[str, Any]] = payload["nodes"]
        edges: list[dict[str, str]] = payload["edges"]
        row_by_depth: Counter[int] = Counter()

        def scan(folder: Path, parent_id: str, logical: str, depth: int) -> None:
            if depth > MAX_RPF_GRAPH_TREE_DEPTH:
                raise ValueError("RPF graph source exceeds the directory depth limit")
            if _is_link(folder):
                raise ValueError(f"RPF graph source cannot contain links: {folder}")
            emitted: set[str] = set()
            for child in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
                if len(nodes) >= MAX_RPF_GRAPH_NODES:
                    raise ValueError("RPF graph source exceeds the guarded node limit")
                if _is_link(child):
                    raise ValueError(f"RPF graph source cannot contain links: {child}")
                nested = child.is_dir() and child.name.casefold().endswith(".rpf.source")
                authored_name = child.name[:-len(".source")] if nested else child.name
                kind = "archive" if nested else "directory" if child.is_dir() else "file"
                authored_name = _safe_name(authored_name, archive=kind == "archive")
                if authored_name.casefold() in emitted:
                    raise ValueError(
                        f"RPF graph source has a case-insensitive sibling collision: {child}"
                    )
                emitted.add(authored_name.casefold())
                child_logical = f"{logical}/{authored_name}" if logical else authored_name
                node_id = _stable_node_id(kind, child_logical)
                row = row_by_depth[depth]
                row_by_depth[depth] += 1
                node: dict[str, Any] = {
                    "id": node_id, "type": kind, "name": authored_name,
                    "x": 80.0 + depth * 300.0, "y": 80.0 + row * 112.0,
                }
                if kind == "file":
                    if child.suffix.casefold() == ".rpf":
                        raise ValueError(
                            f"Prebuilt RPF files cannot be graph source nodes: {child}"
                        )
                    size = child.stat().st_size
                    if size > MAX_RPF_BUILD_FILE_BYTES:
                        raise ValueError(f"RPF graph source file is too large: {child}")
                    node.update({
                        "source": str(child), "size": size,
                        "sha256": _sha256_file(child),
                    })
                nodes.append(node)
                edges.append({"parent": parent_id, "child": node_id})
                if kind != "file":
                    scan(child, node_id, child_logical, depth + 1)

        scan(source, "root", "", 1)
        return cls._publish_new(destination, payload)

    @staticmethod
    def _read(path: str | Path) -> tuple[Path, dict[str, Any]]:
        source = Path(path).expanduser().resolve()
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError(f"RPF graph not found: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid RPF graph JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("RPF graph must be a JSON object")
        return source, payload

    @classmethod
    def _normalize(
        cls, payload: dict[str, Any], *, verify_sources: bool,
    ) -> dict[str, Any]:
        if (
            payload.get("schema_version") != RPF_GRAPH_SCHEMA
            or payload.get("operation") != RPF_GRAPH_OPERATION
        ):
            raise ValueError("Unsupported RPF package graph schema")
        authored_nodes = payload.get("nodes")
        authored_edges = payload.get("edges")
        if (
            not isinstance(authored_nodes, list) or not isinstance(authored_edges, list)
            or not 1 <= len(authored_nodes) <= MAX_RPF_GRAPH_NODES
            or len(authored_edges) > MAX_RPF_GRAPH_NODES - 1
        ):
            raise ValueError("RPF graph has an invalid or oversized node/edge collection")
        nodes: dict[str, dict[str, Any]] = {}
        folded_ids: set[str] = set()
        file_count = 0
        byte_count = 0
        for authored in authored_nodes:
            if not isinstance(authored, dict):
                raise ValueError("Every RPF graph node must be an object")
            node_id = _safe_id(authored.get("id"))
            if node_id.casefold() in folded_ids:
                raise ValueError(f"Duplicate RPF graph node id: {node_id}")
            folded_ids.add(node_id.casefold())
            kind = str(authored.get("type", "")).casefold()
            if kind not in RPF_GRAPH_NODE_TYPES:
                raise ValueError(f"Unsupported RPF graph node type: {kind}")
            name = _safe_name(authored.get("name"), archive=kind == "archive")
            node: dict[str, Any] = {
                "id": node_id, "type": kind, "name": name,
                "x": _number(authored.get("x", 0), "x"),
                "y": _number(authored.get("y", 0), "y"),
            }
            if kind == "file":
                source_value = authored.get("source")
                if not isinstance(source_value, str) or not Path(source_value).is_absolute():
                    raise ValueError(f"RPF graph file source must be absolute: {name}")
                source = Path(source_value).resolve()
                size = authored.get("size")
                if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_RPF_BUILD_FILE_BYTES:
                    raise ValueError(f"RPF graph file size is invalid: {name}")
                digest = authored.get("sha256")
                if not _is_sha256(digest):
                    raise ValueError(f"RPF graph file hash is invalid: {name}")
                if verify_sources:
                    if not source.is_file() or _is_link(source):
                        raise ValueError(f"RPF graph source file is unavailable: {source}")
                    if source.stat().st_size != size or _sha256_file(source) != str(digest).casefold():
                        raise ValueError(
                            f"RPF graph source changed; refresh it explicitly: {source}"
                        )
                node.update({"source": str(source), "size": size, "sha256": str(digest).casefold()})
                file_count += 1
                byte_count += size
            nodes[node_id] = node
        root_id = _safe_id(payload.get("root_id"))
        if root_id not in nodes or nodes[root_id]["type"] != "archive":
            raise ValueError("RPF graph root must reference an archive node")
        if file_count > MAX_RPF_BUILD_FILES or byte_count > MAX_RPF_BUILD_BYTES:
            raise ValueError("RPF graph source payload exceeds the guarded build limits")

        parents: dict[str, str] = {}
        children: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        seen_edges: set[tuple[str, str]] = set()
        for authored in authored_edges:
            if not isinstance(authored, dict):
                raise ValueError("Every RPF graph edge must be an object")
            parent = _safe_id(authored.get("parent"))
            child = _safe_id(authored.get("child"))
            if parent not in nodes or child not in nodes or parent == child:
                raise ValueError("RPF graph edge references an invalid node")
            if nodes[parent]["type"] == "file":
                raise ValueError("RPF graph files cannot contain child nodes")
            edge = (parent.casefold(), child.casefold())
            if edge in seen_edges or child in parents:
                raise ValueError("RPF graph nodes must have exactly one unique parent")
            seen_edges.add(edge)
            parents[child] = parent
            children[parent].append(child)
        if root_id in parents or len(parents) != len(nodes) - 1:
            raise ValueError("RPF graph must be one rooted containment tree")

        ancestry_complete: set[str] = set()
        for node_id in nodes:
            if node_id in ancestry_complete:
                continue
            chain: list[str] = []
            local: set[str] = set()
            current = node_id
            while current not in ancestry_complete:
                if current in local:
                    raise ValueError("RPF graph contains a cycle")
                local.add(current)
                chain.append(current)
                if current not in parents:
                    break
                current = parents[current]
            ancestry_complete.update(chain)

        visited: set[str] = set()

        def walk(node_id: str, archive_depth: int, tree_depth: int = 0) -> None:
            if tree_depth > MAX_RPF_GRAPH_TREE_DEPTH:
                raise ValueError("RPF graph exceeds the directory depth limit")
            if node_id in visited:
                raise ValueError("RPF graph contains a cycle")
            visited.add(node_id)
            if nodes[node_id]["type"] == "archive" and node_id != root_id:
                archive_depth += 1
                if archive_depth > MAX_RPF_BUILD_DEPTH:
                    raise ValueError("RPF graph exceeds the nested archive depth limit")
            folded: set[str] = set()
            for child in children[node_id]:
                name = nodes[child]["name"].casefold()
                if name in folded:
                    raise ValueError(
                        f"RPF graph has a case-insensitive sibling collision under {node_id}"
                    )
                folded.add(name)
                walk(child, archive_depth, tree_depth + 1)

        walk(root_id, 0)
        if visited != set(nodes):
            raise ValueError("RPF graph contains unreachable nodes")
        return {
            "payload": payload, "root_id": root_id, "nodes": nodes,
            "parents": parents, "children": children,
            "file_count": file_count, "byte_count": byte_count,
            "archive_count": sum(1 for node in nodes.values() if node["type"] == "archive"),
            "directory_count": sum(1 for node in nodes.values() if node["type"] == "directory"),
        }

    @classmethod
    def validate(
        cls, path: str | Path, *, verify_sources: bool = True,
    ) -> dict[str, Any]:
        graph, payload = cls._read(path)
        state = cls._normalize(payload, verify_sources=verify_sources)
        state.update({"graph": graph, "graph_sha256": _sha256_file(graph)})
        return state

    @classmethod
    def describe(cls, path: str | Path) -> dict[str, Any]:
        state = cls.validate(path, verify_sources=True)
        return {
            "schema_version": RPF_GRAPH_SCHEMA,
            "operation": "rpf_package_graph_inspection",
            "status": "valid",
            "graph": str(state["graph"]),
            "graph_sha256": state["graph_sha256"],
            "root": state["nodes"][state["root_id"]],
            "summary": {
                "nodes": len(state["nodes"]), "edges": len(state["parents"]),
                "archives": state["archive_count"],
                "directories": state["directory_count"],
                "files": state["file_count"], "source_bytes": state["byte_count"],
            },
            "nodes": list(state["nodes"].values()),
            "edges": list(state["payload"]["edges"]),
        }

    @classmethod
    def _mutate(
        cls, path: str | Path, update: Callable[[dict[str, Any]], Any],
    ) -> Any:
        graph, payload = cls._read(path)
        cls._normalize(payload, verify_sources=False)
        result = update(payload)
        payload["updated_utc"] = datetime.now(timezone.utc).isoformat()
        cls._normalize(payload, verify_sources=False)
        _write_json_atomic(graph, payload)
        return result

    @classmethod
    def add_container(
        cls, path: str | Path, parent_id: str, name: str, *,
        archive: bool = False, x: float = 0, y: float = 0,
    ) -> str:
        kind = "archive" if archive else "directory"
        safe_name = _safe_name(name, archive=archive)
        new_id = f"{kind[0]}_{uuid.uuid4().hex[:20]}"

        def update(payload: dict[str, Any]) -> str:
            nodes = {item["id"]: item for item in payload["nodes"]}
            parent = _safe_id(parent_id)
            if parent not in nodes or nodes[parent]["type"] == "file":
                raise ValueError(f"RPF graph parent cannot contain nodes: {parent}")
            payload["nodes"].append({
                "id": new_id, "type": kind, "name": safe_name,
                "x": float(x), "y": float(y),
            })
            payload["edges"].append({"parent": parent, "child": new_id})
            return new_id

        return cls._mutate(path, update)

    @classmethod
    def add_file(
        cls, path: str | Path, parent_id: str, source_file: str | Path, *,
        name: str = "", x: float = 0, y: float = 0,
    ) -> str:
        source = Path(source_file).expanduser().resolve()
        graph = Path(path).expanduser().resolve()
        if source == graph:
            raise ValueError("An RPF graph cannot include its own JSON as a source file")
        if not source.is_file() or _is_link(source):
            raise ValueError(f"RPF graph file source must be a real file: {source}")
        if source.suffix.casefold() == ".rpf":
            raise ValueError("Prebuilt RPF files cannot be graph source nodes")
        size = source.stat().st_size
        if size > MAX_RPF_BUILD_FILE_BYTES:
            raise ValueError(f"RPF graph source file is too large: {source}")
        authored_name = _safe_name(name or source.name)
        new_id = f"f_{uuid.uuid4().hex[:20]}"
        digest = _sha256_file(source)

        def update(payload: dict[str, Any]) -> str:
            nodes = {item["id"]: item for item in payload["nodes"]}
            parent = _safe_id(parent_id)
            if parent not in nodes or nodes[parent]["type"] == "file":
                raise ValueError(f"RPF graph parent cannot contain nodes: {parent}")
            payload["nodes"].append({
                "id": new_id, "type": "file", "name": authored_name,
                "source": str(source), "size": size, "sha256": digest,
                "x": float(x), "y": float(y),
            })
            payload["edges"].append({"parent": parent, "child": new_id})
            return new_id

        return cls._mutate(path, update)

    @classmethod
    def rename_node(cls, path: str | Path, node_id: str, name: str) -> None:
        wanted = _safe_id(node_id)

        def update(payload: dict[str, Any]) -> None:
            nodes = {item["id"]: item for item in payload["nodes"]}
            if wanted not in nodes:
                raise ValueError(f"RPF graph node was not found: {wanted}")
            nodes[wanted]["name"] = _safe_name(
                name, archive=nodes[wanted]["type"] == "archive",
            )

        cls._mutate(path, update)

    @classmethod
    def reparent_node(
        cls, path: str | Path, node_id: str, parent_id: str,
    ) -> None:
        wanted = _safe_id(node_id)
        parent = _safe_id(parent_id)

        def update(payload: dict[str, Any]) -> None:
            if wanted == payload["root_id"]:
                raise ValueError("The RPF graph root cannot be reparented")
            nodes = {item["id"]: item for item in payload["nodes"]}
            if wanted not in nodes or parent not in nodes or nodes[parent]["type"] == "file":
                raise ValueError("RPF graph reparent nodes are invalid")
            edge = next(
                (item for item in payload["edges"] if item["child"] == wanted), None,
            )
            if edge is None:
                raise ValueError("RPF graph node has no current parent")
            edge["parent"] = parent

        cls._mutate(path, update)

    @classmethod
    def remove_node(cls, path: str | Path, node_id: str) -> tuple[str, ...]:
        wanted = _safe_id(node_id)

        def update(payload: dict[str, Any]) -> tuple[str, ...]:
            if wanted == payload["root_id"]:
                raise ValueError("The RPF graph root cannot be removed")
            nodes = {item["id"]: item for item in payload["nodes"]}
            if wanted not in nodes:
                raise ValueError(f"RPF graph node was not found: {wanted}")
            children: dict[str, list[str]] = {node: [] for node in nodes}
            for edge in payload["edges"]:
                children[edge["parent"]].append(edge["child"])
            removed: list[str] = []

            def collect(current: str) -> None:
                removed.append(current)
                for child in children[current]:
                    collect(child)

            collect(wanted)
            removed_set = set(removed)
            payload["nodes"][:] = [
                item for item in payload["nodes"] if item["id"] not in removed_set
            ]
            payload["edges"][:] = [
                item for item in payload["edges"]
                if item["parent"] not in removed_set and item["child"] not in removed_set
            ]
            return tuple(removed)

        return cls._mutate(path, update)

    @classmethod
    def set_position(
        cls, path: str | Path, node_id: str, x: float, y: float,
    ) -> None:
        wanted = _safe_id(node_id)
        safe_x = _number(x, "x")
        safe_y = _number(y, "y")

        def update(payload: dict[str, Any]) -> None:
            node = next((item for item in payload["nodes"] if item["id"] == wanted), None)
            if node is None:
                raise ValueError(f"RPF graph node was not found: {wanted}")
            node["x"], node["y"] = safe_x, safe_y

        cls._mutate(path, update)

    @classmethod
    def auto_layout(
        cls, path: str | Path, *, x_spacing: float = 300, y_spacing: float = 112,
    ) -> int:
        safe_x_spacing = _number(x_spacing, "x spacing")
        safe_y_spacing = _number(y_spacing, "y spacing")
        if safe_x_spacing < 120 or safe_y_spacing < 60:
            raise ValueError("RPF graph layout spacing is too small for readable nodes")

        def update(payload: dict[str, Any]) -> int:
            nodes = {item["id"]: item for item in payload["nodes"]}
            children: dict[str, list[str]] = {node_id: [] for node_id in nodes}
            for edge in payload["edges"]:
                children[edge["parent"]].append(edge["child"])
            row = 0

            def place(node_id: str, depth: int) -> None:
                nonlocal row
                nodes[node_id]["x"] = 80.0 + depth * safe_x_spacing
                nodes[node_id]["y"] = 80.0 + row * safe_y_spacing
                row += 1
                for child in sorted(
                    children[node_id], key=lambda value: nodes[value]["name"].casefold(),
                ):
                    place(child, depth + 1)

            place(payload["root_id"], 0)
            return row

        return cls._mutate(path, update)

    @classmethod
    def refresh_sources(cls, path: str | Path) -> int:
        def update(payload: dict[str, Any]) -> int:
            changed = 0
            for node in payload["nodes"]:
                if node["type"] != "file":
                    continue
                source = Path(node["source"]).resolve()
                if not source.is_file() or _is_link(source):
                    raise ValueError(f"RPF graph source file is unavailable: {source}")
                size, digest = source.stat().st_size, _sha256_file(source)
                if size > MAX_RPF_BUILD_FILE_BYTES:
                    raise ValueError(f"RPF graph source file is too large: {source}")
                if size != node["size"] or digest != node["sha256"]:
                    node["size"], node["sha256"] = size, digest
                    changed += 1
            return changed

        return cls._mutate(path, update)

    @classmethod
    def materialize(cls, path: str | Path, destination: str | Path) -> Path:
        state = cls.validate(path, verify_sources=True)
        output = Path(destination).expanduser().resolve()
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"RPF graph materialization output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(
            prefix=f".{output.name}.rpf-graph-", dir=output.parent,
        )).resolve()
        try:
            def write_children(parent_id: str, folder: Path) -> None:
                for child_id in state["children"][parent_id]:
                    node = state["nodes"][child_id]
                    kind = node["type"]
                    authored_name = (
                        f"{node['name']}.source" if kind == "archive" else node["name"]
                    )
                    target = folder / authored_name
                    if kind == "file":
                        shutil.copyfile(node["source"], target)
                        if _sha256_file(target) != node["sha256"]:
                            raise RuntimeError(f"RPF graph source changed while copying: {node['source']}")
                    else:
                        target.mkdir()
                        write_children(child_id, target)

            write_children(state["root_id"], stage)
            if _sha256_file(state["graph"]) != state["graph_sha256"]:
                raise RuntimeError("RPF graph changed during materialization")
            cls.validate(path, verify_sources=True)
            stage.rename(output)
            return output
        except Exception:
            if stage.is_dir() and stage.parent == output.parent:
                shutil.rmtree(stage)
            raise

    @classmethod
    def build(
        cls, path: str | Path, builder: RpfArchiveBuilder, output_rpf: str | Path,
    ) -> tuple[Path, Path]:
        state = cls.validate(path, verify_sources=True)
        output = Path(output_rpf).expanduser().resolve()
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-graph-build-") as temporary:
            source = cls.materialize(path, Path(temporary) / "source")
            archive, report_path = builder.build(source, output)
        try:
            after = cls.validate(path, verify_sources=True)
            if after["graph_sha256"] != state["graph_sha256"]:
                raise RuntimeError("RPF graph changed during archive creation")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["source"] = str(state["graph"])
            report["source_kind"] = "rpf_package_graph"
            report["materialized_source_ephemeral"] = True
            report["graph"] = {
                "path": str(state["graph"]),
                "sha256": state["graph_sha256"],
                "root_node": state["root_id"],
                "nodes": len(state["nodes"]),
            }
            _write_json_atomic(report_path, report)
            return archive, report_path
        except Exception:
            archive.unlink(missing_ok=True)
            report_path.unlink(missing_ok=True)
            raise

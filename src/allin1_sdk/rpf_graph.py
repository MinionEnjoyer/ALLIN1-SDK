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
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from allin1_sdk.rpf_builder import (
    MAX_RPF_BUILD_BYTES,
    MAX_RPF_BUILD_DEPTH,
    MAX_RPF_BUILD_FILES,
    MAX_RPF_BUILD_FILE_BYTES,
    RpfArchiveBuilder,
)
from allin1_sdk.rpf_tools import RpfExplorerService, RpfIndex


RPF_GRAPH_SCHEMA = 1
RPF_GRAPH_OPERATION = "rpf_package_graph"
RPF_GRAPH_NODE_TYPES = frozenset({
    "archive", "sealed_archive", "directory", "file",
})
RPF_GRAPH_SOURCE_NODE_TYPES = frozenset({"sealed_archive", "file"})
RPF_GRAPH_SEMANTIC_TYPES = frozenset({"vehicle"})
RPF_GRAPH_RELATION_GROUPS = frozenset({
    "assets", "metadata", "tuning", "registration",
})
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


def _semantic_text(value: object, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"RPF graph semantic {label} is invalid")
    return value


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
        root_name: str = "", allow_sealed_rpfs: bool = False,
        origin: dict[str, Any] | None = None,
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
        if origin is not None:
            payload["origin"] = origin
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
                sealed = child.is_file() and child.suffix.casefold() == ".rpf"
                if sealed and not allow_sealed_rpfs:
                    raise ValueError(
                        f"Prebuilt RPF files cannot be graph source nodes: {child}"
                    )
                kind = (
                    "archive" if nested else "directory" if child.is_dir()
                    else "sealed_archive" if sealed else "file"
                )
                authored_name = _safe_name(
                    authored_name, archive=kind in {"archive", "sealed_archive"},
                )
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
                if kind in RPF_GRAPH_SOURCE_NODE_TYPES:
                    size = child.stat().st_size
                    if size > MAX_RPF_BUILD_FILE_BYTES:
                        raise ValueError(f"RPF graph source file is too large: {child}")
                    node.update({
                        "source": str(child), "size": size,
                        "sha256": _sha256_file(child),
                    })
                nodes.append(node)
                edges.append({"parent": parent_id, "child": node_id})
                if kind in {"archive", "directory"}:
                    scan(child, node_id, child_logical, depth + 1)

        scan(source, "root", "", 1)
        return cls._publish_new(destination, payload)

    @classmethod
    def import_archive(
        cls, index: RpfIndex, service: RpfExplorerService,
        destination: str | Path,
    ) -> Path:
        """Expand an existing recursive RPF into a retained external graph workspace."""
        output = Path(destination).expanduser().resolve()
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"RPF graph import workspace already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{output.name}.rpf-graph-import-", dir=output.parent,
        )).resolve()
        published = False
        try:
            loose, export_report = service.extract_authoring_tree(
                index, staging / "source",
            )
            graph = cls.create_from_folder(
                loose, staging / "rpf-graph.json", root_name=index.source.name,
            )
            _graph, payload = cls._read(graph)
            payload["origin"] = {
                "type": "rpf_archive_import",
                **export_report["source"],
                "archives": export_report["summary"]["archives"],
                "files": export_report["summary"]["files"],
            }
            for node in payload["nodes"]:
                if node["type"] != "file":
                    continue
                relative = Path(node["source"]).resolve().relative_to(staging)
                node["source"] = str(output / relative)
            cls._normalize(payload, verify_sources=False)
            _write_json_atomic(graph, payload)
            staging.replace(output)
            published = True
            final_graph = output / graph.name
            state = cls.validate(final_graph, verify_sources=True)
            report = {
                **export_report,
                "operation": "rpf_graph_archive_import",
                "graph": {
                    "path": str(final_graph), "sha256": state["graph_sha256"],
                    "nodes": len(state["nodes"]), "root_node": state["root_id"],
                },
                "workspace": str(output),
                "stock_game_files_modified": False,
            }
            _write_json_atomic(output / "rpf-graph-import.json", report)
            return final_graph
        except Exception:
            cleanup = output if published else staging
            if cleanup.is_dir() and cleanup.parent == output.parent:
                shutil.rmtree(cleanup)
            raise

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
        origin = payload.get("origin")
        if origin is not None:
            if not isinstance(origin, dict):
                raise ValueError("RPF graph has invalid origin provenance")
            origin_type = origin.get("type")
            if origin_type == "rpf_archive_import":
                valid_origin = (
                    isinstance(origin.get("path"), str)
                    and Path(origin["path"]).is_absolute()
                    and isinstance(origin.get("edition"), str)
                    and bool(origin["edition"].strip())
                    and isinstance(origin.get("size"), int)
                    and not isinstance(origin.get("size"), bool)
                    and origin["size"] >= 0
                    and _is_sha256(origin.get("sha256"))
                    and isinstance(origin.get("archives"), int)
                    and not isinstance(origin.get("archives"), bool)
                    and origin["archives"] >= 1
                    and isinstance(origin.get("files"), int)
                    and not isinstance(origin.get("files"), bool)
                    and origin["files"] >= 0
                )
            elif origin_type == "mod_package_import":
                valid_origin = (
                    isinstance(origin.get("path"), str)
                    and Path(origin["path"]).is_absolute()
                    and origin.get("source_kind") in {
                        "folder", "oiv", "zip", "rar", "7z",
                    }
                    and _is_sha256(origin.get("package_fingerprint"))
                    and isinstance(origin.get("entries"), int)
                    and not isinstance(origin.get("entries"), bool)
                    and origin["entries"] >= 1
                    and isinstance(origin.get("sealed_rpfs"), int)
                    and not isinstance(origin.get("sealed_rpfs"), bool)
                    and 0 <= origin["sealed_rpfs"] <= origin["entries"]
                )
            else:
                valid_origin = False
            if not valid_origin:
                raise ValueError("RPF graph has invalid origin provenance")
            origin = dict(origin)
            origin["path"] = str(Path(origin["path"]).resolve())
            if origin_type == "rpf_archive_import":
                origin["edition"] = origin["edition"].strip()
                origin["sha256"] = str(origin["sha256"]).casefold()
            else:
                origin["package_fingerprint"] = str(
                    origin["package_fingerprint"]
                ).casefold()
            payload = {**payload, "origin": origin}
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
            name = _safe_name(
                authored.get("name"),
                archive=kind in {"archive", "sealed_archive"},
            )
            node: dict[str, Any] = {
                "id": node_id, "type": kind, "name": name,
                "x": _number(authored.get("x", 0), "x"),
                "y": _number(authored.get("y", 0), "y"),
            }
            expanded_from = authored.get("expanded_from")
            if expanded_from is not None:
                if (
                    kind != "archive" or not isinstance(expanded_from, dict)
                    or not isinstance(expanded_from.get("path"), str)
                    or not Path(expanded_from["path"]).is_absolute()
                    or not isinstance(expanded_from.get("size"), int)
                    or isinstance(expanded_from.get("size"), bool)
                    or expanded_from["size"] < 0
                    or not _is_sha256(expanded_from.get("sha256"))
                    or not isinstance(expanded_from.get("edition"), str)
                ):
                    raise ValueError(f"RPF graph expanded provenance is invalid: {name}")
                node["expanded_from"] = {
                    "path": str(Path(expanded_from["path"]).resolve()),
                    "size": expanded_from["size"],
                    "sha256": str(expanded_from["sha256"]).casefold(),
                    "edition": expanded_from["edition"],
                }
            if kind in RPF_GRAPH_SOURCE_NODE_TYPES:
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
            if nodes[parent]["type"] in RPF_GRAPH_SOURCE_NODE_TYPES:
                raise ValueError("RPF graph source nodes cannot contain child nodes")
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
        semantic_payload = payload.get("semantic")
        semantic: dict[str, Any] | None = None
        if semantic_payload is not None:
            if (
                not isinstance(semantic_payload, dict)
                or semantic_payload.get("schema_version") != 1
                or semantic_payload.get("analyzer") != "vehicle_relationships"
            ):
                raise ValueError("RPF graph semantic relationship schema is invalid")
            authored_entities = semantic_payload.get("entities")
            authored_relations = semantic_payload.get("relations")
            authored_findings = semantic_payload.get("findings")
            if (
                not isinstance(authored_entities, list)
                or not isinstance(authored_relations, list)
                or not isinstance(authored_findings, list)
                or len(authored_entities) > MAX_RPF_GRAPH_NODES
                or len(authored_relations) > MAX_RPF_GRAPH_NODES * 8
                or len(authored_findings) > MAX_RPF_GRAPH_NODES * 8
            ):
                raise ValueError("RPF graph semantic relationship collection is invalid")
            entities: dict[str, dict[str, Any]] = {}
            semantic_ids = {node_id.casefold() for node_id in nodes}
            for authored in authored_entities:
                if not isinstance(authored, dict):
                    raise ValueError("RPF graph semantic entities must be objects")
                entity_id = _safe_id(authored.get("id"))
                if entity_id.casefold() in semantic_ids:
                    raise ValueError(f"Duplicate RPF graph semantic id: {entity_id}")
                semantic_ids.add(entity_id.casefold())
                entity_type = str(authored.get("type", "")).casefold()
                if entity_type not in RPF_GRAPH_SEMANTIC_TYPES:
                    raise ValueError(
                        f"Unsupported RPF graph semantic entity type: {entity_type}"
                    )
                metadata = authored.get("metadata")
                finding_codes = authored.get("finding_codes", [])
                if not isinstance(metadata, dict) or not isinstance(finding_codes, list):
                    raise ValueError("RPF graph semantic entity metadata is invalid")
                if any(not isinstance(item, str) for item in finding_codes):
                    raise ValueError("RPF graph semantic finding codes are invalid")
                source_root = authored.get("source_root")
                if (
                    not isinstance(source_root, str)
                    or not Path(source_root).is_absolute()
                ):
                    raise ValueError("RPF graph semantic source root must be absolute")
                entities[entity_id] = {
                    "id": entity_id, "type": entity_type,
                    "name": _semantic_text(authored.get("name"), "entity name", maximum=256),
                    "x": _number(authored.get("x", 0), "semantic x"),
                    "y": _number(authored.get("y", 0), "semantic y"),
                    "source_root": str(Path(source_root).resolve()),
                    "edition": _semantic_text(
                        authored.get("edition"), "edition", maximum=64,
                    ),
                    "metadata": metadata,
                    "finding_codes": finding_codes,
                }
            endpoints = set(nodes) | set(entities)
            normalized_relations: list[dict[str, Any]] = []
            seen_relations: set[tuple[str, str, str]] = set()
            for authored in authored_relations:
                if not isinstance(authored, dict):
                    raise ValueError("RPF graph semantic relations must be objects")
                source = _safe_id(authored.get("source"))
                target = _safe_id(authored.get("target"))
                relation_type = _semantic_text(
                    authored.get("type"), "relation type", maximum=64,
                ).casefold()
                group = str(authored.get("group", "")).casefold()
                if (
                    source not in endpoints or target not in endpoints
                    or source == target or group not in RPF_GRAPH_RELATION_GROUPS
                ):
                    raise ValueError("RPF graph semantic relation endpoint/group is invalid")
                key = (source.casefold(), target.casefold(), relation_type)
                if key in seen_relations:
                    raise ValueError("RPF graph has a duplicate semantic relation")
                seen_relations.add(key)
                required = authored.get("required", False)
                if not isinstance(required, bool):
                    raise ValueError("RPF graph semantic relation required flag is invalid")
                normalized_relations.append({
                    "source": source, "target": target, "type": relation_type,
                    "group": group,
                    "label": _semantic_text(
                        authored.get("label"), "relation label", maximum=256,
                    ),
                    "required": required,
                })
            normalized_findings: list[dict[str, Any]] = []
            for authored in authored_findings:
                if not isinstance(authored, dict):
                    raise ValueError("RPF graph semantic findings must be objects")
                severity = str(authored.get("severity", "")).casefold()
                if severity not in {"info", "warning", "error"}:
                    raise ValueError("RPF graph semantic finding severity is invalid")
                finding = dict(authored)
                finding.update({
                    "severity": severity,
                    "code": _semantic_text(
                        authored.get("code"), "finding code", maximum=128,
                    ),
                    "message": _semantic_text(
                        authored.get("message"), "finding message",
                    ),
                })
                for key in ("entity_id", "node_id"):
                    endpoint = finding.get(key)
                    if endpoint is not None and endpoint not in endpoints:
                        raise ValueError(
                            f"RPF graph semantic finding references an invalid {key}"
                        )
                normalized_findings.append(finding)
            semantic = {
                **semantic_payload,
                "entities": list(entities.values()),
                "relations": normalized_relations,
                "findings": normalized_findings,
            }
        return {
            "payload": payload, "root_id": root_id, "nodes": nodes,
            "parents": parents, "children": children,
            "file_count": file_count, "byte_count": byte_count,
            "archive_count": sum(1 for node in nodes.values() if node["type"] == "archive"),
            "sealed_archive_count": sum(
                1 for node in nodes.values() if node["type"] == "sealed_archive"
            ),
            "directory_count": sum(1 for node in nodes.values() if node["type"] == "directory"),
            "semantic": semantic,
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
                "sealed_archives": state["sealed_archive_count"],
                "directories": state["directory_count"],
                "files": state["file_count"], "source_bytes": state["byte_count"],
            },
            "nodes": list(state["nodes"].values()),
            "edges": list(state["payload"]["edges"]),
            "origin": state["payload"].get("origin"),
            "semantic": state["semantic"],
        }

    @classmethod
    def _mutate(
        cls, path: str | Path, update: Callable[[dict[str, Any]], Any],
    ) -> Any:
        graph, payload = cls._read(path)
        graph_sha256 = _sha256_file(graph)
        cls._normalize(payload, verify_sources=False)
        before = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        result = update(payload)
        if _sha256_file(graph) != graph_sha256:
            raise RuntimeError("RPF graph changed during edit")
        if json.dumps(payload, sort_keys=True, separators=(",", ":")) == before:
            return result
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
            if parent not in nodes or nodes[parent]["type"] in RPF_GRAPH_SOURCE_NODE_TYPES:
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
            if parent not in nodes or nodes[parent]["type"] in RPF_GRAPH_SOURCE_NODE_TYPES:
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
                name,
                archive=nodes[wanted]["type"] in {"archive", "sealed_archive"},
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
            if (
                wanted not in nodes or parent not in nodes
                or nodes[parent]["type"] in RPF_GRAPH_SOURCE_NODE_TYPES
            ):
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
            semantic = payload.get("semantic")
            if isinstance(semantic, dict):
                semantic["relations"] = [
                    item for item in semantic.get("relations", [])
                    if item.get("source") not in removed_set
                    and item.get("target") not in removed_set
                ]
                semantic["findings"] = [
                    item for item in semantic.get("findings", [])
                    if item.get("node_id") not in removed_set
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
    def set_semantic_report(
        cls, path: str | Path, report: dict[str, Any],
    ) -> None:
        """Replace the derived semantic overlay without changing containment nodes."""
        if not isinstance(report, dict):
            raise ValueError("RPF graph semantic report must be an object")

        def update(payload: dict[str, Any]) -> None:
            payload["semantic"] = report

        cls._mutate(path, update)

    @classmethod
    def set_semantic_position(
        cls, path: str | Path, entity_id: str, x: float, y: float,
    ) -> None:
        wanted = _safe_id(entity_id)
        safe_x = _number(x, "semantic x")
        safe_y = _number(y, "semantic y")

        def update(payload: dict[str, Any]) -> None:
            semantic = payload.get("semantic")
            entities = semantic.get("entities", []) if isinstance(semantic, dict) else []
            entity = next(
                (item for item in entities if item.get("id") == wanted), None,
            )
            if entity is None:
                raise ValueError(f"RPF graph semantic entity was not found: {wanted}")
            entity["x"], entity["y"] = safe_x, safe_y

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
            semantic = payload.get("semantic")
            if isinstance(semantic, dict):
                semantic_x = max(item["x"] for item in nodes.values()) + safe_x_spacing
                for index, entity in enumerate(semantic.get("entities", [])):
                    entity["x"] = semantic_x
                    entity["y"] = 80.0 + index * max(safe_y_spacing, 132.0)
            return row

        return cls._mutate(path, update)

    @classmethod
    def refresh_sources(cls, path: str | Path) -> int:
        def update(payload: dict[str, Any]) -> int:
            changed = 0
            for node in payload["nodes"]:
                if node["type"] not in RPF_GRAPH_SOURCE_NODE_TYPES:
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
    def relocate_sources(
        cls, path: str | Path, old_root: str | Path, new_root: str | Path,
    ) -> int:
        """Rebind source nodes when an atomic graph workspace is published."""
        previous = Path(old_root).expanduser().resolve()
        destination = Path(new_root).expanduser().resolve()

        def update(payload: dict[str, Any]) -> int:
            changed = 0
            for node in payload["nodes"]:
                if node["type"] not in RPF_GRAPH_SOURCE_NODE_TYPES:
                    continue
                source = Path(node["source"]).resolve()
                if not source.is_relative_to(previous):
                    raise ValueError(
                        f"RPF graph source is outside its workspace: {source}"
                    )
                node["source"] = str(destination / source.relative_to(previous))
                changed += 1
            return changed

        return cls._mutate(path, update)

    @classmethod
    def expand_sealed_archive(
        cls, path: str | Path, node_id: str, service: RpfExplorerService,
    ) -> dict[str, Any]:
        """Expand one immutable package RPF into editable, source-hashed nodes."""
        graph = Path(path).expanduser().resolve()
        state = cls.validate(graph, verify_sources=True)
        wanted = _safe_id(node_id)
        sealed = state["nodes"].get(wanted)
        if sealed is None or sealed["type"] != "sealed_archive":
            raise ValueError("Select a sealed RPF node to expand")
        expansions = graph.parent / "expanded-rpfs"
        expansions.mkdir(parents=True, exist_ok=True)
        target = expansions / f"{wanted}.rpf.source"
        if target.exists() or target.is_symlink():
            raise ValueError(f"Sealed RPF expansion already exists: {target}")
        index = service.index(sealed["source"])
        loose: Path | None = None
        try:
            loose, report = service.extract_authoring_tree(index, target)
            with tempfile.TemporaryDirectory(
                prefix="allin1-rpf-subgraph-", dir=graph.parent,
            ) as temporary:
                subgraph = cls.create_from_folder(
                    loose, Path(temporary) / "subgraph.json",
                    root_name=sealed["name"],
                )
                substate = cls.validate(subgraph, verify_sources=True)

            def update(payload: dict[str, Any]) -> dict[str, Any]:
                nodes = {item["id"]: item for item in payload["nodes"]}
                authored = nodes.get(wanted)
                if authored is None or authored["type"] != "sealed_archive":
                    raise ValueError("Sealed RPF node changed during expansion")
                authored.update({
                    "type": "archive",
                    "expanded_from": {
                        "path": authored.pop("source"),
                        "size": authored.pop("size"),
                        "sha256": authored.pop("sha256"),
                        "edition": index.edition,
                    },
                })
                mapping = {
                    item: f"{wanted}_{item}"
                    for item in substate["nodes"] if item != substate["root_id"]
                }
                root_x = substate["nodes"][substate["root_id"]]["x"]
                root_y = substate["nodes"][substate["root_id"]]["y"]
                added = 0
                for item, replacement in mapping.items():
                    source_node = dict(substate["nodes"][item])
                    if source_node["name"] == ".allin1-rpf-export.json":
                        continue
                    source_node["id"] = replacement
                    source_node["x"] = authored["x"] + source_node["x"] - root_x
                    source_node["y"] = authored["y"] + source_node["y"] - root_y
                    payload["nodes"].append(source_node)
                    added += 1
                included = {
                    item for item in mapping
                    if substate["nodes"][item]["name"] != ".allin1-rpf-export.json"
                }
                for edge in substate["payload"]["edges"]:
                    if edge["child"] not in included:
                        continue
                    parent = (
                        wanted if edge["parent"] == substate["root_id"]
                        else mapping[edge["parent"]]
                    )
                    payload["edges"].append({
                        "parent": parent, "child": mapping[edge["child"]],
                    })
                return {
                    "node_id": wanted, "edition": index.edition,
                    "added_nodes": added,
                    "archives": report["summary"]["archives"],
                    "files": report["summary"]["files"],
                    "workspace": str(target),
                }

            result = cls._mutate(graph, update)
            cls.auto_layout(graph)
            cls.validate(graph, verify_sources=True)
            return result
        except Exception:
            if loose is not None and target.is_dir() and target.parent == expansions:
                shutil.rmtree(target)
            raise

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
                    if kind in RPF_GRAPH_SOURCE_NODE_TYPES:
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
        origin = state["payload"].get("origin")
        if isinstance(origin, dict) and origin.get("type") == "mod_package_import":
            raise ValueError(
                "A package-review graph cannot be built as one RPF; create a dedicated "
                "RPF authoring graph from the desired subtree."
            )
        if state["sealed_archive_count"]:
            raise ValueError(
                "Sealed RPF nodes must be expanded or removed before building an RPF."
            )
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

    @staticmethod
    def _root_semantic_states(
        index: RpfIndex, service: RpfExplorerService,
    ) -> dict[str, dict[str, Any]]:
        leaves = tuple(
            entry for entry in index.entries
            if entry.kind not in {"directory", "archive"}
        )
        fingerprints = service.entry_content_fingerprints(index, leaves)
        states: dict[str, dict[str, Any]] = {}
        for entry in (item for item in index.entries if not item.archive_path):
            state: dict[str, Any] = {"path": entry.path, "kind": entry.kind}
            if entry.kind == "directory":
                state["signature"] = "directory"
            elif entry.kind != "archive":
                fingerprint = fingerprints[entry.id]
                state["signature"] = hashlib.sha256(json.dumps({
                    "mode": fingerprint["mode"],
                    "logical_size": fingerprint["logical_size"],
                    "canonical_sha256": fingerprint["canonical_sha256"],
                }, sort_keys=True).encode("utf-8")).hexdigest()
            else:
                prefix = entry.path.casefold()

                def inside(value: str) -> bool:
                    folded = value.casefold()
                    return folded == prefix or folded.startswith(f"{prefix}!")

                archive_records = [
                    {"path": archive.path}
                    for archive in index.archives if inside(archive.path)
                ]
                entry_records: list[dict[str, Any]] = []
                for nested in (item for item in index.entries if inside(item.archive_path)):
                    record: dict[str, Any] = {
                        "archive_path": nested.archive_path,
                        "path": nested.path, "kind": nested.kind,
                    }
                    if nested.kind not in {"directory", "archive"}:
                        fingerprint = fingerprints[nested.id]
                        record["content"] = {
                            "mode": fingerprint["mode"],
                            "logical_size": fingerprint["logical_size"],
                            "canonical_sha256": fingerprint["canonical_sha256"],
                        }
                    entry_records.append(record)
                semantic = {
                    "archives": sorted(
                        archive_records, key=lambda item: item["path"].casefold(),
                    ),
                    "entries": sorted(
                        entry_records,
                        key=lambda item: (
                            item["archive_path"].casefold(), item["path"].casefold(),
                        ),
                    ),
                }
                state["signature"] = hashlib.sha256(
                    json.dumps(semantic, sort_keys=True).encode("utf-8")
                ).hexdigest()
            folded_path = entry.path.casefold()
            if folded_path in states:
                raise ValueError(f"RPF root has a case-insensitive collision: {entry.path}")
            states[folded_path] = state
        return states

    @classmethod
    def plan_origin_changes(
        cls, path: str | Path, builder: RpfArchiveBuilder,
        service: RpfExplorerService, destination: str | Path,
    ) -> tuple[Path, Path]:
        """Build an imported graph and emit an inert atomic plan against its origin."""
        state = cls.validate(path, verify_sources=True)
        origin = state["payload"].get("origin")
        if not isinstance(origin, dict) or origin.get("type") != "rpf_archive_import":
            raise ValueError("RPF origin planning requires an imported archive graph")
        source = Path(origin["path"]).resolve()
        if (
            not source.is_file() or source.stat().st_size != origin["size"]
            or _sha256_file(source) != origin["sha256"]
        ):
            raise ValueError("Imported RPF origin changed; import it again before planning")
        root_name = state["nodes"][state["root_id"]]["name"]
        if root_name != source.name:
            raise ValueError("An origin change plan cannot rename the outer RPF archive")
        plan_path = Path(destination).expanduser().resolve()
        if plan_path.suffix.casefold() != ".json":
            raise ValueError("RPF graph origin plan must use a .json extension")
        if plan_path.exists() or plan_path.is_symlink():
            raise FileExistsError(f"RPF graph origin plan already exists: {plan_path}")
        payload_root = plan_path.with_name(f"{plan_path.stem}.payload")
        if payload_root.exists() or payload_root.is_symlink():
            raise FileExistsError(f"RPF graph origin payload folder exists: {payload_root}")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{plan_path.stem}.rpf-origin-plan-", dir=plan_path.parent,
        )).resolve()
        published = False
        try:
            original_index = service.index(source)
            if original_index.edition.casefold() != origin["edition"].casefold():
                raise ValueError("Imported RPF origin edition changed")
            desired_archive, desired_validation = cls.build(
                path, builder, staging / "desired" / source.name,
            )
            desired_index = service.index(desired_archive)
            if desired_index.edition.casefold() != original_index.edition.casefold():
                raise ValueError("Graph build edition differs from its imported origin")
            original_states = cls._root_semantic_states(original_index, service)
            desired_states = cls._root_semantic_states(desired_index, service)
            changes: list[dict[str, Any]] = []
            payload_records: list[tuple[dict[str, Any], Path]] = []
            for folded in sorted(set(original_states) | set(desired_states)):
                before = original_states.get(folded)
                after = desired_states.get(folded)
                if before is not None and after is not None:
                    if before["path"] != after["path"]:
                        raise ValueError(
                            "Case-only RPF path changes require an explicit rename plan: "
                            f"{before['path']} -> {after['path']}"
                        )
                    if before["kind"] != after["kind"]:
                        raise ValueError(
                            f"RPF graph origin planning does not combine a type change at "
                            f"{before['path']}"
                        )
                    if before["kind"] == "directory" or (
                        before["signature"] == after["signature"]
                    ):
                        continue
                    change = {
                        "action": "replace", "archive_path": "",
                        "entry": after["path"],
                    }
                    changes.append(change)
                    payload_records.append((change, Path(after["path"])))
                    continue
                if before is not None:
                    changes.append({
                        "action": "rmdir" if before["kind"] == "directory" else "delete",
                        "archive_path": "", "entry": before["path"],
                    })
                    continue
                assert after is not None
                change = {
                    "action": "mkdir" if after["kind"] == "directory" else "add",
                    "archive_path": "", "entry": after["path"],
                }
                changes.append(change)
                if after["kind"] != "directory":
                    payload_records.append((change, Path(after["path"])))
            if not changes:
                raise ValueError("RPF graph has no logical changes from its imported origin")
            payload_stage = staging / "changes"
            payload_stage.mkdir()
            for number, (change, virtual_path) in enumerate(payload_records):
                entry = desired_index.entry(f"::{virtual_path.as_posix()}")
                suffix = virtual_path.suffix or ".bin"
                relative = Path("changes") / f"{number:04d}{suffix}"
                service.extract(desired_index, entry, staging / relative)
                change["payload_relative"] = relative.as_posix()
            if cls.validate(path, verify_sources=True)["graph_sha256"] != state["graph_sha256"]:
                raise RuntimeError("RPF graph changed during origin-plan creation")
            staging.replace(payload_root)
            published = True
            for change in changes:
                relative = change.pop("payload_relative", None)
                if relative is not None:
                    change["payload"] = payload_root / Path(relative)
            plan = service.multi_change_plan(original_index, changes)
            plan["rpf_graph"] = {
                "path": str(state["graph"]), "sha256": state["graph_sha256"],
                "origin_sha256": origin["sha256"],
                "desired_archive": str(payload_root / desired_archive.relative_to(staging)),
                "desired_validation": str(
                    payload_root / desired_validation.relative_to(staging)
                ),
                "comparison": "canonical_logical_content",
            }
            _write_json_atomic(plan_path, plan)
            return plan_path, payload_root
        except Exception:
            cleanup = payload_root if published else staging
            if cleanup.is_dir() and cleanup.parent == plan_path.parent:
                shutil.rmtree(cleanup)
            plan_path.unlink(missing_ok=True)
            raise

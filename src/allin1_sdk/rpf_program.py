"""Typed, executable node programs for safe RPF package authoring."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_tools import RpfExplorerService


RPF_PROGRAM_SCHEMA = 1
RPF_PROGRAM_OPERATION = "rpf_package_program"
MAX_RPF_PROGRAM_NODES = 128
MAX_RPF_PROGRAM_LINKS = 256
MAX_RPF_PROGRAM_COORDINATE = 1_000_000


@dataclass(frozen=True)
class ProgramNodeSpec:
    title: str
    input_types: tuple[str, ...]
    output_type: str | None
    required_config: tuple[str, ...] = ()
    optional_config: tuple[str, ...] = ()


NODE_SPECS: dict[str, ProgramNodeSpec] = {
    "package_source": ProgramNodeSpec("Package graph", (), "package"),
    "validate_graph": ProgramNodeSpec(
        "Validate package", ("package",), "validated_package",
    ),
    "materialize_tree": ProgramNodeSpec(
        "Materialize tree", ("validated_package",), "directory", ("output",),
    ),
    "build_rpf": ProgramNodeSpec(
        "Build + verify RPF", ("validated_package",), "rpf",
        ("gta_path", "output"),
    ),
    "defragment_rpf": ProgramNodeSpec(
        "Defragment + verify", ("rpf",), "rpf",
        ("gta_path", "output", "report"),
    ),
    "plan_origin": ProgramNodeSpec(
        "Plan imported-origin changes", ("validated_package",), "plan",
        ("gta_path", "output"),
    ),
    "artifact_output": ProgramNodeSpec(
        "Artifact output", ("rpf", "directory", "plan"), None,
        optional_config=("label",),
    ),
}


PROGRAM_TEMPLATES: dict[str, dict[str, Any]] = {
    "validate": {
        "title": "Validate only",
        "description": "Verify the package graph and every bound source file.",
        "nodes": (),
        "links": (),
    },
    "loose-export": {
        "title": "Loose authoring tree",
        "description": "Validate, materialize a loose tree, and expose it as an artifact.",
        "nodes": (
            ("materialize", "materialize_tree", 680.0, 120.0),
            ("artifact", "artifact_output", 980.0, 120.0),
        ),
        "links": (("validate", "materialize"), ("materialize", "artifact")),
    },
    "verified-build": {
        "title": "Verified RPF build",
        "description": "Validate, build an external RPF, and expose the verified archive.",
        "nodes": (
            ("build", "build_rpf", 680.0, 120.0),
            ("artifact", "artifact_output", 980.0, 120.0),
        ),
        "links": (("validate", "build"), ("build", "artifact")),
    },
    "compact-release": {
        "title": "Compact verified release",
        "description": (
            "Validate, build, defragment a separate copy, and expose the compact RPF."
        ),
        "nodes": (
            ("build", "build_rpf", 680.0, 120.0),
            ("compact", "defragment_rpf", 980.0, 120.0),
            ("artifact", "artifact_output", 1280.0, 120.0),
        ),
        "links": (
            ("validate", "build"), ("build", "compact"),
            ("compact", "artifact"),
        ),
    },
    "origin-change-plan": {
        "title": "Imported archive change plan",
        "description": (
            "Validate an imported graph and emit a reviewed plan for its origin archive."
        ),
        "nodes": (
            ("origin-plan", "plan_origin", 680.0, 120.0),
            ("artifact", "artifact_output", 980.0, 120.0),
        ),
        "links": (("validate", "origin-plan"), ("origin-plan", "artifact")),
    },
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_new(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        temporary.rename(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_id(value: object) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in value)
    ):
        raise ValueError(f"Unsafe RPF program node id: {value!r}")
    return value


def _coordinate(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"RPF program {label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"RPF program {label} must be numeric") from exc
    if not math.isfinite(number) or abs(number) > MAX_RPF_PROGRAM_COORDINATE:
        raise ValueError(f"RPF program {label} exceeds the coordinate limit")
    return number


def _configured_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"RPF program {label} must be an absolute path")
    authored = Path(value).expanduser()
    if not authored.is_absolute():
        raise ValueError(f"RPF program {label} must be an absolute path")
    return authored.resolve()


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _overlaps(left: Path, right: Path) -> bool:
    return _inside(left, right) or _inside(right, left)


def _detected_gta_root(path: Path) -> Path | None:
    """Find a GTA install ancestor without trusting only a user-supplied path."""
    folder = path if path.is_dir() else path.parent
    for candidate in (folder, *folder.parents):
        if any((candidate / marker).is_file() for marker in (
            "GTA5.exe", "GTA5_Enhanced.exe", "PlayGTAV.exe",
        )):
            return candidate
    return None


class RpfPackageProgram:
    """Create, edit, validate, plan, and execute a typed RPF node program."""

    @classmethod
    def create(
        cls, package_graph: str | Path, destination: str | Path, *,
        template: str = "validate",
    ) -> Path:
        graph = Path(package_graph).expanduser().resolve()
        RpfPackageGraph.validate(graph, verify_sources=False)
        if template not in PROGRAM_TEMPLATES:
            raise ValueError(f"Unknown RPF program template: {template}")
        output = Path(destination).expanduser().resolve()
        if output.suffix.casefold() != ".json":
            raise ValueError("RPF program output must use a .json extension")
        detected_game = _detected_gta_root(output)
        if detected_game is not None:
            raise ValueError(
                f"RPF package programs must be created outside GTA V: {detected_game}"
            )
        now = datetime.now(timezone.utc).isoformat()
        template_spec = PROGRAM_TEMPLATES[template]
        nodes = [
            {
                "id": "package", "type": "package_source",
                "x": 80.0, "y": 120.0, "config": {},
            },
            {
                "id": "validate", "type": "validate_graph",
                "x": 380.0, "y": 120.0, "config": {},
            },
        ]
        nodes.extend({
            "id": node_id, "type": node_type, "x": x, "y": y, "config": {},
        } for node_id, node_type, x, y in template_spec["nodes"])
        links = [("package", "validate"), *template_spec["links"]]
        payload = {
            "schema_version": RPF_PROGRAM_SCHEMA,
            "operation": RPF_PROGRAM_OPERATION,
            "created_utc": now,
            "updated_utc": now,
            "package_graph": str(graph),
            "template": template,
            "source_id": "package",
            "nodes": nodes,
            "links": [{
                "from": parent, "from_port": "artifact",
                "to": child, "to_port": "input",
            } for parent, child in links],
        }
        cls._normalize(payload, verify_graph=False)
        _write_json_new(output, payload)
        return output

    @staticmethod
    def _read(path: str | Path) -> tuple[Path, dict[str, Any]]:
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"RPF package program not found: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid RPF package program JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("RPF package program must be a JSON object")
        return source, payload

    @classmethod
    def _normalize(
        cls, payload: dict[str, Any], *, verify_graph: bool,
    ) -> dict[str, Any]:
        if (
            payload.get("schema_version") != RPF_PROGRAM_SCHEMA
            or payload.get("operation") != RPF_PROGRAM_OPERATION
        ):
            raise ValueError("Unsupported RPF package program schema")
        template = payload.get("template", "validate")
        if template not in PROGRAM_TEMPLATES:
            raise ValueError(f"Unknown RPF program template: {template!r}")
        graph = _configured_path(payload.get("package_graph"), "package graph")
        if verify_graph:
            RpfPackageGraph.validate(graph, verify_sources=True)
        authored_nodes = payload.get("nodes")
        authored_links = payload.get("links")
        if not isinstance(authored_nodes, list) or not isinstance(authored_links, list):
            raise ValueError("RPF package program nodes and links must be arrays")
        if not 1 <= len(authored_nodes) <= MAX_RPF_PROGRAM_NODES:
            raise ValueError("RPF package program exceeds its guarded node limit")
        if len(authored_links) > MAX_RPF_PROGRAM_LINKS:
            raise ValueError("RPF package program exceeds its guarded link limit")

        nodes: dict[str, dict[str, Any]] = {}
        issues: list[str] = []
        for authored in authored_nodes:
            if not isinstance(authored, dict):
                raise ValueError("RPF package program nodes must be objects")
            node_id = _safe_id(authored.get("id"))
            if node_id.casefold() in {item.casefold() for item in nodes}:
                raise ValueError(f"Duplicate RPF package program node id: {node_id}")
            node_type = authored.get("type")
            if node_type not in NODE_SPECS:
                raise ValueError(f"Unknown RPF package program node type: {node_type!r}")
            config = authored.get("config", {})
            if not isinstance(config, dict) or any(not isinstance(key, str) for key in config):
                raise ValueError(f"RPF program node {node_id} config must be an object")
            spec = NODE_SPECS[node_type]
            unknown = set(config).difference(spec.required_config, spec.optional_config)
            if unknown:
                raise ValueError(
                    f"RPF program node {node_id} has unsupported config: "
                    + ", ".join(sorted(unknown))
                )
            for key in spec.required_config:
                if key not in config or config[key] is None or config[key] == "":
                    issues.append(f"{node_id}: missing configuration '{key}'")
            for key, value in config.items():
                if key == "label":
                    if not isinstance(value, str) or len(value) > 200:
                        raise ValueError(f"RPF program node {node_id} label is invalid")
                else:
                    configured = _configured_path(value, f"{node_id}.{key}")
                    if key == "gta_path" and not configured.is_dir():
                        issues.append(f"{node_id}: GTA V path is not an available directory")
                    if key == "output" and node_type in {"build_rpf", "defragment_rpf"}:
                        if configured.suffix.casefold() != ".rpf":
                            issues.append(f"{node_id}: output must use the .rpf extension")
                    json_output = (
                        node_type == "defragment_rpf" and key == "report"
                    ) or (
                        node_type == "plan_origin" and key == "output"
                    )
                    if json_output:
                        if configured.suffix.casefold() != ".json":
                            issues.append(f"{node_id}: {key} must use the .json extension")
            node = {
                "id": node_id, "type": node_type,
                "x": _coordinate(authored.get("x", 0), f"{node_id}.x"),
                "y": _coordinate(authored.get("y", 0), f"{node_id}.y"),
                "config": dict(config),
            }
            nodes[node_id] = node

        source_id = _safe_id(payload.get("source_id"))
        if source_id not in nodes or nodes[source_id]["type"] != "package_source":
            raise ValueError("RPF package program source node is missing or invalid")
        if sum(node["type"] == "package_source" for node in nodes.values()) != 1:
            raise ValueError("RPF package program requires exactly one package source")

        incoming: dict[str, str] = {}
        outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        links: list[dict[str, str]] = []
        seen_links: set[tuple[str, str]] = set()
        for authored in authored_links:
            if not isinstance(authored, dict):
                raise ValueError("RPF package program links must be objects")
            parent = _safe_id(authored.get("from"))
            child = _safe_id(authored.get("to"))
            if parent not in nodes or child not in nodes or parent == child:
                raise ValueError("RPF package program link references invalid nodes")
            if authored.get("from_port") != "artifact" or authored.get("to_port") != "input":
                raise ValueError("RPF package program links must connect artifact to input")
            if (parent, child) in seen_links:
                raise ValueError("Duplicate RPF package program link")
            if child in incoming:
                raise ValueError(f"RPF program node {child} has more than one input")
            output_type = NODE_SPECS[nodes[parent]["type"]].output_type
            accepted = NODE_SPECS[nodes[child]["type"]].input_types
            if output_type is None or output_type not in accepted:
                raise ValueError(
                    f"Incompatible RPF program link: {parent} ({output_type}) -> "
                    f"{child} ({', '.join(accepted) or 'no input'})"
                )
            seen_links.add((parent, child))
            incoming[child] = parent
            outgoing[parent].append(child)
            links.append({
                "from": parent, "from_port": "artifact",
                "to": child, "to_port": "input",
            })

        indegree = {node_id: 0 for node_id in nodes}
        for child in incoming:
            indegree[child] += 1
        queue = sorted((node_id for node_id, count in indegree.items() if count == 0))
        order: list[str] = []
        while queue:
            node_id = queue.pop(0)
            order.append(node_id)
            for child in sorted(outgoing[node_id]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
                    queue.sort()
        if len(order) != len(nodes):
            raise ValueError("RPF package program contains a cycle")
        for node_id, node in nodes.items():
            if node_id == source_id:
                if node_id in incoming:
                    raise ValueError("RPF package source cannot have an input")
                continue
            if node_id not in incoming:
                issues.append(f"{node_id}: input is not connected")
            if node["type"] == "artifact_output" and outgoing[node_id]:
                raise ValueError("RPF artifact output nodes cannot feed another node")
        reachable = {source_id}
        frontier = [source_id]
        while frontier:
            for child in outgoing[frontier.pop()]:
                if child not in reachable:
                    reachable.add(child)
                    frontier.append(child)
        for node_id in nodes:
            if node_id not in reachable:
                issues.append(f"{node_id}: node is not reachable from package source")

        return {
            "payload": payload, "package_graph": graph,
            "template": template,
            "source_id": source_id, "nodes": nodes, "links": tuple(links),
            "incoming": incoming,
            "outgoing": {key: tuple(value) for key, value in outgoing.items()},
            "order": tuple(order), "issues": tuple(dict.fromkeys(issues)),
        }

    @classmethod
    def validate(
        cls, path: str | Path, *, verify_graph: bool = False,
    ) -> dict[str, Any]:
        authored = Path(path).expanduser().resolve()
        before_sha256 = _sha256_file(authored)
        source, payload = cls._read(path)
        if _sha256_file(source) != before_sha256:
            raise ValueError("RPF package program changed while opening it")
        state = cls._normalize(payload, verify_graph=verify_graph)
        state["program"] = source
        state["program_sha256"] = before_sha256
        return state

    @classmethod
    def describe(cls, path: str | Path, *, verify_graph: bool = False) -> dict[str, Any]:
        state = cls.validate(path, verify_graph=verify_graph)
        return {
            "schema_version": RPF_PROGRAM_SCHEMA,
            "operation": "rpf_package_program_inspection",
            "status": "ready" if not state["issues"] else "incomplete",
            "program": str(state["program"]),
            "program_sha256": state["program_sha256"],
            "package_graph": str(state["package_graph"]),
            "template": state["template"],
            "summary": {
                "nodes": len(state["nodes"]), "links": len(state["links"]),
                "ready": not state["issues"], "issues": len(state["issues"]),
            },
            "issues": list(state["issues"]),
            "execution_order": list(state["order"]),
            "nodes": list(state["nodes"].values()),
            "links": list(state["links"]),
        }

    @classmethod
    def _mutate(cls, path: str | Path, callback: Callable[[dict[str, Any]], Any]) -> Any:
        authored = Path(path).expanduser().resolve()
        before_sha256 = _sha256_file(authored)
        source, payload = cls._read(path)
        detected_game = _detected_gta_root(source)
        if detected_game is not None:
            raise ValueError(
                f"RPF package programs cannot be edited inside GTA V: {detected_game}"
            )
        if _sha256_file(source) != before_sha256:
            raise ValueError("RPF package program changed while opening it")
        cls._normalize(payload, verify_graph=False)
        result = callback(payload)
        payload["updated_utc"] = datetime.now(timezone.utc).isoformat()
        cls._normalize(payload, verify_graph=False)
        if _sha256_file(source) != before_sha256:
            raise ValueError("RPF package program changed during edit")
        _write_json_atomic(source, payload)
        return result

    @classmethod
    def add_node(
        cls, path: str | Path, node_type: str, *,
        config: dict[str, Any] | None = None, x: float = 0, y: float = 0,
    ) -> str:
        if node_type not in NODE_SPECS or node_type == "package_source":
            raise ValueError(f"Unsupported addable RPF program node type: {node_type}")
        safe_x, safe_y = _coordinate(x, "x"), _coordinate(y, "y")

        def update(payload: dict[str, Any]) -> str:
            existing = {item["id"] for item in payload["nodes"]}
            prefix = node_type.replace("_", "-")
            number = 1
            node_id = prefix
            while node_id in existing:
                number += 1
                node_id = f"{prefix}-{number}"
            payload["nodes"].append({
                "id": node_id, "type": node_type, "x": safe_x, "y": safe_y,
                "config": dict(config or {}),
            })
            return node_id

        return cls._mutate(path, update)

    @classmethod
    def configure_node(
        cls, path: str | Path, node_id: str, config: dict[str, Any],
    ) -> None:
        wanted = _safe_id(node_id)
        if not isinstance(config, dict):
            raise ValueError("RPF program node config must be an object")

        def update(payload: dict[str, Any]) -> None:
            node = next((item for item in payload["nodes"] if item["id"] == wanted), None)
            if node is None:
                raise ValueError(f"RPF program node not found: {wanted}")
            if node["type"] == "package_source":
                raise ValueError("The package source node has no editable configuration")
            normalized: dict[str, Any] = {}
            for key, value in config.items():
                if key != "label" and not isinstance(value, str):
                    raise ValueError(
                        f"RPF program node path configuration must be text: {key}"
                    )
                normalized[key] = (
                    str(Path(value).expanduser().resolve()) if key != "label" else value
                )
            node["config"] = normalized

        cls._mutate(path, update)

    @classmethod
    def connect(cls, path: str | Path, parent_id: str, child_id: str) -> None:
        parent, child = _safe_id(parent_id), _safe_id(child_id)

        def update(payload: dict[str, Any]) -> None:
            payload["links"] = [
                item for item in payload["links"] if item["to"] != child
            ]
            payload["links"].append({
                "from": parent, "from_port": "artifact",
                "to": child, "to_port": "input",
            })

        cls._mutate(path, update)

    @classmethod
    def disconnect(cls, path: str | Path, child_id: str) -> None:
        child = _safe_id(child_id)

        def update(payload: dict[str, Any]) -> None:
            before = len(payload["links"])
            payload["links"] = [item for item in payload["links"] if item["to"] != child]
            if len(payload["links"]) == before:
                raise ValueError(f"RPF program node has no input link: {child}")

        cls._mutate(path, update)

    @classmethod
    def remove_node(cls, path: str | Path, node_id: str) -> None:
        wanted = _safe_id(node_id)

        def update(payload: dict[str, Any]) -> None:
            if wanted == payload["source_id"]:
                raise ValueError("The RPF package source node cannot be removed")
            before = len(payload["nodes"])
            payload["nodes"] = [item for item in payload["nodes"] if item["id"] != wanted]
            if len(payload["nodes"]) == before:
                raise ValueError(f"RPF program node not found: {wanted}")
            payload["links"] = [
                item for item in payload["links"]
                if item["from"] != wanted and item["to"] != wanted
            ]

        cls._mutate(path, update)

    @classmethod
    def set_position(cls, path: str | Path, node_id: str, x: float, y: float) -> None:
        wanted, safe_x, safe_y = _safe_id(node_id), _coordinate(x, "x"), _coordinate(y, "y")

        def update(payload: dict[str, Any]) -> None:
            node = next((item for item in payload["nodes"] if item["id"] == wanted), None)
            if node is None:
                raise ValueError(f"RPF program node not found: {wanted}")
            node["x"], node["y"] = safe_x, safe_y

        cls._mutate(path, update)

    @classmethod
    def auto_layout(cls, path: str | Path) -> int:
        def update(payload: dict[str, Any]) -> int:
            state = cls._normalize(payload, verify_graph=False)
            depths = {state["source_id"]: 0}
            rows: dict[int, int] = {}
            for node_id in state["order"]:
                parent = state["incoming"].get(node_id)
                depth = depths.get(parent, -1) + 1 if parent else 0
                depths[node_id] = depth
                row = rows.get(depth, 0)
                rows[depth] = row + 1
                state["nodes"][node_id]["x"] = 80.0 + depth * 310.0
                state["nodes"][node_id]["y"] = 100.0 + row * 132.0
            return len(state["nodes"])

        return cls._mutate(path, update)

    @classmethod
    def _ready_state(cls, path: str | Path) -> dict[str, Any]:
        state = cls.validate(path, verify_graph=True)
        if state["issues"]:
            raise ValueError(
                "RPF package program is incomplete: " + "; ".join(state["issues"])
            )
        outputs: dict[str, Path] = {}
        gta_roots: set[Path] = set()

        def register(selected: Path) -> None:
            identity = str(selected).casefold()
            if identity in outputs:
                raise ValueError(f"RPF program output collision: {selected}")
            overlap = next((item for item in outputs.values() if _overlaps(selected, item)), None)
            if overlap is not None:
                raise ValueError(
                    f"RPF program outputs cannot contain one another: {overlap} and {selected}"
                )
            outputs[identity] = selected
            if selected.exists() or selected.is_symlink():
                raise FileExistsError(f"RPF program output already exists: {selected}")
            detected_game = _detected_gta_root(selected)
            if detected_game is not None:
                raise ValueError(
                    f"RPF package programs only author outside GTA V: {detected_game}"
                )

        for node_id, node in state["nodes"].items():
            config = node["config"]
            if "gta_path" in config:
                gta_roots.add(_configured_path(config["gta_path"], f"{node_id}.gta_path"))
            for key in ("output", "report"):
                if key not in config:
                    continue
                selected = _configured_path(config[key], f"{node_id}.{key}")
                register(selected)
                if key == "output" and node["type"] == "build_rpf":
                    register(RpfArchiveBuilder.validation_path(selected))
                elif key == "output" and node["type"] == "plan_origin":
                    register(selected.with_name(f"{selected.stem}.payload"))
        for selected in outputs.values():
            for gta_root in gta_roots:
                if _inside(selected, gta_root):
                    raise ValueError(
                        "RPF package programs only author outside GTA V; installation "
                        "requires a separate reviewed package or plan action"
                    )
        state["configured_outputs"] = tuple(outputs.values())
        state["gta_roots"] = tuple(gta_roots)
        return state

    @classmethod
    def plan(cls, path: str | Path, destination: str | Path) -> tuple[Path, dict[str, Any]]:
        state = cls._ready_state(path)
        graph_state = RpfPackageGraph.validate(
            state["package_graph"], verify_sources=True,
        )
        output = Path(destination).expanduser().resolve()
        if any(_overlaps(output, item) for item in state["configured_outputs"]):
            raise ValueError("RPF program plan path collides with a configured artifact")
        detected_game = _detected_gta_root(output)
        if detected_game is not None:
            raise ValueError(f"RPF program plan must be outside GTA V: {detected_game}")
        if any(_inside(output, root) for root in state["gta_roots"]):
            raise ValueError("RPF program plan must be outside GTA V")
        plan = {
            "schema_version": 1,
            "operation": "rpf_package_program_plan",
            "status": "ready",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "program": {
                "path": str(state["program"]), "sha256": state["program_sha256"],
            },
            "package_graph": {
                "path": str(state["package_graph"]),
                "sha256": graph_state["graph_sha256"],
            },
            "execution_order": list(state["order"]),
            "nodes": [
                {
                    "id": node_id, "type": state["nodes"][node_id]["type"],
                    "config": state["nodes"][node_id]["config"],
                }
                for node_id in state["order"]
            ],
            "outputs": [str(item) for item in state["configured_outputs"]],
            "safety": {
                "stock_game_files_modified": False,
                "outputs_are_new": True,
                "outputs_outside_gta": True,
                "execution_requires_explicit_acknowledgement": True,
            },
        }
        if _sha256_file(state["program"]) != state["program_sha256"]:
            raise RuntimeError("RPF package program changed during dry-run compilation")
        after_graph = RpfPackageGraph.validate(
            state["package_graph"], verify_sources=True,
        )
        if after_graph["graph_sha256"] != graph_state["graph_sha256"]:
            raise RuntimeError("RPF package graph changed during dry-run compilation")
        _write_json_new(output, plan)
        return output, plan

    @classmethod
    def execute(
        cls, path: str | Path, project_root: str | Path,
        report_path: str | Path,
    ) -> tuple[Path, dict[str, Any]]:
        state = cls._ready_state(path)
        graph_state = RpfPackageGraph.validate(
            state["package_graph"], verify_sources=True,
        )
        report_output = Path(report_path).expanduser().resolve()
        if report_output.suffix.casefold() != ".json":
            raise ValueError("RPF program execution report must use .json")
        if report_output.exists() or report_output.is_symlink():
            raise FileExistsError(f"RPF program execution report exists: {report_output}")
        if any(_overlaps(report_output, item) for item in state["configured_outputs"]):
            raise ValueError(
                "RPF program execution report collides with a configured artifact"
            )
        detected_game = _detected_gta_root(report_output)
        if detected_game is not None:
            raise ValueError(
                f"RPF program execution report must be outside GTA V: {detected_game}"
            )
        if any(_inside(report_output, root) for root in state["gta_roots"]):
            raise ValueError("RPF program execution report must be outside GTA V")
        project = Path(project_root).resolve()
        artifacts: dict[str, Any] = {}
        created_files: list[Path] = []
        created_directories: list[Path] = []
        node_results: list[dict[str, Any]] = []
        try:
            for node_id in state["order"]:
                node = state["nodes"][node_id]
                node_type, config = node["type"], node["config"]
                parent = state["incoming"].get(node_id)
                input_value = artifacts.get(parent) if parent else None
                if node_type == "package_source":
                    result: Any = state["package_graph"]
                elif node_type == "validate_graph":
                    RpfPackageGraph.validate(input_value, verify_sources=True)
                    result = Path(input_value)
                elif node_type == "materialize_tree":
                    result = RpfPackageGraph.materialize(input_value, config["output"])
                    created_directories.append(result)
                elif node_type == "build_rpf":
                    builder = RpfArchiveBuilder(project, config["gta_path"])
                    archive, validation = RpfPackageGraph.build(
                        input_value, builder, config["output"],
                    )
                    created_files.extend((archive, validation))
                    result = archive
                elif node_type == "defragment_rpf":
                    service = RpfExplorerService(project, config["gta_path"])
                    archive, verification, _evidence = service.defragment_verified_copy(
                        service.index(input_value), config["output"], config["report"],
                    )
                    created_files.extend((archive, verification))
                    result = archive
                elif node_type == "plan_origin":
                    builder = RpfArchiveBuilder(project, config["gta_path"])
                    plan, payloads = RpfPackageGraph.plan_origin_changes(
                        input_value, builder, builder.service, config["output"],
                    )
                    created_files.append(plan)
                    created_directories.append(payloads)
                    result = plan
                elif node_type == "artifact_output":
                    result = input_value
                else:  # pragma: no cover - normalized node types are exhaustive
                    raise RuntimeError(f"Unhandled RPF program node: {node_type}")
                artifacts[node_id] = result
                node_results.append({
                    "id": node_id, "type": node_type,
                    "artifact": str(result) if result is not None else None,
                })
            if _sha256_file(state["program"]) != state["program_sha256"]:
                raise RuntimeError("RPF package program changed during execution")
            after_graph = RpfPackageGraph.validate(
                state["package_graph"], verify_sources=True,
            )
            if after_graph["graph_sha256"] != graph_state["graph_sha256"]:
                raise RuntimeError("RPF package graph changed during execution")
            report = {
                "schema_version": 1,
                "operation": "rpf_package_program_execution",
                "status": "verified",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "program": {
                    "path": str(state["program"]), "sha256": state["program_sha256"],
                },
                "package_graph": {
                    "path": str(state["package_graph"]),
                    "sha256": graph_state["graph_sha256"],
                },
                "nodes": node_results,
                "artifacts": [
                    str(path) for path in (*created_files, *created_directories)
                ],
                "safety": {
                    "stock_game_files_modified": False,
                    "outputs_outside_gta": True,
                    "program_unchanged": True,
                    "package_graph_unchanged": True,
                    "failure_cleanup_enabled": True,
                },
            }
            _write_json_new(report_output, report)
            return report_output, report
        except Exception:
            report_output.unlink(missing_ok=True)
            for path in reversed(created_files):
                path.unlink(missing_ok=True)
            for path in reversed(created_directories):
                if path.is_dir():
                    shutil.rmtree(path)
            raise

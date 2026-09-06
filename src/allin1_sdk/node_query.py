"""Bounded read-only node discovery shared by Python, CLI and agent callers."""
from __future__ import annotations

import re
from pathlib import Path

from allin1_sdk.automation import inspect_authoring


def node_category(node, mode="kind", findings=()):
    if mode == "none":
        return "plain"
    if mode == "findings":
        levels = {f["severity"].lower() for f in findings if f.get("node_id") == node["id"]}
        return "error" if levels & {"error", "critical", "fatal"} else "warning" if levels & {"warning", "warn"} else "info" if levels else "neutral"
    kind = node["type"]
    if kind in {"archive", "sealed_archive"}:
        return "archive"
    if kind == "directory":
        return "directory"
    if kind in {"vehicle", "handling", "material", "texture_binding", "archetype"} or kind.startswith("vehicle_"):
        return "relationship"
    if kind != "file":
        return "step"
    extension = (node.get("name") or node.get("source") or "").rsplit(".", 1)[-1].lower()
    if extension in {"yft", "ydr", "ydd", "ybn"}:
        return "model"
    if extension in {"ytd", "dds", "png", "jpg", "jpeg"}:
        return "texture"
    if extension in {"meta", "xml", "json", "toml", "ymt", "ytyp", "ymap"}:
        return "metadata"
    return "language" if extension == "gxt2" else "other"


CATEGORIES = ("archive", "directory", "model", "texture", "metadata", "language", "relationship", "step", "other", "error", "warning", "info", "neutral", "plain")


def _natural(value):
    return [(1, int(part)) if part.isdigit() else (0, part.casefold()) for part in re.split(r"(\d+)", value)]


def query_nodes(source: str | Path, *, module="graph", query="", sort="name", color_by="kind", category=None, offset=0, limit=50):
    if module not in {"graph", "program"} or sort not in {"name", "source", "color"} or color_by not in {"kind", "findings", "none"}:
        raise ValueError("Unknown graph module, sorting or color mode")
    if category is not None and category not in CATEGORIES:
        raise ValueError("Unknown node category")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Use nonnegative offset and a limit between 1 and 200")
    if not isinstance(query, str) or len(query) > 512:
        raise ValueError("Node query must be at most 512 characters")
    session = inspect_authoring({"module": module, "workspace": str(Path(source).absolute())})
    document = session["document"]
    semantic = document.get("semantic") or {}
    nodes = document["nodes"] + semantic.get("entities", [])
    edges = document.get("edges", document.get("links", []))
    findings = semantic.get("findings", [])
    by_id = {node["id"]: node for node in nodes}
    parents = {}
    for edge in edges:
        parents.setdefault(edge.get("child", edge.get("to")), []).append(edge.get("parent", edge.get("from")))
    terms = query.casefold().split()
    candidates, counts = [], {}
    for node in nodes:
        color = node_category(node, color_by, findings)
        counts[color] = counts.get(color, 0) + 1
        text = " ".join(str(node.get(key, "")) for key in ("id", "name", "type", "source")).casefold()
        if (category is None or category == color) and all(term in text for term in terms):
            candidates.append({**node, "category": color})
    def key(node):
        exact = 0 if (node.get("name") or node["id"]).casefold() == query.strip().casefold() else 1
        primary = CATEGORIES.index(node["category"]) if sort == "color" else (node.get("source") or "").casefold() if sort == "source" else ""
        return exact if terms else 0, primary, _natural(node.get("name") or node["id"]), node["id"]
    candidates.sort(key=key)
    result = []
    for node in candidates[offset:offset + limit]:
        ancestry, pending = [], list(parents.get(node["id"], []))
        for parent in pending:
            if parent in ancestry or parent == node["id"] or parent not in by_id:
                continue
            ancestry.append(parent)
            pending.extend(parents.get(parent, []))
        result.append({**node, "ancestor_ids": ancestry})
    return {"schema_version": 1, "operation": "query_node_graph", "module": module,
            "source": session["workspace"], "state_sha256": session["state_sha256"],
            "query": query, "total_nodes": len(nodes), "match_count": len(candidates), "offset": offset,
            "next_offset": offset + len(result) if offset + len(result) < len(candidates) else None,
            "nodes": result, "categories": counts, "issues": session["issues"],
            "read_only": True, "game_write_performed": False,
            "focus_command": "open-rpf-graph" if module == "graph" else "open-rpf-program",
            "focus_parameter": "focus_node", "note": "Use an exact returned node id for desktop focus. Category colors are organizational, not proof of validation."}


def register_command(group):
    import click

    @group.command("query-node-graph")
    @click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--module", type=click.Choice(["graph", "program"]), default="graph")
    @click.option("--query", default="", help="Match name, id, type or source path; exact names sort first.")
    @click.option("--sort", type=click.Choice(["name", "source", "color"]), default="name")
    @click.option("--color-by", type=click.Choice(["kind", "findings", "none"]), default="kind")
    @click.option("--category", type=click.Choice(CATEGORIES))
    @click.option("--offset", type=click.IntRange(0), default=0)
    @click.option("--limit", type=click.IntRange(1, 200), default=50)
    def command(**kwargs):
        """Search/sort/page graph nodes and discover exact ids without opening a GUI."""
        import json
        try:
            click.echo(json.dumps(query_nodes(**kwargs), ensure_ascii=False, allow_nan=False))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
    return command

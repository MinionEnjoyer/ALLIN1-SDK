"""Bounded, read-only REL graph using the pinned native decoder's typed APIs."""
from pathlib import Path
import subprocess
import tempfile

from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
from allin1_sdk.release_paths import strict_json


def analyze(data: bytes):
    if not 0 < len(data) <= 16 * 1024**2:
        raise ValueError("REL relationship XML exceeds the 16 MiB limit")
    helper = project_root() / "tools/RpfPatcher/RpfPatcher.exe"
    if not helper.is_file():
        raise ValueError("REL relationships require the current RpfPatcher native helper")
    with tempfile.TemporaryDirectory(prefix="allin1-rel-relationships-") as temporary:
        xml = Path(temporary) / "source.rel.xml"
        xml.write_bytes(data)
        try:
            result = run_hidden([str(helper), "rel-relationships", str(xml)], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"REL relationship decoder unavailable: {exc}") from exc
    if result.returncode:
        raise ValueError("REL relationship decoder failed: " + (result.stderr or result.stdout or "unknown error")[:500])
    if len(result.stdout) > 8 * 1024**2:
        raise ValueError("REL relationship response exceeds 8 MiB")
    graph = strict_json(result.stdout.encode("utf-8"))
    if (not isinstance(graph, dict) or graph.get("schema_version") != 1 or graph.get("format") != ".rel"
            or graph.get("read_only") is not True or not isinstance(graph.get("nodes"), list)
            or not isinstance(graph.get("edges"), list) or not isinstance(graph.get("warnings"), list)
            or not isinstance(graph.get("scope"), str) or len(graph["nodes"]) > 1000 or len(graph["edges"]) > 1800):
        raise ValueError("Invalid REL relationship decoder evidence; rebuild the native helper")
    for count, items in (("node_count", "nodes"), ("edge_count", "edges")):
        if type(graph.get(count)) is not int or graph[count] < len(graph[items]):
            raise ValueError("REL relationship decoder returned inconsistent counts")
    return graph

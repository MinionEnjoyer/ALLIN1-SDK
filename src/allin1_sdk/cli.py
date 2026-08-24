"""Command-line interface for standalone ALLIN1 SDK workflows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import click

from allin1_sdk.addon_importer import (
    AddonDraftBuilder, AddonPackageInspector, PackageAssetReader,
)
from allin1_sdk.addon_sdk import AddonLinker, AddonManifest, AddonSdkCatalog
from allin1_sdk.binary_workspace import BinaryPatchWorkspace
from allin1_sdk.compiled_render import (
    COMPILED_BACKGROUNDS,
    COMPILED_LIGHT_RIGS,
    COMPILED_RENDER_QUALITIES,
    MAX_COMPILED_RESOLUTION,
    MAX_COMPILED_SAMPLES,
    BLENDER_DEVICES,
    BLENDER_ENGINES,
    CompiledRenderError,
    CompiledRenderSettings,
    compile_vehicle_render,
)
from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.detector import detect_gta_path
from allin1_sdk.dlc_inventory import DlcInventory
from allin1_sdk.meta_tools import diff_meta, validate_meta_roundtrip
from allin1_sdk.model_materials import (
    MaterialAuthoringWorkspace,
    inspect_model_file,
)
from allin1_sdk.native_assets import (
    MAX_NATIVE_PREVIEW_BYTES, MODEL_PREVIEW_SUFFIXES,
    NativeAssetInspector, NativeAssetReport,
)
from allin1_sdk.mods import ModIntegrationService, open_mod_package
from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.package_graph import PackageGraphWorkspace
from allin1_sdk.package_relations import PackageRelationshipAnalyzer
from allin1_sdk.paths import project_root
from allin1_sdk.ped_authoring import PedAuthoringWorkspace
from allin1_sdk.processes import run_hidden
from allin1_sdk.rage_data_compiler import RageVehicleDataCompiler
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_catalog import RpfCatalogService
from allin1_sdk.rpf_change_set import CHANGE_ACTIONS, RpfChangeSet
from allin1_sdk.rpf_delta import derive_rpf_change_plan
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_graph_previews import render_graph_preview_bundle
from allin1_sdk.rpf_program import NODE_SPECS, PROGRAM_TEMPLATES, RpfPackageProgram
from allin1_sdk.rpf_tools import RpfExplorerService, _running_gta_processes
from allin1_sdk.texture_workspace import TextureDictionaryWorkspace
from allin1_sdk.vehicle_project import VehicleProjectResolver
from allin1_sdk.vehicle_package import VehicleAddonPackageBuilder
from allin1_sdk.vehicle_authoring import (
    TUNING_COLLECTIONS,
    VehicleAuthoringWorkspace,
)
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace


PROJECT_ROOT = project_root()


def _manifest(path: Path) -> AddonManifest:
    resolved = path.resolve()
    source = PROJECT_ROOT if resolved.is_relative_to(PROJECT_ROOT) else resolved.parent
    return AddonManifest.load(resolved, source_root=source)


def _game_path(value: Path | None) -> Path:
    game = value.resolve() if value else detect_gta_path()
    if game is None:
        raise click.ClickException("GTA V was not detected; pass --gta-path.")
    return game


def _entry(service: RpfExplorerService, archive: Path, archive_path: str, path: str):
    index = service.index(archive)
    normalized = path.replace("\\", "/").strip("/").casefold()
    matches = [
        item for item in index.entries
        if item.archive_path.casefold() == archive_path.casefold()
        and item.path.casefold() == normalized
    ]
    if len(matches) != 1:
        raise ValueError(
            "Entry was not found uniquely; export an index and use its exact archive/path."
        )
    return index, matches[0]


def _rpf_service(
    gta_path: Path | None, workspace_root: Path | None = None,
) -> RpfExplorerService:
    roots = (workspace_root.resolve(),) if workspace_root else ()
    return RpfExplorerService(PROJECT_ROOT, _game_path(gta_path), workspace_roots=roots)


def _mod_service(gta_path: Path | None) -> ModIntegrationService:
    return ModIntegrationService(_game_path(gta_path))


def _progress(message: str, percent: int) -> None:
    click.echo(f"[{percent:3d}%] {message}")


def _field_assignments(
    assignments: tuple[str, ...], label: str,
) -> dict[str, str]:
    updates: dict[str, str] = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise ValueError(f"{label} assignments must use FIELD=VALUE")
        key, value = assignment.split("=", 1)
        key = key.strip()
        if not key or key in updates:
            raise ValueError(f"Duplicate or empty {label.casefold()} field: {key}")
        updates[key] = value
    return updates


def _open_graph_window(
    graph: Path, gta_path: Path | None = None, focus_node: str | None = None,
) -> int:
    """Start the desktop graph editor without routing paths through a shell."""
    resolved = graph.expanduser().resolve(strict=True)
    state = RpfPackageGraph.validate(resolved, verify_sources=False)
    selected_node: str | None = None
    if focus_node:
        semantic = state.get("semantic") or {}
        candidates = list(state["nodes"].values()) + semantic.get("entities", [])
        matches = [
            item["id"] for item in candidates
            if item["id"].casefold() == focus_node.casefold()
            or item["name"].casefold() == focus_node.casefold()
            or (
                isinstance(item.get("edition"), str)
                and f"{item['name']}@{item['edition']}".casefold()
                == focus_node.casefold()
            )
        ]
        if len(matches) != 1:
            raise ValueError(f"Graph focus was not found uniquely: {focus_node}")
        selected_node = matches[0]
    selected_game = gta_path.expanduser().resolve(strict=True) if gta_path else None

    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        desktop = _frozen_desktop_executable(executable)
        command = [str(desktop), "--rpf-graph", str(resolved)]
    else:
        interpreter = executable
        if os.name == "nt":
            windowed = executable.with_name("pythonw.exe")
            if windowed.is_file():
                interpreter = windowed
        command = [
            str(interpreter), "-m", "allin1_sdk.app", "--rpf-graph", str(resolved),
        ]
    if selected_game is not None:
        command.extend(("--gta-path", str(selected_game)))
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, cwd=PROJECT_ROOT, **options)
    return process.pid


def _open_vehicle_workbench_window(
    source: Path, gta_path: Path | None = None,
) -> tuple[int, int]:
    """Compatibility wrapper for the Vehicles tab of the unified Workbench."""
    pid, counts = _open_workbench_window(source, "vehicles", gta_path)
    return pid, counts["vehicles"]


def _frozen_desktop_executable(executable: Path | None = None) -> Path:
    """Resolve the desktop sibling used by every frozen SDK entry point."""
    current = (executable or Path(sys.executable)).resolve()
    desktop_name = "ALLIN1-SDK-Desktop.exe"
    if current.name.casefold() == desktop_name.casefold():
        return current
    desktop = current.with_name(desktop_name)
    if not desktop.is_file():
        raise FileNotFoundError(f"SDK desktop executable was not found: {desktop}")
    return desktop


def _open_workbench_window(
    source: Path, category: str = "auto", gta_path: Path | None = None,
) -> tuple[int, dict[str, int]]:
    """Validate common add-on content and open it in the desktop Workbench."""
    resolved = source.expanduser().resolve(strict=True)
    scan = AddonPackageInspector().inspect(resolved)
    counts = {
        "vehicles": len(scan.vehicles),
        "weapons": len(scan.weapons),
        "peds": len(scan.peds),
    }
    if category not in {"auto", *counts}:
        raise ValueError(f"Unsupported Workbench category: {category}")
    if not any(counts.values()):
        raise ValueError(
            "The selected package does not contain vehicle, weapon, or ped metadata."
        )
    if category != "auto" and not counts[category]:
        raise ValueError(f"The selected package does not contain {category} metadata.")
    selected_game = gta_path.expanduser().resolve(strict=True) if gta_path else None

    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        desktop = _frozen_desktop_executable(executable)
        command = [str(desktop), "--workbench-package", str(resolved)]
    else:
        interpreter = executable
        if os.name == "nt":
            windowed = executable.with_name("pythonw.exe")
            if windowed.is_file():
                interpreter = windowed
        command = [
            str(interpreter), "-m", "allin1_sdk.app",
            "--workbench-package", str(resolved),
        ]
    command.extend(("--workbench-category", category))
    if selected_game is not None:
        command.extend(("--gta-path", str(selected_game)))
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, cwd=PROJECT_ROOT, **options)
    return process.pid, counts


def _open_model_material_window(
    source: Path, gta_path: Path | None = None,
) -> tuple[int, int]:
    """Validate a model/package and open the dedicated desktop workspace."""
    resolved = source.expanduser().resolve(strict=True)
    if resolved.is_file() and resolved.suffix.casefold() in MODEL_PREVIEW_SUFFIXES:
        model_count = 1
    else:
        scan = AddonPackageInspector().inspect(resolved)
        model_count = sum(
            entry.suffix in MODEL_PREVIEW_SUFFIXES for entry in scan.entries
        )
    if not model_count:
        raise ValueError("The selected source does not contain a YDR, YDD, or YFT model.")
    selected_game = gta_path.expanduser().resolve(strict=True) if gta_path else None
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        desktop = _frozen_desktop_executable(executable)
        command = [str(desktop), "--model-material-source", str(resolved)]
    else:
        interpreter = executable
        if os.name == "nt":
            windowed = executable.with_name("pythonw.exe")
            if windowed.is_file():
                interpreter = windowed
        command = [
            str(interpreter), "-m", "allin1_sdk.app",
            "--model-material-source", str(resolved),
        ]
    if selected_game is not None:
        command.extend(("--gta-path", str(selected_game)))
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, cwd=PROJECT_ROOT, **options)
    return process.pid, model_count


def _open_package_graph_window(
    source: Path, gta_path: Path | None = None,
) -> tuple[int, Path, int, int, bool]:
    """Open or reuse a persistent, provenance-checked package graph."""
    project = PackageGraphWorkspace().import_package(source)
    pid = _open_graph_window(project.graph, gta_path)
    return (
        pid, project.graph, project.member_count,
        project.sealed_rpf_count, project.reused,
    )


def _native_report_payload(
    report: NativeAssetReport, *, source: str, edition: str,
    binding: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "operation": "inspect_native_asset",
        "source": source,
        "edition": edition.title(),
        "name": report.name,
        "suffix": report.suffix,
        "format": report.format_name,
        "size": report.size,
        "sha256": report.sha256,
        "metadata": report.metadata,
        "warnings": list(report.warnings),
        "structured_preview_chars": len(report.structured_text or ""),
        "has_image_preview": report.image_png is not None,
        "output_dir": None,
    }
    if binding:
        payload["binding"] = binding
    return payload


def _publish_native_report(
    report: NativeAssetReport, payload: dict[str, object], output_dir: Path,
    *, safe_overwrite: bool = False,
) -> None:
    destination = output_dir.expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        if not safe_overwrite:
            raise ValueError(f"Native preview output already exists: {destination}")
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("Safe overwrite requires an existing regular report folder")
        marker = destination / "report.json"
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Safe overwrite refused a folder without a readable report.json"
            ) from exc
        if existing.get("operation") != "inspect_rpf_native_entry":
            raise ValueError(
                "Safe overwrite refused a folder not owned by native-entry inspection"
            )
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.allin1-stage-", dir=destination.parent,
    ))
    try:
        outputs: list[str] = []
        if report.structured_text is not None:
            (staging / "structured-preview.txt").write_text(
                report.structured_text, encoding="utf-8",
            )
            outputs.append("structured-preview.txt")
        if report.image_png is not None:
            (staging / "preview.png").write_bytes(report.image_png)
            outputs.append("preview.png")
        payload["output_dir"] = str(destination)
        payload["outputs"] = outputs
        (staging / "report.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8",
        )
        staging.rename(destination)
    except Exception:
        if staging.is_dir():
            shutil.rmtree(staging)
        raise


@click.group()
def main() -> None:
    """Author, audit, and inspect GTA V add-on content."""
    from allin1_sdk.console_entry import configure_utf8_stdio

    configure_utf8_stdio()


@main.command("inspect-source")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--symbol", "symbols", multiple=True, help="Symbol or exact text to retrieve; repeatable.")
@click.option("--context-lines", type=click.IntRange(0, 200), default=16, show_default=True)
def inspect_source_command(source: Path, symbols: tuple[str, ...], context_lines: int) -> None:
    """Inspect bounded source snippets around selected symbols without editing."""
    from allin1_sdk.assistant_evidence import inspect_source

    try:
        payload = inspect_source(source, symbols=symbols, context_lines=context_lines)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@main.command("inspect-log")
@click.argument("log", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--pattern", "patterns", multiple=True, help="Literal telemetry text to retain; repeatable.")
@click.option("--max-lines", type=click.IntRange(1, 1000), default=200, show_default=True)
def inspect_log_command(log: Path, patterns: tuple[str, ...], max_lines: int) -> None:
    """Inspect bounded matching or trailing telemetry lines without editing."""
    from allin1_sdk.assistant_evidence import inspect_log

    try:
        payload = inspect_log(log, patterns=patterns, max_lines=max_lines)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@main.command("compare-telemetry")
@click.argument("baseline", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("current", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def compare_telemetry_command(baseline: Path, current: Path) -> None:
    """Compare numeric key/value telemetry from two text files without editing."""
    from allin1_sdk.assistant_evidence import compare_telemetry

    try:
        payload = compare_telemetry(baseline, current)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@main.command("propose-package-settings")
@click.argument("request", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--root", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--timeout", type=click.FloatRange(1, 600), default=180.0, show_default=True,
)
@click.option(
    "--startup-timeout", type=click.FloatRange(1, 300), default=90.0,
    show_default=True,
)
@click.option(
    "--max-tokens", type=click.IntRange(1, 8192), default=1024,
    show_default=True,
)
@click.option("--no-progress", is_flag=True)
def propose_package_settings_command(
    request: Path, root: Path | None, timeout: float, startup_timeout: float,
    max_tokens: int, no_progress: bool,
) -> None:
    """Ask Qwen for a typed advisory package-settings diff; never apply it."""
    from allin1_sdk.assistant_client import prompt_structured_assistant
    from allin1_sdk.settings_assistant import (
        SETTINGS_PROPOSAL_RESPONSE_FORMAT,
        proposal_prompt,
        validate_proposal_against_request,
        validate_settings_request,
    )

    try:
        host_request = validate_settings_request(request)
        response_format = SETTINGS_PROPOSAL_RESPONSE_FORMAT["json_schema"]
        result = prompt_structured_assistant(
            proposal_prompt(host_request),
            response_schema=response_format["schema"],
            schema_name=response_format["name"],
            root=root, timeout=timeout, startup_timeout=startup_timeout,
            max_tokens=max_tokens,
            progress=(None if no_progress else lambda state: click.echo(
                f"assistant: {state}", err=True,
            )),
        )
        proposal = validate_proposal_against_request(host_request, result.payload)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(proposal, indent=2, ensure_ascii=False))


@main.command("validate-package-settings-proposal")
@click.argument("request", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("proposal", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def validate_package_settings_proposal_command(request: Path, proposal: Path) -> None:
    """Validate a typed advisory diff against its immutable host request."""
    from allin1_sdk.settings_assistant import validate_proposal_against_request

    try:
        normalized = validate_proposal_against_request(request, proposal)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(normalized, indent=2, ensure_ascii=False))


@main.group("assistant")
def assistant_group() -> None:
    """Prompt or inspect the optional local-first SDK assistant."""


@assistant_group.command("status")
@click.option("--root", type=click.Path(file_okay=False, path_type=Path))
def assistant_status_command(root: Path | None) -> None:
    """Show the configured provider without starting a model."""
    from allin1_sdk.assistant_client import assistant_status

    try:
        payload = assistant_status(root)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2))


@assistant_group.command("context")
@click.argument("question", nargs=-1, required=True)
@click.option(
    "--repository-root", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository that owns the current task; defaults to the caller's directory.",
)
@click.option(
    "--workspace-root", "workspace_roots", multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Additional launcher, SDK, or package workspace root.",
)
@click.option(
    "--manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Authoritative mod.toml or addon.json for the package under review.",
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Verified GTA V installation path; never inferred by the model.",
)
@click.option(
    "--operation-mode", type=click.Choice(("advisory", "planning")),
    default="advisory", show_default=True,
)
@click.option(
    "--source", "sources", multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Explicit source file to retrieve; repeatable.",
)
@click.option("--symbol", "symbols", multiple=True, help="Source symbol or text to retrieve; repeatable.")
@click.option(
    "--prioritize", "source_priorities", multiple=True,
    help="Repository relationship evidence: callers, tests, or state-transitions.",
)
@click.option(
    "--telemetry", "telemetry_files", multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Explicit log or telemetry file to retrieve; repeatable.",
)
@click.option("--telemetry-pattern", "telemetry_patterns", multiple=True, help="Telemetry text to retain.")
def assistant_context_command(
    question: tuple[str, ...], repository_root: Path | None,
    workspace_roots: tuple[Path, ...], manifest: Path | None,
    gta_path: Path | None, operation_mode: str, sources: tuple[Path, ...],
    symbols: tuple[str, ...], source_priorities: tuple[str, ...],
    telemetry_files: tuple[Path, ...],
    telemetry_patterns: tuple[str, ...],
) -> None:
    """Show the exact evidence and typed operations supplied to the model."""
    from allin1_sdk.assistant_context import build_assistant_context

    try:
        context = build_assistant_context(
            " ".join(question), repository_root=repository_root,
            workspace_roots=workspace_roots, manifest=manifest, gta_path=gta_path,
            operation_mode=operation_mode, sources=sources, symbols=symbols,
            source_priorities=source_priorities,
            telemetry_files=telemetry_files, telemetry_patterns=telemetry_patterns,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(context.to_dict(), indent=2))


@assistant_group.command("prompt")
@click.argument("prompt", nargs=-1, required=True)
@click.option("--root", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--system-prompt",
    help="Add request-specific guidance; the permanent ALLIN1 policy cannot be replaced.",
)
@click.option(
    "--repository-root", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository that owns the current task; defaults to the caller's directory.",
)
@click.option(
    "--workspace-root", "workspace_roots", multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Additional launcher, SDK, or package workspace root.",
)
@click.option(
    "--manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Authoritative mod.toml or addon.json for the package under review.",
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Verified GTA V installation path; never inferred by the model.",
)
@click.option(
    "--operation-mode", type=click.Choice(("advisory", "planning")),
    default="advisory", show_default=True,
)
@click.option(
    "--source", "sources", multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Explicit source file to retrieve; repeatable.",
)
@click.option("--symbol", "symbols", multiple=True, help="Source symbol or text to retrieve; repeatable.")
@click.option(
    "--prioritize", "source_priorities", multiple=True,
    help="Repository relationship evidence: callers, tests, or state-transitions.",
)
@click.option(
    "--telemetry", "telemetry_files", multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Explicit log or telemetry file to retrieve; repeatable.",
)
@click.option("--telemetry-pattern", "telemetry_patterns", multiple=True, help="Telemetry text to retain.")
@click.option(
    "--timeout", type=click.FloatRange(1, 600), default=180.0, show_default=True,
    help="Maximum completion request time in seconds.",
)
@click.option(
    "--startup-timeout", type=click.FloatRange(1, 300), default=90.0,
    show_default=True, help="Maximum local-runtime startup time in seconds.",
)
@click.option(
    "--max-tokens", type=click.IntRange(1, 8192), default=640,
    show_default=True, help="Maximum response token budget.",
)
@click.option("--json-output", is_flag=True, help="Return response metadata as JSON.")
@click.option("--no-progress", is_flag=True, help="Suppress progress states written to stderr.")
def assistant_prompt_command(
    prompt: tuple[str, ...], root: Path | None, system_prompt: str | None,
    repository_root: Path | None, workspace_roots: tuple[Path, ...],
    manifest: Path | None, gta_path: Path | None, operation_mode: str,
    sources: tuple[Path, ...], symbols: tuple[str, ...],
    source_priorities: tuple[str, ...],
    telemetry_files: tuple[Path, ...], telemetry_patterns: tuple[str, ...],
    timeout: float, startup_timeout: float, max_tokens: int, json_output: bool,
    no_progress: bool,
) -> None:
    """Ask the configured Qwen/compatible model a read-only question."""
    from allin1_sdk.assistant_client import (
        AssistantContextOverflow, DEFAULT_SYSTEM_PROMPT, prompt_assistant,
    )

    try:
        result = prompt_assistant(
            " ".join(prompt), root=root,
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
            timeout=timeout, startup_timeout=startup_timeout,
            max_tokens=max_tokens,
            repository_root=repository_root, workspace_roots=workspace_roots,
            manifest=manifest, gta_path=gta_path, operation_mode=operation_mode,
            sources=sources, symbols=symbols, telemetry_files=telemetry_files,
            telemetry_patterns=telemetry_patterns,
            source_priorities=source_priorities,
            progress=(None if no_progress else lambda state: click.echo(
                f"assistant: {state}", err=True,
            )),
        )
    except AssistantContextOverflow as exc:
        if json_output:
            click.echo(json.dumps(exc.details, indent=2, ensure_ascii=False))
            raise click.exceptions.Exit(2) from exc
        raise click.ClickException(str(exc)) from exc
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        click.echo(result.text)
        click.echo(
            f"\n[{result.model} | {result.mode.replace('_', ' ')} | "
            f"{result.elapsed_seconds:.3f}s]"
        )


@assistant_group.command("review")
@click.argument("question", nargs=-1)
@click.option("--root", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--repository-root", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repository containing the requested source symbols.",
)
@click.option(
    "--source", "sources", multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Optional explicit source file; otherwise exact definitions are discovered.",
)
@click.option(
    "--symbols", "symbol_values", multiple=True, required=True,
    help="Comma-separated requested symbols; option may be repeated.",
)
@click.option(
    "--prioritize", "priority_values", multiple=True,
    default=("callers,tests,state-transitions",), show_default=True,
    help="Comma-separated relationship evidence to prioritize.",
)
@click.option(
    "--format", "output_format", type=click.Choice(("structured",)),
    default="structured", show_default=True,
)
@click.option(
    "--preserve-findings-on-schema-failure", is_flag=True, default=False,
    help="Keep safe read-only prose findings if strict JSON repair fails.",
)
@click.option(
    "--chunk-size", type=click.IntRange(1, 8), default=3, show_default=True,
    help="Maximum requested symbols per grounded inference pass.",
)
@click.option(
    "--timeout", type=click.FloatRange(1, 600), default=180.0, show_default=True,
)
@click.option(
    "--startup-timeout", type=click.FloatRange(1, 300), default=90.0,
    show_default=True,
)
@click.option(
    "--max-tokens", type=click.IntRange(256, 8192), default=1024,
    show_default=True,
)
@click.option("--no-progress", is_flag=True)
def assistant_review_command(
    question: tuple[str, ...], root: Path | None, repository_root: Path | None,
    sources: tuple[Path, ...], symbol_values: tuple[str, ...],
    priority_values: tuple[str, ...], output_format: str,
    preserve_findings_on_schema_failure: bool, chunk_size: int,
    timeout: float, startup_timeout: float, max_tokens: int, no_progress: bool,
) -> None:
    """Run a chunked, repository-grounded multi-symbol code audit."""
    from allin1_sdk.assistant_client import (
        AssistantContextOverflow, review_assistant,
    )

    del output_format  # Click constrains the only currently supported contract.
    symbols = tuple(dict.fromkeys(
        item.strip() for value in symbol_values for item in value.split(",")
        if item.strip()
    ))
    priorities = tuple(dict.fromkeys(
        item.strip() for value in priority_values for item in value.split(",")
        if item.strip()
    ))
    try:
        result = review_assistant(
            symbols, root=root, repository_root=repository_root, sources=sources,
            priorities=priorities, question=" ".join(question), timeout=timeout,
            startup_timeout=startup_timeout, max_tokens=max_tokens,
            chunk_size=chunk_size,
            preserve_findings_on_schema_failure=(
                preserve_findings_on_schema_failure
            ),
            progress=(None if no_progress else lambda state: click.echo(
                f"assistant: {state}", err=True,
            )),
        )
    except AssistantContextOverflow as exc:
        click.echo(json.dumps(exc.details, indent=2, ensure_ascii=False))
        raise click.exceptions.Exit(2) from exc
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


@assistant_group.command("stop")
def assistant_stop_command() -> None:
    """Stop the local model server retained by this SDK process."""
    from allin1_sdk.assistant_client import stop_local_assistant

    click.echo(
        "Local assistant stopped."
        if stop_local_assistant() else "No local assistant runtime is running."
    )


@main.command("agent-api")
@click.option(
    "--allow-game-writes", is_flag=True,
    help=(
        "Permit guarded game/archive commands. Each command still requires its "
        "normal acknowledgement and safety checks."
    ),
)
def agent_api(allow_game_writes: bool) -> None:
    """Serve the structured local AI/developer API over JSONL stdio."""
    from allin1_sdk.agent_api import serve_stdio

    serve_stdio(sys.stdin, sys.stdout, allow_game_writes=allow_game_writes)


@main.command("open-rpf-graph")
@click.argument(
    "graph", type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation for encrypted/native asset previews.",
)
@click.option("--focus-node", help="Select one node id or exact node name on open.")
def open_rpf_graph(
    graph: Path, gta_path: Path | None, focus_node: str | None,
) -> None:
    """Open an RPF package graph in the desktop node editor."""
    try:
        pid = (
            _open_graph_window(graph, gta_path, focus_node)
            if focus_node else _open_graph_window(graph, gta_path)
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "open_rpf_graph", "graph": str(graph.resolve()),
        "focus_node": focus_node, "pid": pid,
    }, indent=2))


@main.command("open-vehicle-workbench")
@click.argument(
    "source", type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation for encrypted/native asset previews.",
)
def open_vehicle_workbench(source: Path, gta_path: Path | None) -> None:
    """Open a vehicle add-on package in the desktop Workbench's Vehicles tab."""
    try:
        pid, model_count = _open_vehicle_workbench_window(source, gta_path)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "open_vehicle_workbench",
        "source": str(source.resolve()),
        "vehicle_models": model_count,
        "pid": pid,
    }, indent=2))


@main.command("open-workbench")
@click.argument(
    "source", type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--category",
    type=click.Choice(("auto", "vehicles", "weapons", "peds"), case_sensitive=False),
    default="auto", show_default=True,
    help="Select a Workbench tab after the package opens.",
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation for encrypted/native asset previews.",
)
def open_workbench(source: Path, category: str, gta_path: Path | None) -> None:
    """Open vehicle, weapon, and ped add-on projects in one desktop workspace."""
    try:
        pid, counts = _open_workbench_window(source, category.casefold(), gta_path)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "open_workbench",
        "source": str(source.resolve()),
        "category": category.casefold(),
        "content": counts,
        "pid": pid,
    }, indent=2))


@main.command("open-model-material-workbench")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation for native model decoding and builds.",
)
def open_model_material_workbench(
    source: Path, gta_path: Path | None,
) -> None:
    """Open a native model or package in the desktop Models & Materials workspace."""
    try:
        pid, model_count = _open_model_material_window(source, gta_path)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "open_model_material_workbench",
        "source": str(source.resolve()),
        "model_assets": model_count,
        "pid": pid,
    }, indent=2))


@main.command("inspect-workbench")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--category",
    type=click.Choice(("all", "vehicles", "weapons", "peds"), case_sensitive=False),
    default="all", show_default=True,
    help="Limit the structured report to one Workbench content family.",
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation; auto-detected when omitted.",
)
def inspect_workbench(
    source: Path, category: str, gta_path: Path | None,
) -> None:
    """Return the Workbench's linked vehicle, weapon, and ped evidence as JSON."""
    try:
        resolved = source.expanduser().resolve(strict=True)
        scan = AddonPackageInspector(
            PROJECT_ROOT, _game_path(gta_path),
        ).inspect(resolved)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    selected = category.casefold()
    payload: dict[str, object] = {
        "operation": "inspect_workbench",
        "source": str(resolved),
        "source_kind": scan.source_kind,
        "category": selected,
        "summary": {
            "vehicles": len(scan.vehicles),
            "weapons": len(scan.weapons),
            "weapon_components": len(scan.weapon_components),
            "weapon_enhancements": len(scan.weapon_enhancements),
            "scripted_weapon_systems": len(scan.scripted_weapon_systems),
            "peds": len(scan.peds),
            "rpf_archives": len(scan.rpf_archives),
            "rpf_native_assets": len(scan.rpf_native_assets),
            "material_progressions": len(scan.material_progressions),
            "errors": scan.error_count,
            "warnings": scan.warning_count,
        },
        "findings": [asdict(item) for item in scan.findings],
    }
    if selected in {"all", "vehicles"}:
        payload["vehicles"] = [asdict(item) for item in scan.vehicles]
        payload["handling"] = [asdict(item) for item in scan.handlings]
        payload["variations"] = [asdict(item) for item in scan.variations]
        payload["tuning_kits"] = [asdict(item) for item in scan.kits]
    if selected in {"all", "weapons"}:
        payload["weapons"] = [asdict(item) for item in scan.weapons]
        payload["ammo"] = [asdict(item) for item in scan.ammo]
        payload["weapon_components"] = [
            asdict(item) for item in scan.weapon_components
        ]
        payload["weapon_component_links"] = [
            asdict(item) for item in scan.weapon_component_links
        ]
        payload["weapon_enhancements"] = [
            item.to_dict() for item in scan.weapon_enhancements
        ]
        payload["scripted_weapon_systems"] = [
            asdict(item) for item in scan.scripted_weapon_systems
        ]
        payload["material_progressions"] = [
            item.to_dict() for item in scan.material_progressions
        ]
        payload["animation_weapons"] = list(scan.animation_weapons)
        payload["shop_weapons"] = list(scan.shop_weapons)
    if selected in {"all", "peds"}:
        payload["peds"] = [asdict(item) for item in scan.peds]
    if selected == "all":
        payload["rpf_archives"] = [asdict(item) for item in scan.rpf_archives]
        payload["rpf_native_assets"] = [
            asdict(item) for item in scan.rpf_native_assets
        ]
    click.echo(json.dumps(payload, indent=2))


@main.command("open-package-graph")
@click.argument(
    "source", type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation for encrypted/native asset previews.",
)
def open_package_graph(source: Path, gta_path: Path | None) -> None:
    """Open or reuse a complete persistent mod-package node graph."""
    try:
        pid, graph, member_count, sealed_rpfs, reused = _open_package_graph_window(
            source, gta_path,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "open_package_graph",
        "source": str(source.resolve()),
        "graph": str(graph),
        "package_members": member_count,
        "sealed_rpf_nodes": sealed_rpfs,
        "workspace_reused": reused,
        "pid": pid,
    }, indent=2))


@main.command("import-package-graph")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--workspace-root", type=click.Path(file_okay=False, path_type=Path),
    help="Optional retained project root; defaults to the SDK user-data directory.",
)
def import_package_graph(source: Path, workspace_root: Path | None) -> None:
    """Create or reuse a persistent, provenance-checked package node graph."""
    try:
        project = PackageGraphWorkspace(workspace_root).import_package(source)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "import_package_graph", "source": str(project.source),
        "workspace": str(project.workspace), "graph": str(project.graph),
        "package_fingerprint": project.package_fingerprint,
        "package_members": project.member_count,
        "sealed_rpf_nodes": project.sealed_rpf_count,
        "workspace_reused": project.reused,
    }, indent=2))


@main.command("list-installed-packages")
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA V installation; auto-detected when omitted.",
)
def list_installed_packages(gta_path: Path | None) -> None:
    """List receipt-backed mod packages installed in a GTA V edition."""
    packages = _mod_service(gta_path).list_installed()
    if not packages:
        click.echo("No managed packages are installed.")
        return
    for package in packages:
        state = "enabled" if package.enabled else "disabled"
        click.echo(
            f"{package.mod_id}\t{package.version}\t{state}\t{package.name}"
        )


@main.command("validate-package")
@click.argument(
    "manifest", type=click.Path(exists=True, path_type=Path),
)
def validate_package(manifest: Path) -> None:
    """Validate a mod.toml, package folder, or bounded ZIP package."""
    try:
        with open_mod_package(manifest) as package:
            payload = {
                "valid": True, "manifest": str(package.manifest_path),
                "schema_version": package.schema_version,
                "id": package.mod_id, "name": package.name, "version": package.version,
                "type": package.mod_type, "editions": list(package.editions),
                "dependencies": list(package.dependencies), "files": len(package.files),
                "rpf_entries": len(package.rpf_entries),
                "allin1_extension": package.extension is not None,
            }
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2))


@main.command("inspect-package-receipt")
@click.argument("mod_id")
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA V installation; auto-detected when omitted.",
)
def inspect_package_receipt(mod_id: str, gta_path: Path | None) -> None:
    """Inspect one validated managed-package receipt without changing GTA V."""
    try:
        receipt = _mod_service(gta_path).inspect_receipt(mod_id)
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(receipt, indent=2))


@main.command("verify-package-ownership")
@click.argument("mod_id")
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA V installation; auto-detected when omitted.",
)
def verify_package_ownership(mod_id: str, gta_path: Path | None) -> None:
    """Verify receipt-owned files, backups, and RPF entries without mutation."""
    try:
        report = _mod_service(gta_path).verify_ownership(mod_id)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, indent=2))
    if not report["healthy"]:
        raise SystemExit(1)


@main.command("install-package")
@click.argument(
    "manifest", type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA V installation; auto-detected when omitted.",
)
@click.option(
    "--acknowledge-write", is_flag=True,
    help="Confirm that validated package files may be installed or backed up.",
)
def install_package(
    manifest: Path, gta_path: Path | None, acknowledge_write: bool,
) -> None:
    """Install a validated manifest, package folder, or bounded ZIP package."""
    if not acknowledge_write:
        raise click.ClickException(
            "Package installation requires --acknowledge-write."
        )
    running = _running_gta_processes()
    if running:
        raise click.ClickException(
            "Close GTA V before installing a package: " + ", ".join(running)
        )
    with open_mod_package(manifest) as package:
        status = _mod_service(gta_path).install(package)
    click.echo(
        f"Installed {status.name} {status.version} ({status.mod_id}); "
        "receipt and rollback ownership verified."
    )


@main.command("uninstall-package")
@click.argument("mod_id")
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA V installation; auto-detected when omitted.",
)
@click.option(
    "--acknowledge-write", is_flag=True,
    help="Confirm that the receipt-owned files may be removed or restored.",
)
def uninstall_package(
    mod_id: str, gta_path: Path | None, acknowledge_write: bool,
) -> None:
    """Uninstall one managed package using its verified receipt and backups."""
    if not acknowledge_write:
        raise click.ClickException(
            "Package uninstall requires --acknowledge-write."
        )
    running = _running_gta_processes()
    if running:
        raise click.ClickException(
            "Close GTA V before uninstalling a package: " + ", ".join(running)
        )
    package_id = mod_id.strip().casefold()
    service = _mod_service(gta_path)
    installed = {item.mod_id: item for item in service.list_installed()}
    package = installed.get(package_id)
    if package is None:
        raise click.ClickException(f"Managed package is not installed: {package_id}")
    service.uninstall(package_id)
    click.echo(f"Uninstalled {package.name} ({package_id}) and applied its receipt rollback.")


@main.command("list")
def list_examples() -> None:
    """List bundled SDK example manifests."""
    for item in AddonSdkCatalog(PROJECT_ROOT).discover():
        click.echo(f"{item.addon_id:<32} {item.version:<10} {item.name}")


@main.command("validate")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def validate(manifest: Path) -> None:
    """Validate an addon.json and its cross-file links."""
    report = AddonLinker().link(_manifest(manifest))
    click.echo(
        f"{'PASS' if report.valid else 'FAIL'}: {len(report.manifest.nodes)} nodes, "
        f"{sum(item.valid for item in report.references)}/{len(report.references)} references, "
        f"{report.error_count} errors, {report.warning_count} warnings"
    )
    for issue in report.issues:
        subject = f" [{issue.subject}]" if issue.subject else ""
        click.echo(f"{issue.severity.upper()} {issue.code}{subject}: {issue.message}")
    if not report.valid:
        raise SystemExit(1)


@main.command("link")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def link(manifest: Path, output: Path) -> None:
    """Write a linked integration and install-plan report."""
    report = AddonLinker().link(_manifest(manifest))
    destination = output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report.to_markdown(), encoding="utf-8")
    click.echo(f"Wrote {'passing' if report.valid else 'failing'} report: {destination}")
    if not report.valid:
        raise SystemExit(1)


@main.command("import-package")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
def import_package(source: Path, output: Path | None) -> None:
    """Scan a folder/archive and generate a review-only addon.json draft."""
    try:
        source = source.resolve()
        scan = AddonPackageInspector().inspect(source)
        click.echo(
            f"Scanned {len(scan.entries)} files ({scan.total_bytes} bytes): "
            f"{', '.join(scan.package_kinds)}; {scan.edition_tag}"
        )
        for finding in scan.findings:
            location = f" [{finding.path}]" if finding.path else ""
            click.echo(
                f"{finding.severity.upper()} {finding.code}{location}: {finding.message}"
            )
        if not scan.valid:
            raise ValueError("Package contains safety errors; no SDK draft was written.")
        destination = output.resolve() if output else (
            source / "addon.json" if source.is_dir()
            else source.with_name(f"{source.stem}.addon.json")
        )
        if source.is_dir() and destination.parent != source:
            raise ValueError("Loose-folder drafts must stay at the package root.")
        written = AddonDraftBuilder().build(scan).write(destination)
        report = AddonLinker().link(AddonManifest.load(
            written, source_root=source if source.is_dir() else written.parent,
        ))
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Wrote review-only SDK draft: {written}\n"
        f"Draft linker: {report.error_count} errors, {report.warning_count} warnings"
    )


@main.command("audit-folder")
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
@click.option("--draft-dir", type=click.Path(file_okay=False, path_type=Path))
def audit_folder(folder: Path, output: Path, draft_dir: Path | None) -> None:
    """Audit all supported packages in a staging folder."""
    supported = {".oiv", ".zip", ".rar", ".7z"}
    packages = sorted(
        (item for item in folder.resolve().iterdir()
         if item.is_file() and item.suffix.casefold() in supported),
        key=lambda item: item.name.casefold(),
    )
    partials = sorted(
        item for item in folder.resolve().iterdir()
        if item.is_file() and item.name.casefold().endswith(".crdownload")
    )
    if not packages and not partials:
        raise click.ClickException("Folder contains no supported package archives")
    rows: list[dict[str, object]] = []
    for package in packages:
        try:
            scan = AddonPackageInspector().inspect(package)
            if draft_dir:
                draft_root = draft_dir.resolve()
                draft_root.mkdir(parents=True, exist_ok=True)
                safe_name = "".join(
                    value if value.isalnum() or value in "._-" else "-"
                    for value in package.stem
                ).strip("-.") or "package"
                AddonDraftBuilder().build(scan).write(
                    draft_root / f"{safe_name}.addon.json"
                )
            rows.append({
                "package": package.name,
                "status": "review" if scan.valid else "unsafe",
                "edition": scan.edition_tag,
                "kinds": list(scan.package_kinds),
                "files": len(scan.entries),
                "warnings": scan.warning_count,
                "errors": scan.error_count,
            })
        except (OSError, ValueError) as exc:
            rows.append({
                "package": package.name, "status": "scan error", "error": str(exc),
            })
    rows.extend({
        "package": partial.name,
        "status": "incomplete download",
        "error": "Browser download has not completed; it was not scanned.",
    } for partial in partials)
    destination = output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# ALLIN1 SDK package audit", ""]
    for row in rows:
        lines.extend([
            f"## {row['package']}", "",
            f"- Status: **{str(row['status']).upper()}**",
            f"- Edition: {row.get('edition', 'unresolved')}",
            f"- Package shapes: {', '.join(row.get('kinds', [])) or 'unresolved'}",
            f"- Files: {row.get('files', 0)}",
            f"- Findings: {row.get('errors', 0)} errors / {row.get('warnings', 0)} warnings",
            "- imported_draft_requires_review: generated drafts are never install-ready",
            f"- Error: {row['error']}" if 'error' in row else "",
            "",
        ])
    destination.write_text("\n".join(line for line in lines if line != "") + "\n", encoding="utf-8")
    destination.with_suffix(".json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    click.echo(f"Audited {len(rows)} package(s): {destination}")


@main.command("oiv-plan")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
@click.option("--managed-package", type=click.Path(file_okay=False, path_type=Path))
@click.option("--rpf-batches", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--created-rpf-package", type=click.Path(file_okay=False, path_type=Path),
    help="Build verified createIfNotExist archives into a managed package.",
)
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
def oiv_plan(
    source: Path, output: Path, managed_package: Path | None,
    rpf_batches: Path | None, created_rpf_package: Path | None,
    gta_path: Path | None,
) -> None:
    """Preview an OIV recipe without executing it."""
    try:
        plan = OivWorkbench().inspect(source)
        written = plan.write_report(output)
        if managed_package:
            OivWorkbench().export_managed_package(plan, managed_package)
        if created_rpf_package:
            OivWorkbench().export_created_rpf_package(
                plan, created_rpf_package, project_root=PROJECT_ROOT,
                gta_path=_game_path(gta_path),
            )
        batch_manifests = (
            OivWorkbench().export_rpf_batch_manifests(plan, rpf_batches)
            if rpf_batches else ()
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    state = (
        "managed export ready" if plan.managed_exportable
        else "created RPF export ready" if plan.created_archive_operations
        and plan.translatable
        else "verified XML compile ready" if plan.xml_compilable
        else "verified RPF recipe compile ready" if plan.rpf_recipe_compilable
        else "atomic RPF export ready" if plan.translatable
        else "manual review required"
    )
    click.echo(
        f"Wrote OIV plan ({state}): {written}"
        + (
            f"; {len(batch_manifests)} atomic RPF batch manifest(s)"
            if batch_manifests else ""
        )
    )


@main.command("compile-oiv-xml")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Explicitly authorize an external archive workspace for the inert plan.",
)
def compile_oiv_xml(
    source: Path, archive: Path, output: Path, gta_path: Path | None,
    workspace_root: Path | None,
) -> None:
    """Compile official OIV XML commands into a verified inert RPF plan."""
    try:
        workbench = OivWorkbench()
        recipe = workbench.inspect(source)
        plan, audit = workbench.compile_xml_rpf_bundle(
            recipe, archive, output,
            service=_rpf_service(gta_path, workspace_root),
        )
        authored = json.loads(plan.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Compiled {len(recipe.xml_operations)} OIV XML operation(s); "
        f"wrote {authored['status']} inert RPF plan: {plan}"
    )
    click.echo(f"Canonical XML verification audit: {audit}")


@main.command("compile-oiv-recipe")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Explicitly authorize an external archive workspace for the inert plan.",
)
def compile_oiv_recipe(
    source: Path, archive: Path, output: Path, gta_path: Path | None,
    workspace_root: Path | None,
) -> None:
    """Compile guarded OIV XML, text, and PSO commands into an inert RPF plan."""
    try:
        workbench = OivWorkbench()
        recipe = workbench.inspect(source)
        plan, audit = workbench.compile_rpf_recipe_bundle(
            recipe, archive, output,
            service=_rpf_service(gta_path, workspace_root),
        )
        authored = json.loads(plan.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Compiled {len(recipe.xml_operations)} XML and "
        f"{len(recipe.text_operations)} bounded text operation(s) and "
        f"{len(recipe.pso_operations)} native PSO operation(s); wrote "
        f"{authored['status']} inert RPF plan: {plan}"
    )
    click.echo(f"Structured recipe verification audit: {audit}")


@main.command("inspect-rpf")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
def inspect_rpf(archive: Path, gta_path: Path | None, output: Path | None) -> None:
    """Write the helper's human-readable RPF inventory."""
    if archive.suffix.casefold() != ".rpf":
        raise click.ClickException("inspect-rpf requires a loose .rpf archive")
    game = _game_path(gta_path)
    patcher = PROJECT_ROOT / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    if not patcher.is_file():
        raise click.ClickException(
            "RpfPatcher.exe is missing; run runtools.ps1 to build the SDK helper."
        )
    completed = run_hidden(
        [patcher, "inspect", game, archive.resolve()], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "unknown helper error").strip()
        raise click.ClickException(f"RPF inspection failed: {detail}")
    if output is None:
        click.echo(completed.stdout, nl=False)
    else:
        destination = output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(completed.stdout, encoding="utf-8")
        click.echo(f"Wrote RPF inventory: {destination}")


@main.command("dlc-inventory")
@click.argument("gta_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def dlc_inventory(gta_path: Path, output: Path) -> None:
    """Inventory DLC folders and registrations."""
    try:
        report = DlcInventory(PROJECT_ROOT).scan(gta_path)
        written = report.write(output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"{report.edition}: {len(report.packs)} DLC packages, "
        f"{report.issue_count} findings. Wrote: {written}"
    )


@main.command("compile-vehicle-data")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False, path_type=Path))
def compile_vehicle_data(source: Path, output_dir: Path) -> None:
    """Join vehicle metadata, assets, and registration data."""
    try:
        report = RageVehicleDataCompiler().compile(source)
        written = report.write_bundle(output_dir)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Compiled {len(report.vehicles)} vehicles into {written[-1].parent}")


@main.command("inspect-vehicle-project")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--model", help="Return one vehicle model instead of the full project.")
def inspect_vehicle_project(source: Path, model: str | None) -> None:
    """Resolve a package's vehicle models, assets, and metadata links."""
    try:
        project = VehicleProjectResolver().inspect(source)
        payload: dict[str, object] = project.to_dict()
        if model:
            payload = {
                "schema_version": payload["schema_version"],
                "source": payload["source"],
                "source_kind": payload["source_kind"],
                "edition": payload["edition"],
                "inventory_fingerprint": payload["inventory_fingerprint"],
                "model": project.model(model).to_dict(),
            }
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2))


@main.command("export-vehicle-project")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output-dir", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def export_vehicle_project(source: Path, output_dir: Path) -> None:
    """Publish a portable vehicle asset project and relationship report."""
    try:
        project = VehicleProjectResolver().inspect(source)
        manifest = project.write(output_dir)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Exported {len(project.models)} vehicle projects: {manifest}"
    )


@main.command("build-vehicle-package")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output-dir", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option("--pack-name", help="DLC pack folder name; inferred when omitted.")
@click.option("--mod-id", help="Managed package id; inferred when omitted.")
@click.option("--name", "package_name", help="Player-facing package name.")
@click.option("--version", default="1.0.0", show_default=True)
@click.option(
    "--edition", "editions", multiple=True,
    type=click.Choice(("legacy", "enhanced"), case_sensitive=False),
    help="Supported game edition; repeat to select both. Defaults to both.",
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Required only when compiling a dlc.rpf.source directory.",
)
def build_vehicle_package(
    source: Path,
    output_dir: Path,
    pack_name: str | None,
    mod_id: str | None,
    package_name: str | None,
    version: str,
    editions: tuple[str, ...],
    gta_path: Path | None,
) -> None:
    """Publish a vehicle DLC as a validated, installable ALLIN1 package."""
    try:
        result = VehicleAddonPackageBuilder(PROJECT_ROOT, gta_path).build(
            source, output_dir, pack_name=pack_name, mod_id=mod_id,
            name=package_name, version=version,
            editions=editions or ("legacy", "enhanced"),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("create-vehicle-authoring")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output-dir", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def create_vehicle_authoring(source: Path, output_dir: Path) -> None:
    """Copy visible vehicle DLC source into a safe editable workspace."""
    try:
        workspace = VehicleAuthoringWorkspace.create(source, output_dir)
        project = workspace.inspect()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "workspace": str(workspace.root),
        "content_root": str(workspace.source),
        "revision": workspace.revision,
        "models": [item.model for item in project.models],
    }, indent=2))


@main.command("inspect-vehicle-authoring")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--model", help="Include editable values for one vehicle model.")
def inspect_vehicle_authoring(workspace: Path, model: str | None) -> None:
    """Inspect a vehicle authoring workspace and its current validation state."""
    try:
        authoring = VehicleAuthoringWorkspace(workspace)
        project = authoring.inspect()
        payload: dict[str, object] = {
            "workspace": str(authoring.root),
            "content_root": str(authoring.source),
            "revision": authoring.revision,
            "validation": project.to_dict(),
        }
        if model:
            payload["authoring"] = authoring.values(model).to_dict()
            payload["appearance"] = authoring.appearance(model).to_dict()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2))


@main.command("set-vehicle-fields")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Editable field assignment as FIELD=VALUE; repeat for multiple fields.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_vehicle_fields(
    workspace: Path, model: str, assignments: tuple[str, ...],
    acknowledge_edit: bool,
) -> None:
    """Transactionally update copied vehicle metadata and revalidate its links."""
    del acknowledge_edit
    updates: dict[str, str] = {}
    try:
        for assignment in assignments:
            if "=" not in assignment:
                raise ValueError("Vehicle field assignments must use FIELD=VALUE")
            key, value = assignment.split("=", 1)
            key = key.strip()
            if not key or key in updates:
                raise ValueError(f"Duplicate or empty vehicle field assignment: {key}")
            updates[key] = value
        result = VehicleAuthoringWorkspace(workspace).update(model, updates)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-vehicle-appearance")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.option(
    "--colors-json", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="JSON array of {indices: [...], liveries: [...]} color sets.",
)
@click.option("--kits", help="Comma-separated linked tuning-kit names; empty clears.")
@click.option("--light-settings", type=int)
@click.option("--siren-settings", type=int)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_vehicle_appearance(
    workspace: Path, model: str, colors_json: Path | None, kits: str | None,
    light_settings: int | None, siren_settings: int | None,
    acknowledge_edit: bool,
) -> None:
    """Edit colors, liveries, tuning links, and light/siren selections."""
    del acknowledge_edit
    try:
        colors = None
        if colors_json is not None:
            colors = json.loads(colors_json.read_text(encoding="utf-8"))
            if not isinstance(colors, list):
                raise ValueError("Vehicle colors JSON must contain an array")
        kit_values = None if kits is None else [
            value.strip() for value in kits.split(",") if value.strip()
        ]
        result = VehicleAuthoringWorkspace(workspace).update_appearance(
            model, colors=colors, kits=kit_values,
            light_settings=light_settings, siren_settings=siren_settings,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-vehicle-tuning-kit")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.argument("kit_name")
@click.option("--kit-type")
@click.option("--livery-names", help="Comma-separated livery label hashes; empty clears.")
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_vehicle_tuning_kit(
    workspace: Path, model: str, kit_name: str, kit_type: str | None,
    livery_names: str | None, acknowledge_edit: bool,
) -> None:
    """Edit safe structured fields on an existing linked tuning kit."""
    del acknowledge_edit
    try:
        labels = None if livery_names is None else [
            value.strip() for value in livery_names.split(",") if value.strip()
        ]
        result = VehicleAuthoringWorkspace(workspace).update_tuning_kit(
            model, kit_name, kit_type=kit_type, livery_names=labels,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("inspect-vehicle-tuning")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.option("--kit", "kit_name", help="Linked kit name or numeric kit ID.")
def inspect_vehicle_tuning(
    workspace: Path, model: str, kit_name: str | None,
) -> None:
    """Inspect tuning parts, performance entries, assets, and validation findings."""
    try:
        builder = VehicleAuthoringWorkspace(workspace).tuning_builder(
            model, kit_name,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(builder.to_dict(), indent=2))


@main.command("add-vehicle-tuning-entry")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.argument("kit_name")
@click.argument("collection", type=click.Choice(TUNING_COLLECTIONS))
@click.option(
    "--set", "assignments", multiple=True,
    help="Tuning field as FIELD=VALUE; repeat for multiple fields.",
)
@click.option(
    "--duplicate-index", type=click.IntRange(min=0),
    help="Clone this zero-based entry, applying any --set overrides.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def add_vehicle_tuning_entry(
    workspace: Path, model: str, kit_name: str, collection: str,
    assignments: tuple[str, ...], duplicate_index: int | None,
    acknowledge_edit: bool,
) -> None:
    """Add or duplicate one validated tuning-kit entry."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Tuning")
        result = VehicleAuthoringWorkspace(workspace).add_tuning_entry(
            model, kit_name, collection, updates,
            duplicate_index=duplicate_index,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-vehicle-tuning-entry")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.argument("kit_name")
@click.argument("collection", type=click.Choice(TUNING_COLLECTIONS))
@click.argument("index", type=click.IntRange(min=0))
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Tuning field as FIELD=VALUE; repeat for multiple fields.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_vehicle_tuning_entry(
    workspace: Path, model: str, kit_name: str, collection: str, index: int,
    assignments: tuple[str, ...], acknowledge_edit: bool,
) -> None:
    """Update scalar or array fields on one tuning entry."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Tuning")
        result = VehicleAuthoringWorkspace(workspace).update_tuning_entry(
            model, kit_name, collection, index, updates,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("remove-vehicle-tuning-entry")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.argument("kit_name")
@click.argument("collection", type=click.Choice(TUNING_COLLECTIONS))
@click.argument("index", type=click.IntRange(min=0))
@click.option("--acknowledge-edit", is_flag=True, required=True)
def remove_vehicle_tuning_entry(
    workspace: Path, model: str, kit_name: str, collection: str, index: int,
    acknowledge_edit: bool,
) -> None:
    """Remove one tuning entry while retaining an undo snapshot."""
    del acknowledge_edit
    try:
        result = VehicleAuthoringWorkspace(workspace).remove_tuning_entry(
            model, kit_name, collection, index,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("move-vehicle-tuning-entry")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.argument("kit_name")
@click.argument("collection", type=click.Choice(TUNING_COLLECTIONS))
@click.argument("index", type=click.IntRange(min=0))
@click.argument("new_index", type=click.IntRange(min=0))
@click.option("--acknowledge-edit", is_flag=True, required=True)
def move_vehicle_tuning_entry(
    workspace: Path, model: str, kit_name: str, collection: str,
    index: int, new_index: int, acknowledge_edit: bool,
) -> None:
    """Reorder a tuning entry within its collection."""
    del acknowledge_edit
    try:
        result = VehicleAuthoringWorkspace(workspace).move_tuning_entry(
            model, kit_name, collection, index, new_index,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-vehicle-light-profile")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.argument("profile_id")
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Existing flattened profile field as FIELD=VALUE; repeat as needed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_vehicle_light_profile(
    workspace: Path, model: str, profile_id: str,
    assignments: tuple[str, ...], acknowledge_edit: bool,
) -> None:
    """Edit scalar values on one existing carcols light profile."""
    del acknowledge_edit
    updates: dict[str, str] = {}
    try:
        for assignment in assignments:
            if "=" not in assignment:
                raise ValueError("Light profile assignments must use FIELD=VALUE")
            key, value = assignment.split("=", 1)
            if not key.strip() or key.strip() in updates:
                raise ValueError(f"Duplicate or empty light-profile field: {key}")
            updates[key.strip()] = value
        result = VehicleAuthoringWorkspace(workspace).update_light_profile(
            model, profile_id, updates,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("migrate-vehicle-identity")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("model")
@click.option("--new-model")
@click.option("--new-handling")
@click.option("--acknowledge-edit", is_flag=True, required=True)
def migrate_vehicle_identity(
    workspace: Path, model: str, new_model: str | None,
    new_handling: str | None, acknowledge_edit: bool,
) -> None:
    """Transactionally migrate model/handling references and streamed filenames."""
    del acknowledge_edit
    try:
        result = VehicleAuthoringWorkspace(workspace).migrate_identity(
            model, new_model=new_model, new_handling=new_handling,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("undo-vehicle-edit")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def undo_vehicle_edit(workspace: Path, acknowledge_edit: bool) -> None:
    """Restore the latest vehicle metadata edit from retained local history."""
    del acknowledge_edit
    try:
        result = VehicleAuthoringWorkspace(workspace).undo()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("create-ped-authoring")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output-dir", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def create_ped_authoring(source: Path, output_dir: Path) -> None:
    """Copy visible ped metadata into a safe editable workspace."""
    try:
        workspace = PedAuthoringWorkspace.create(source, output_dir)
        project = workspace.inspect()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "workspace": str(workspace.root),
        "content_root": str(workspace.source),
        "revision": workspace.revision,
        "peds": [item.name for item in project.peds],
    }, indent=2))


@main.command("inspect-ped-authoring")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--ped", help="Include editable values for one ped record.")
def inspect_ped_authoring(workspace: Path, ped: str | None) -> None:
    """Inspect a ped workspace, validation state, and editable values."""
    try:
        authoring = PedAuthoringWorkspace(workspace)
        project = authoring.inspect()
        payload: dict[str, object] = {
            "workspace": str(authoring.root),
            "content_root": str(authoring.source),
            "revision": authoring.revision,
            "validation": project.to_dict(),
        }
        if ped:
            payload["ped_authoring"] = authoring.values(ped).to_dict()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2))


@main.command("plan-ped-clone")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("donor")
@click.option("--ped-name", required=True, help="New ped model identity.")
@click.option(
    "--set", "assignments", multiple=True,
    help="Optional cloned field override as FIELD=VALUE; repeat as needed.",
)
def plan_ped_clone(
    workspace: Path,
    donor: str,
    ped_name: str,
    assignments: tuple[str, ...],
) -> None:
    """Plan a complete donor-based ped record without changing files."""
    try:
        updates = _field_assignments(assignments, "Ped clone") \
            if assignments else {}
        plan = PedAuthoringWorkspace(workspace).plan_ped_clone(
            donor, ped_name=ped_name, updates=updates,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(plan.to_dict(), indent=2))


@main.command("clone-ped-bundle")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("donor")
@click.option("--ped-name", required=True, help="New ped model identity.")
@click.option(
    "--set", "assignments", multiple=True,
    help="Optional cloned field override as FIELD=VALUE; repeat as needed.",
)
@click.option(
    "--expected-revision", required=True, type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option(
    "--plan-sha256", required=True,
    help="Exact digest returned by plan-ped-clone.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def clone_ped_bundle(
    workspace: Path,
    donor: str,
    ped_name: str,
    assignments: tuple[str, ...],
    expected_revision: int,
    plan_sha256: str,
    acknowledge_edit: bool,
) -> None:
    """Apply one reviewed, revision-bound complete ped clone plan."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Ped clone") \
            if assignments else {}
        authoring = PedAuthoringWorkspace(workspace)
        plan = authoring.plan_ped_clone(
            donor, ped_name=ped_name, updates=updates,
        )
        if plan.plan_sha256.casefold() != plan_sha256.strip().casefold():
            raise ValueError(
                "ped clone plan digest mismatch; run plan-ped-clone again "
                "and review the current plan"
            )
        result = authoring.clone_ped_bundle(
            plan,
            expected_revision=expected_revision,
            expected_plan_sha256=plan_sha256,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-ped-fields")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("ped")
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Editable field assignment as FIELD=VALUE; repeat for multiple fields.",
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_ped_fields(
    workspace: Path,
    ped: str,
    assignments: tuple[str, ...],
    expected_revision: int | None,
    acknowledge_edit: bool,
) -> None:
    """Transactionally update copied ped metadata and revalidate the package."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Ped")
        result = PedAuthoringWorkspace(workspace).update(
            ped, updates, expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("migrate-ped-identity")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("ped")
@click.option("--new-name", required=True, help="New ped model identity.")
@click.option(
    "--new-props",
    help="New props identity; defaults to NEW_NAME_p for conventional donors.",
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def migrate_ped_identity(
    workspace: Path,
    ped: str,
    new_name: str,
    new_props: str | None,
    expected_revision: int | None,
    acknowledge_edit: bool,
) -> None:
    """Transactionally migrate ped metadata and owned streamed filenames."""
    del acknowledge_edit
    try:
        result = PedAuthoringWorkspace(workspace).migrate_identity(
            ped,
            new_name=new_name,
            new_props=new_props,
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("undo-ped-edit")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the undo if the copied workspace revision has changed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def undo_ped_edit(
    workspace: Path,
    expected_revision: int | None,
    acknowledge_edit: bool,
) -> None:
    """Restore the latest ped metadata edit from retained local history."""
    del acknowledge_edit
    try:
        result = PedAuthoringWorkspace(workspace).undo(
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("create-weapon-authoring")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output-dir", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def create_weapon_authoring(source: Path, output_dir: Path) -> None:
    """Copy visible weapon metadata into a safe editable workspace."""
    try:
        workspace = WeaponAuthoringWorkspace.create(source, output_dir)
        project = workspace.inspect()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "workspace": str(workspace.root),
        "content_root": str(workspace.source),
        "revision": workspace.revision,
        "weapons": [item.name for item in project.weapons],
        "components": [item.name for item in project.components],
    }, indent=2))


@main.command("inspect-weapon-authoring")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--weapon", help="Include editable values for one weapon record.")
@click.option("--component", help="Include editable values for one component record.")
def inspect_weapon_authoring(
    workspace: Path, weapon: str | None, component: str | None,
) -> None:
    """Inspect a weapon workspace, relationships, and editable values."""
    try:
        authoring = WeaponAuthoringWorkspace(workspace)
        project = authoring.inspect()
        payload: dict[str, object] = {
            "workspace": str(authoring.root),
            "content_root": str(authoring.source),
            "revision": authoring.revision,
            "validation": project.to_dict(),
        }
        if weapon:
            payload["weapon_authoring"] = authoring.values(weapon).to_dict()
        if component:
            payload["component_authoring"] = authoring.component_values(
                component,
            ).to_dict()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2))


@main.command("plan-weapon-clone")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("donor")
@click.option("--weapon-name", required=True, help="New WEAPON_ identity.")
@click.option("--slot", required=True, help="New SLOT_ identity.")
@click.option(
    "--ammo-info", required=True,
    help="Ammo identity referenced by the cloned weapon.",
)
@click.option("--model", required=True, help="New weapon model identity.")
@click.option(
    "--human-name-hash", required=True,
    help="New player-facing weapon label key.",
)
@click.option("--stat-name", required=True, help="New weapon stat identity.")
@click.option(
    "--ammo-mode", type=click.Choice(("clone", "reuse"), case_sensitive=False),
    default="clone", show_default=True,
    help="Clone the donor ammo definition or reference an existing one.",
)
@click.option(
    "--ammo-name",
    help="New ammo-definition identity when --ammo-mode is clone.",
)
def plan_weapon_clone(
    workspace: Path,
    donor: str,
    weapon_name: str,
    slot: str,
    ammo_info: str,
    model: str,
    human_name_hash: str,
    stat_name: str,
    ammo_mode: str,
    ammo_name: str | None,
) -> None:
    """Plan a complete donor-based weapon bundle without changing files."""
    try:
        plan = WeaponAuthoringWorkspace(workspace).plan_weapon_clone(
            donor,
            weapon_name=weapon_name,
            slot=slot,
            ammo_info=ammo_info,
            model=model,
            human_name_hash=human_name_hash,
            stat_name=stat_name,
            clone_ammo=ammo_mode.casefold() == "clone",
            ammo_name=ammo_name,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(plan.to_dict(), indent=2))


@main.command("clone-weapon-bundle")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("donor")
@click.option("--weapon-name", required=True, help="New WEAPON_ identity.")
@click.option("--slot", required=True, help="New SLOT_ identity.")
@click.option(
    "--ammo-info", required=True,
    help="Ammo identity referenced by the cloned weapon.",
)
@click.option("--model", required=True, help="New weapon model identity.")
@click.option(
    "--human-name-hash", required=True,
    help="New player-facing weapon label key.",
)
@click.option("--stat-name", required=True, help="New weapon stat identity.")
@click.option(
    "--ammo-mode", type=click.Choice(("clone", "reuse"), case_sensitive=False),
    default="clone", show_default=True,
    help="Clone the donor ammo definition or reference an existing one.",
)
@click.option(
    "--ammo-name",
    help="New ammo-definition identity when --ammo-mode is clone.",
)
@click.option(
    "--expected-revision", required=True, type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option(
    "--plan-sha256", required=True,
    help="Exact digest returned by plan-weapon-clone.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def clone_weapon_bundle(
    workspace: Path,
    donor: str,
    weapon_name: str,
    slot: str,
    ammo_info: str,
    model: str,
    human_name_hash: str,
    stat_name: str,
    ammo_mode: str,
    ammo_name: str | None,
    expected_revision: int,
    plan_sha256: str,
    acknowledge_edit: bool,
) -> None:
    """Apply one reviewed, revision-bound complete weapon clone plan."""
    del acknowledge_edit
    try:
        authoring = WeaponAuthoringWorkspace(workspace)
        plan = authoring.plan_weapon_clone(
            donor,
            weapon_name=weapon_name,
            slot=slot,
            ammo_info=ammo_info,
            model=model,
            human_name_hash=human_name_hash,
            stat_name=stat_name,
            clone_ammo=ammo_mode.casefold() == "clone",
            ammo_name=ammo_name,
        )
        actual_digest = str(plan.to_dict().get("plan_sha256", ""))
        if actual_digest.casefold() != plan_sha256.strip().casefold():
            raise ValueError(
                "weapon clone plan digest mismatch; run plan-weapon-clone "
                "again and review the current plan"
            )
        result = authoring.clone_weapon_bundle(
            plan,
            expected_revision=expected_revision,
            expected_plan_sha256=plan_sha256,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-weapon-fields")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("weapon")
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Editable weapon/ammo field as FIELD=VALUE; repeat as needed.",
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option(
    "--acknowledge-shared", is_flag=True,
    help="Confirm edits to ammo used by more than one weapon.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_weapon_fields(
    workspace: Path,
    weapon: str,
    assignments: tuple[str, ...],
    expected_revision: int | None,
    acknowledge_shared: bool,
    acknowledge_edit: bool,
) -> None:
    """Transactionally edit an existing weapon and its linked ammo record."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Weapon")
        result = WeaponAuthoringWorkspace(workspace).update(
            weapon,
            updates,
            expected_revision=expected_revision,
            acknowledge_shared=acknowledge_shared,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-weapon-component")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("component")
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Editable component field as FIELD=VALUE; repeat as needed.",
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option(
    "--acknowledge-shared", is_flag=True,
    help="Confirm edits to a component used by more than one weapon.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_weapon_component(
    workspace: Path,
    component: str,
    assignments: tuple[str, ...],
    expected_revision: int | None,
    acknowledge_shared: bool,
    acknowledge_edit: bool,
) -> None:
    """Transactionally edit one existing weapon-component definition."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Weapon component")
        result = WeaponAuthoringWorkspace(workspace).update_component(
            component,
            updates,
            expected_revision=expected_revision,
            acknowledge_shared=acknowledge_shared,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-weapon-attachment")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("weapon")
@click.argument("component")
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Attachment field as FIELD=VALUE; repeat as needed.",
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_weapon_attachment(
    workspace: Path,
    weapon: str,
    component: str,
    assignments: tuple[str, ...],
    expected_revision: int | None,
    acknowledge_edit: bool,
) -> None:
    """Edit one existing weapon-to-component attachment link."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Weapon attachment")
        result = WeaponAuthoringWorkspace(workspace).update_attachment(
            weapon,
            component,
            updates,
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("inspect-weapon-animation")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("weapon")
@click.option(
    "--source",
    help="Exact relative metadata member when more than one source has mappings.",
)
def inspect_weapon_animation(
    workspace: Path, weapon: str, source: str | None,
) -> None:
    """Inspect exact animation-set coverage retained for one weapon."""
    try:
        authoring = WeaponAuthoringWorkspace(workspace)
        values = authoring.animation_values(weapon, source=source)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "workspace": str(authoring.root),
        "revision": authoring.revision,
        "animation": values.to_dict(),
    }, indent=2))


@main.command("clone-weapon-animation")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("weapon")
@click.option("--template", required=True, help="Mapped weapon to clone exactly.")
@click.option(
    "--source",
    help="Exact relative metadata member when the template has multiple sources.",
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def clone_weapon_animation(
    workspace: Path,
    weapon: str,
    template: str,
    source: str | None,
    expected_revision: int | None,
    acknowledge_edit: bool,
) -> None:
    """Clone complete native animation mappings without editing clip payloads."""
    del acknowledge_edit
    try:
        result = WeaponAuthoringWorkspace(workspace).clone_animation_mappings(
            weapon,
            template,
            source=source,
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("inspect-weapon-shop")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("weapon")
@click.option(
    "--source",
    help="Exact relative metadata member when more than one shop source matches.",
)
def inspect_weapon_shop(
    workspace: Path, weapon: str, source: str | None,
) -> None:
    """Inspect a weapon's exact existing storefront record and representations."""
    try:
        authoring = WeaponAuthoringWorkspace(workspace)
        values = authoring.shop_values(weapon, source=source)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "workspace": str(authoring.root),
        "revision": authoring.revision,
        "shop": values.to_dict(),
    }, indent=2))


@main.command("set-weapon-shop-fields")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("weapon")
@click.option(
    "--set", "assignments", multiple=True, required=True,
    help="Editable existing shop field as FIELD=VALUE; repeat as needed.",
)
@click.option(
    "--source",
    help="Exact relative metadata member when more than one shop source matches.",
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the edit if the copied workspace revision has changed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_weapon_shop_fields(
    workspace: Path,
    weapon: str,
    assignments: tuple[str, ...],
    source: str | None,
    expected_revision: int | None,
    acknowledge_edit: bool,
) -> None:
    """Transactionally edit supported fields on an existing shop record."""
    del acknowledge_edit
    try:
        updates = _field_assignments(assignments, "Weapon shop")
        result = WeaponAuthoringWorkspace(workspace).update_shop(
            weapon,
            updates,
            source=source,
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("undo-weapon-edit")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--expected-revision", type=click.IntRange(min=0),
    help="Reject the undo if the copied workspace revision has changed.",
)
@click.option("--acknowledge-edit", is_flag=True, required=True)
def undo_weapon_edit(
    workspace: Path,
    expected_revision: int | None,
    acknowledge_edit: bool,
) -> None:
    """Restore the latest weapon metadata edit from retained local history."""
    del acknowledge_edit
    try:
        result = WeaponAuthoringWorkspace(workspace).undo(
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("index-rpf")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def index_rpf(archive: Path, gta_path: Path | None, output: Path) -> None:
    """Export a structured recursive RPF index."""
    try:
        index = RpfExplorerService(PROJECT_ROOT, _game_path(gta_path)).index(archive)
        json_path, csv_path = index.export(output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Indexed {len(index.entries)} entries across {len(index.archives)} archive(s): "
        f"{json_path} and {csv_path}"
    )


@main.command("catalog-rpfs")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--refresh", is_flag=True,
    help="Re-index every archive instead of reusing unchanged cached indexes.",
)
def catalog_rpfs(
    source: Path, gta_path: Path | None, output: Path, refresh: bool,
) -> None:
    """Build or incrementally refresh a global loose-RPF search catalog."""
    try:
        database, summary = RpfCatalogService(
            PROJECT_ROOT, _game_path(gta_path),
        ).build(source, output, refresh=refresh, progress=_progress)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Cataloged {summary['archives']} archive(s): {summary['indexed']} indexed, "
        f"{summary['cached']} cached, {summary['failed']} failed; {database}"
    )


@main.command("search-rpf-catalog")
@click.argument("catalog", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("query", required=False, default="")
@click.option("--kind", default="")
@click.option("--suffix", default="")
@click.option("--limit", default=250, type=int, show_default=True)
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def search_rpf_catalog(
    catalog: Path, query: str, kind: str, suffix: str, limit: int,
    output: Path | None,
) -> None:
    """Search a global RPF catalog by archive, nested path, or entry name."""
    try:
        results = RpfCatalogService.search(
            catalog, query, kind=kind, suffix=suffix, limit=limit,
        )
        report = (
            RpfCatalogService.export_results(results, output, query=query)
            if output else None
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    for item in results[:100] if not output else ():
        click.echo(
            f"{item.outer_archive} :: {item.archive_path or 'root'} :: "
            f"{item.entry_path} [{item.kind}, {item.size:,} bytes]"
        )
    click.echo(
        f"Found {len(results)} RPF catalog result(s)"
        + (f": {report}" if report else "")
    )


@main.command("build-rpf-tree")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
def build_rpf_tree(source: Path, gta_path: Path | None, output: Path) -> None:
    """Create and exactly verify a new RPF, including *.rpf.source subtrees."""
    try:
        archive, report = RpfArchiveBuilder(
            PROJECT_ROOT, _game_path(gta_path),
        ).build(source, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built and exactly verified new RPF: {archive}")
    click.echo(f"Validation report: {report}")


@main.command("create-rpf-graph")
@click.argument(
    "source", required=False,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--root-name", default="", help="Root archive name ending in .rpf.")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
def create_rpf_graph(source: Path | None, root_name: str, output: Path) -> None:
    """Create an empty or folder-imported visual RPF package graph."""
    try:
        if source is None:
            if not root_name:
                raise ValueError("An empty RPF graph requires --root-name")
            graph = RpfPackageGraph.create_empty(root_name, output)
        else:
            graph = RpfPackageGraph.create_from_folder(
                source, output, root_name=root_name,
            )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created validated RPF package graph: {graph}")


@main.command("import-rpf-graph")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(file_okay=False, path_type=Path))
def import_rpf_graph(archive: Path, gta_path: Path | None, output: Path) -> None:
    """Expand an existing recursive RPF into an external visual graph workspace."""
    try:
        service = RpfExplorerService(PROJECT_ROOT, _game_path(gta_path))
        graph = RpfPackageGraph.import_archive(
            service.index(archive), service, output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Imported existing RPF into external graph workspace: {graph}")
    click.echo("Source archive unchanged; import report: " + str(graph.parent / "rpf-graph-import.json"))


@main.command("render-rpf-graph-previews")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA V installation for edition-aware native asset decoding.",
)
@click.option(
    "--limit", type=click.IntRange(1, 2500), default=2500, show_default=True,
    help="Maximum number of file-node previews to render.",
)
@click.option(
    "--output", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def render_rpf_graph_previews(
    graph: Path, gta_path: Path | None, limit: int, output: Path,
) -> None:
    """Render a hash-bound portable preview bundle for graph asset nodes."""
    try:
        bundle, report = render_graph_preview_bundle(
            graph, output, PROJECT_ROOT,
            game_path=gta_path.resolve() if gta_path is not None else None,
            limit=limit,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Rendered verified RPF graph asset previews: {bundle}")
    click.echo(f"Preview report: {report}")


def _write_rpf_graph_report(report: dict, output: Path | None) -> None:
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if output is None:
        click.echo(rendered, nl=False)
        return
    destination = output.resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"RPF graph report already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    click.echo(f"Wrote validated RPF graph report: {destination}")


@main.command("inspect-rpf-graph")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def inspect_rpf_graph(graph: Path, output: Path | None) -> None:
    """Inspect nodes, edges, source hashes, and summary for one package graph."""
    try:
        _write_rpf_graph_report(RpfPackageGraph.describe(graph), output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("validate-rpf-graph")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def validate_rpf_graph(graph: Path, output: Path | None) -> None:
    """Validate the complete graph tree and every referenced source hash."""
    try:
        report = RpfPackageGraph.describe(graph)
        _write_rpf_graph_report({
            "schema_version": report["schema_version"],
            "operation": "rpf_package_graph_validation",
            "status": report["status"], "graph": report["graph"],
            "graph_sha256": report["graph_sha256"], "root": report["root"],
            "summary": report["summary"],
        }, output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _require_rpf_graph_edit_acknowledgement(acknowledged: bool) -> None:
    if not acknowledged:
        raise click.ClickException(
            "RPF graph mutations require --acknowledge-edit; referenced source files "
            "and game archives remain unchanged"
        )


@main.command("add-rpf-graph-container")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("parent_id")
@click.argument("name")
@click.option("--archive", is_flag=True, help="Create a nested RPF node.")
@click.option("--x", default=0.0, type=float, show_default=True)
@click.option("--y", default=0.0, type=float, show_default=True)
@click.option("--acknowledge-edit", is_flag=True)
def add_rpf_graph_container(
    graph: Path, parent_id: str, name: str, archive: bool,
    x: float, y: float, acknowledge_edit: bool,
) -> None:
    """Add a directory or nested archive below a graph parent node."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        node_id = RpfPackageGraph.add_container(
            graph, parent_id, name, archive=archive, x=x, y=y,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Added RPF graph {'archive' if archive else 'directory'} node: {node_id}")


@main.command("add-rpf-graph-file")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("parent_id")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", default="", help="Authored name inside the RPF.")
@click.option("--x", default=0.0, type=float, show_default=True)
@click.option("--y", default=0.0, type=float, show_default=True)
@click.option("--acknowledge-edit", is_flag=True)
def add_rpf_graph_file(
    graph: Path, parent_id: str, source: Path, name: str,
    x: float, y: float, acknowledge_edit: bool,
) -> None:
    """Add a source-hashed file below a graph parent node."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        node_id = RpfPackageGraph.add_file(
            graph, parent_id, source, name=name, x=x, y=y,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Added RPF graph file node: {node_id}")


@main.command("expand-rpf-graph-sealed")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def expand_rpf_graph_sealed(
    graph: Path, node_id: str, gta_path: Path | None, acknowledge_edit: bool,
) -> None:
    """Expand one immutable package RPF into retained editable graph nodes."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        service = RpfExplorerService(
            PROJECT_ROOT, _game_path(gta_path),
            workspace_roots=(graph.resolve().parent,),
        )
        report = RpfPackageGraph.expand_sealed_archive(
            graph, node_id, service,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "expand_rpf_graph_sealed", "graph": str(graph.resolve()),
        **report,
    }, indent=2))


@main.command("analyze-package-graph")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def analyze_package_graph(graph: Path, output: Path | None) -> None:
    """Resolve and persist typed vehicle relationships in a package graph."""
    try:
        report = PackageRelationshipAnalyzer.analyze(graph)
        _write_rpf_graph_report({
            "operation": "analyze_package_graph", "graph": str(graph.resolve()),
            **report,
        }, output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("inspect-package-graph-relations")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def inspect_package_graph_relations(graph: Path, output: Path | None) -> None:
    """Inspect persisted vehicle links and relationship findings."""
    try:
        report = PackageRelationshipAnalyzer.inspect(graph)
        _write_rpf_graph_report({
            "operation": "inspect_package_graph_relations",
            "graph": str(graph.resolve()), **report,
        }, output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("rename-rpf-graph-node")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.argument("name")
@click.option("--acknowledge-edit", is_flag=True)
def rename_rpf_graph_node(
    graph: Path, node_id: str, name: str, acknowledge_edit: bool,
) -> None:
    """Rename one graph node with sibling collision validation."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageGraph.rename_node(graph, node_id, name)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Renamed RPF graph node: {node_id}")


@main.command("reparent-rpf-graph-node")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.argument("parent_id")
@click.option("--acknowledge-edit", is_flag=True)
def reparent_rpf_graph_node(
    graph: Path, node_id: str, parent_id: str, acknowledge_edit: bool,
) -> None:
    """Reconnect a graph node to a validated archive or directory parent."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageGraph.reparent_node(graph, node_id, parent_id)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Reparented RPF graph node {node_id} under {parent_id}")


@main.command("position-rpf-graph-node")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.option("--acknowledge-edit", is_flag=True)
def position_rpf_graph_node(
    graph: Path, node_id: str, x: float, y: float, acknowledge_edit: bool,
) -> None:
    """Persist one node's visual canvas position."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageGraph.set_position(graph, node_id, x, y)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Positioned RPF graph node {node_id} at {x}, {y}")


@main.command("layout-rpf-graph")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--x-spacing", default=300.0, type=float, show_default=True)
@click.option("--y-spacing", default=112.0, type=float, show_default=True)
@click.option("--acknowledge-edit", is_flag=True)
def layout_rpf_graph(
    graph: Path, x_spacing: float, y_spacing: float, acknowledge_edit: bool,
) -> None:
    """Apply a deterministic readable tree layout to all graph nodes."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        count = RpfPackageGraph.auto_layout(
            graph, x_spacing=x_spacing, y_spacing=y_spacing,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Positioned {count} RPF graph node(s) with deterministic tree layout")


@main.command("remove-rpf-graph-node")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.option("--acknowledge-edit", is_flag=True)
def remove_rpf_graph_node(
    graph: Path, node_id: str, acknowledge_edit: bool,
) -> None:
    """Remove a graph node and its descendants without deleting source files."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        removed = RpfPackageGraph.remove_node(graph, node_id)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Removed {len(removed)} RPF graph node(s); source files unchanged")


@main.command("refresh-rpf-graph-sources")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def refresh_rpf_graph_sources(graph: Path, acknowledge_edit: bool) -> None:
    """Explicitly accept current size/hash values for changed graph sources."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        changed = RpfPackageGraph.refresh_sources(graph)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Refreshed {changed} changed RPF graph source record(s)")


@main.command("materialize-rpf-graph")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(file_okay=False, path_type=Path))
def materialize_rpf_graph(graph: Path, output: Path) -> None:
    """Create a new provenance-safe loose tree with nested *.rpf.source folders."""
    try:
        written = RpfPackageGraph.materialize(graph, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Materialized verified RPF graph source tree: {written}")


@main.command("build-rpf-graph")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
def build_rpf_graph(graph: Path, gta_path: Path | None, output: Path) -> None:
    """Materialize, build, exactly verify, and bind a graph-authored RPF."""
    try:
        archive, report = RpfPackageGraph.build(
            graph, RpfArchiveBuilder(PROJECT_ROOT, _game_path(gta_path)), output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built graph-authored and exactly verified RPF: {archive}")
    click.echo(f"Graph-bound validation report: {report}")


@main.command("plan-rpf-graph-origin")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
def plan_rpf_graph_origin(graph: Path, gta_path: Path | None, output: Path) -> None:
    """Build/diff an imported graph and emit an inert plan against its origin."""
    try:
        selected_game = _game_path(gta_path)
        builder = RpfArchiveBuilder(PROJECT_ROOT, selected_game)
        plan, payloads = RpfPackageGraph.plan_origin_changes(
            graph, builder, builder.service, output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created reviewed RPF graph origin plan: {plan}")
    click.echo(f"Retained desired archive, validation, and payloads: {payloads}")
    click.echo("Origin archive unchanged; applying remains a separate guarded action")


def _program_config(value: str) -> dict:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid RPF program config JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise click.ClickException("RPF program config JSON must be an object")
    return payload


@main.command("create-rpf-program")
@click.argument("graph", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--template", type=click.Choice(sorted(PROGRAM_TEMPLATES)),
    default="validate", show_default=True,
)
def create_rpf_program(graph: Path, output: Path, template: str) -> None:
    """Create a typed visual build program bound to one RPF package graph."""
    try:
        program = RpfPackageProgram.create(graph, output, template=template)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created RPF package node program: {program}")
    click.echo(
        f"Template: {PROGRAM_TEMPLATES[template]['title']} ({template}); "
        "configure any incomplete operation nodes before planning"
    )


@main.command("list-rpf-program-templates")
def list_rpf_program_templates() -> None:
    """List reusable visual RPF package program templates as JSON."""
    click.echo(json.dumps({
        "schema_version": 1,
        "operation": "rpf_package_program_templates",
        "templates": [
            {
                "id": template_id,
                "title": spec["title"],
                "description": spec["description"],
                "operation_nodes": len(spec["nodes"]),
            }
            for template_id, spec in PROGRAM_TEMPLATES.items()
        ],
    }, indent=2, ensure_ascii=False))


@main.command("inspect-rpf-program")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--verify-graph", is_flag=True, help="Hash every package source file.")
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def inspect_rpf_program(program: Path, verify_graph: bool, output: Path | None) -> None:
    """Inspect typed nodes, links, readiness issues, and execution order."""
    try:
        report = RpfPackageProgram.describe(program, verify_graph=verify_graph)
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if output is None:
            click.echo(rendered, nl=False)
        else:
            destination = output.resolve()
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"RPF program report already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
            click.echo(f"Wrote RPF program inspection: {destination}")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("add-rpf-program-node")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_type", type=click.Choice(sorted(
    item for item in NODE_SPECS if item != "package_source"
)))
@click.option("--config-json", default="{}", show_default=True)
@click.option("--x", default=0.0, type=float, show_default=True)
@click.option("--y", default=0.0, type=float, show_default=True)
@click.option("--acknowledge-edit", is_flag=True)
def add_rpf_program_node(
    program: Path, node_type: str, config_json: str,
    x: float, y: float, acknowledge_edit: bool,
) -> None:
    """Add a typed operation node; the package and game remain unchanged."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        node_id = RpfPackageProgram.add_node(
            program, node_type, config=_program_config(config_json), x=x, y=y,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Added RPF program node: {node_id} ({node_type})")


@main.command("configure-rpf-program-node")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.argument("config_json")
@click.option("--acknowledge-edit", is_flag=True)
def configure_rpf_program_node(
    program: Path, node_id: str, config_json: str, acknowledge_edit: bool,
) -> None:
    """Replace one operation node's validated JSON configuration."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageProgram.configure_node(
            program, node_id, _program_config(config_json),
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Configured RPF program node: {node_id}")


@main.command("connect-rpf-program-nodes")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("from_node")
@click.argument("to_node")
@click.option("--acknowledge-edit", is_flag=True)
def connect_rpf_program_nodes(
    program: Path, from_node: str, to_node: str, acknowledge_edit: bool,
) -> None:
    """Connect typed artifact/output pins and replace the target input link."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageProgram.connect(program, from_node, to_node)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Connected RPF program nodes: {from_node} -> {to_node}")


@main.command("disconnect-rpf-program-node")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.option("--acknowledge-edit", is_flag=True)
def disconnect_rpf_program_node(
    program: Path, node_id: str, acknowledge_edit: bool,
) -> None:
    """Disconnect one node's typed input without removing the node."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageProgram.disconnect(program, node_id)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Disconnected RPF program node input: {node_id}")


@main.command("position-rpf-program-node")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.argument("x", type=float)
@click.argument("y", type=float)
@click.option("--acknowledge-edit", is_flag=True)
def position_rpf_program_node(
    program: Path, node_id: str, x: float, y: float, acknowledge_edit: bool,
) -> None:
    """Persist one operation node's canvas position."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageProgram.set_position(program, node_id, x, y)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Positioned RPF program node {node_id} at {x}, {y}")


@main.command("layout-rpf-program")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def layout_rpf_program(program: Path, acknowledge_edit: bool) -> None:
    """Apply deterministic left-to-right layout to the operation graph."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        count = RpfPackageProgram.auto_layout(program)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Positioned {count} RPF program node(s)")


@main.command("remove-rpf-program-node")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("node_id")
@click.option("--acknowledge-edit", is_flag=True)
def remove_rpf_program_node(
    program: Path, node_id: str, acknowledge_edit: bool,
) -> None:
    """Remove one operation node and its links without deleting artifacts."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfPackageProgram.remove_node(program, node_id)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Removed RPF program node: {node_id}; artifacts unchanged")


@main.command("plan-rpf-program")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
def plan_rpf_program(program: Path, output: Path) -> None:
    """Compile and bind a dry-run plan without executing operation nodes."""
    try:
        plan_path, plan = RpfPackageProgram.plan(program, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Compiled ready RPF node program: {len(plan['nodes'])} node(s), "
        f"{len(plan['outputs'])} new output(s); {plan_path}"
    )
    click.echo("No program operation was executed and no game archive was changed")


@main.command("run-rpf-program")
@click.argument("program", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--report", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--acknowledge-execution", is_flag=True,
    help="Acknowledge creation of the program's external authored outputs.",
)
def run_rpf_program(
    program: Path, report: Path, acknowledge_execution: bool,
) -> None:
    """Execute a ready external-authoring graph with exact failure cleanup."""
    if not acknowledge_execution:
        raise click.ClickException(
            "RPF program execution requires --acknowledge-execution; stock/game "
            "archive writes are not part of this command"
        )
    try:
        report_path, result = RpfPackageProgram.execute(
            program, PROJECT_ROOT, report,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Executed and verified {len(result['nodes'])} RPF program node(s); "
        f"{len(result['artifacts'])} artifact(s); {report_path}"
    )
    click.echo("Stock/game archives unchanged")


@main.command("create-rpf-change-set")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
def create_rpf_change_set(
    archive: Path, gta_path: Path | None, output: Path,
) -> None:
    """Create an inert source-bound workspace for staged atomic RPF changes."""
    service = _rpf_service(gta_path)
    try:
        change_set = RpfChangeSet.create(service.index(archive), output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created empty RPF change set: {change_set}")
    click.echo("Source archive unchanged; stage and review actions before compiling a plan")


@main.command("inspect-rpf-change-set")
@click.argument("change_set", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--verify-files", is_flag=True, help="Hash the archive and staged payloads.")
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def inspect_rpf_change_set(
    change_set: Path, verify_files: bool, output: Path | None,
) -> None:
    """Inspect staged actions and optional source/payload verification."""
    try:
        report = RpfChangeSet.describe(change_set, verify_files=verify_files)
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if output is None:
            click.echo(rendered, nl=False)
        else:
            destination = output.resolve()
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"RPF change-set report already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
            click.echo(f"Wrote RPF change-set inspection: {destination}")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("stage-rpf-change")
@click.argument("change_set", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("action", type=click.Choice(sorted(CHANGE_ACTIONS)))
@click.argument("entry")
@click.option("--archive-path", default="", help="Nested RPF path using ! separators.")
@click.option("--payload", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--new-entry", help="Destination path for a rename action.")
@click.option("--acknowledge-edit", is_flag=True)
def stage_rpf_change(
    change_set: Path, action: str, entry: str, archive_path: str,
    payload: Path | None, new_entry: str | None, acknowledge_edit: bool,
) -> None:
    """Stage one inert action in a persistent RPF change-set workspace."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        action_id = RpfChangeSet.stage(
            change_set, action, entry, archive_path=archive_path,
            payload=payload, new_entry=new_entry,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Staged RPF change-set action: {action_id} ({action})")


@main.command("unstage-rpf-change")
@click.argument("change_set", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("action_id")
@click.option("--acknowledge-edit", is_flag=True)
def unstage_rpf_change(
    change_set: Path, action_id: str, acknowledge_edit: bool,
) -> None:
    """Remove one inert action without changing its archive or payload."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfChangeSet.remove(change_set, action_id)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Removed staged RPF action: {action_id}; archive unchanged")


@main.command("move-rpf-change")
@click.argument("change_set", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("action_id")
@click.argument("position", type=click.IntRange(min=1))
@click.option("--acknowledge-edit", is_flag=True)
def move_rpf_change(
    change_set: Path, action_id: str, position: int, acknowledge_edit: bool,
) -> None:
    """Move one staged action to a one-based review position."""
    _require_rpf_graph_edit_acknowledgement(acknowledge_edit)
    try:
        RpfChangeSet.move(change_set, action_id, position)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Moved staged RPF action {action_id} to position {position}")


@main.command("plan-rpf-change-set")
@click.argument("change_set", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Explicitly authorize one external authoring root for this plan.",
)
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
def plan_rpf_change_set(
    change_set: Path, gta_path: Path | None, workspace_root: Path | None,
    output: Path,
) -> None:
    """Compile a verified change set into the normal guarded atomic RPF plan."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        plan_path, plan = RpfChangeSet.compile_plan(change_set, service, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Compiled {len(plan['changes'])} staged action(s) into "
        f"{plan['status']} atomic plan: {plan_path}"
    )
    click.echo("Archive unchanged; application remains a separate reviewed transaction")


@main.command("extract-rpf-entry")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def extract_rpf_entry(
    archive: Path, entry_path: str, archive_path: str,
    gta_path: Path | None, output: Path,
) -> None:
    """Extract one exact root or nested-RPF entry."""
    service = RpfExplorerService(PROJECT_ROOT, _game_path(gta_path))
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        written = service.extract(index, entry, output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Extracted read-only copy: {written}")


@main.command("inspect-rpf-native-entry")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--output-dir", type=click.Path(file_okay=False, path_type=Path),
    help=(
        "Publish a report folder. Include the native extension in its name "
        "(for example asset.ytd.native-report) to prevent basename collisions."
    ),
)
@click.option(
    "--safe-overwrite", is_flag=True,
    help="Replace only an existing report folder created by this command.",
)
def inspect_rpf_native_entry(
    archive: Path, entry_path: str, archive_path: str,
    gta_path: Path | None, output_dir: Path | None, safe_overwrite: bool,
) -> None:
    """Inspect an exact root or nested-RPF asset without modifying its archive."""
    service = _rpf_service(gta_path)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        report, binding = service.inspect_native_entry(index, entry)
        payload = _native_report_payload(
            report, source=entry.virtual_name, edition=index.edition,
            binding=binding,
        )
        payload["operation"] = "inspect_rpf_native_entry"
        if output_dir is not None:
            _publish_native_report(
                report, payload, output_dir, safe_overwrite=safe_overwrite,
            )
        elif safe_overwrite:
            raise ValueError("--safe-overwrite requires --output-dir")
        click.echo(json.dumps(payload, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("export-rpf-native-workspace")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def export_rpf_native_workspace(
    archive: Path, entry_path: str, archive_path: str,
    gta_path: Path | None, output: Path,
) -> None:
    """Extract an RPF native asset into an editable CodeWalker XML workspace."""
    service = _rpf_service(gta_path)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        workspace = service.export_native_workspace(index, entry, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Exported RPF native editing workspace: {workspace}")


@main.command("export-rpf-binary-workspace")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def export_rpf_binary_workspace(
    archive: Path, entry_path: str, archive_path: str,
    gta_path: Path | None, output: Path,
) -> None:
    """Extract an exact RPF entry into an auditable same-size hex workspace."""
    service = _rpf_service(gta_path)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        workspace = service.export_binary_workspace(index, entry, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Exported bound RPF binary workspace: {workspace}")


@main.command("export-rpf-gxt2-workspace")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def export_rpf_gxt2_workspace(
    archive: Path, entry_path: str, archive_path: str,
    gta_path: Path | None, output: Path,
) -> None:
    """Extract an exact GXT2 dictionary into a bound text workspace."""
    service = _rpf_service(gta_path)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        workspace = service.export_gxt2_workspace(index, entry, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Exported bound RPF GXT2 text workspace: {workspace}")


@main.command("extract-rpf-subtree")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--directory", default="",
    help="Directory inside the selected virtual archive; blank exports its root.",
)
@click.option(
    "--archive-path", default="",
    help="Nested RPF path using ! between archive levels; blank means root.",
)
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
def extract_rpf_subtree(
    archive: Path, directory: str, archive_path: str,
    gta_path: Path | None, output: Path,
) -> None:
    """Recursively export one root or nested-RPF directory with a hash manifest."""
    service = RpfExplorerService(PROJECT_ROOT, _game_path(gta_path))
    try:
        index = service.index(archive)
        written = service.extract_subtree(
            index, output, archive_path=archive_path, directory_path=directory,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        "Extracted read-only RPF subtree and verification manifest: "
        f"{written}"
    )


@main.command("diff-rpf")
@click.argument("left", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("right", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--exact-content", is_flag=True,
    help="Extract and hash entries to detect changes hidden by identical metadata.",
)
@click.option(
    "--logical-content", is_flag=True,
    help="Compare canonical RSC7 header + decompressed payload identities.",
)
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def diff_rpf(
    left: Path, right: Path, exact_content: bool, logical_content: bool,
    gta_path: Path | None, output: Path,
) -> None:
    """Compare two recursive RPF trees and export JSON and Markdown reports."""
    if exact_content and logical_content:
        raise click.ClickException(
            "Choose --exact-content or --logical-content, not both"
        )
    service = RpfExplorerService(PROJECT_ROOT, _game_path(gta_path))
    try:
        left_index = service.index(left)
        right_index = service.index(right)
        report = service.compare_indexes(
            left_index, right_index, exact_content=exact_content,
            logical_content=logical_content,
        )
        json_path, markdown_path = service.export_diff(report, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = report["summary"]
    click.echo(
        f"RPF diff: {summary['added']} added, {summary['removed']} removed, "
        f"{summary['modified']} modified; {json_path} and {markdown_path}"
    )


@main.command("derive-rpf-plan")
@click.argument("base", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("desired", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--exact-content", is_flag=True,
    help="Preserve byte-level resource differences instead of ignoring recompression.",
)
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Authorize an isolated external base archive workspace for later apply.",
)
@click.option(
    "--output", "-o", required=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
def derive_rpf_plan(
    base: Path, desired: Path, exact_content: bool,
    gta_path: Path | None, workspace_root: Path | None, output: Path,
) -> None:
    """Derive a guarded plan and changed payloads from before/after RPFs."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        result = derive_rpf_change_plan(
            service, service.index(base), service.index(desired), output,
            exact_content=exact_content, progress=_progress,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    counts = result.plan["derived_delta"]["action_counts"]
    summary = ", ".join(f"{count} {action}" for action, count in counts.items())
    click.echo(
        f"Derived {result.plan['status']} RPF plan with "
        f"{len(result.plan['changes'])} action(s) ({summary}): {result.plan_path}"
    )
    if result.payload_directory is not None:
        click.echo(f"Portable changed payloads: {result.payload_directory}")
    if result.plan["blocking_reasons"]:
        click.echo("Apply remains blocked: " + "; ".join(result.plan["blocking_reasons"]))


@main.command("verify-rpf-archive")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def verify_rpf_archive(
    archive: Path, gta_path: Path | None, output: Path,
) -> None:
    """Verify recursive structure and exact extraction of every RPF payload."""
    service = _rpf_service(gta_path)
    try:
        index = service.index(archive)
        report_path, report = service.verify_archive_integrity(index, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = report["summary"]
    click.echo(
        f"RPF integrity {report['status']}: {summary['archives']} archive(s), "
        f"{summary['payloads_exactly_extracted']} exact payload(s), "
        f"{summary['structural_issues']} structural issue(s); {report_path}"
    )


@main.command("defragment-rpf")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--output", "-o", required=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.option(
    "--report", required=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
def defragment_rpf(
    archive: Path, gta_path: Path | None, output: Path, report: Path,
) -> None:
    """Create a smaller external RPF copy and exactly verify every leaf payload."""
    service = _rpf_service(gta_path)
    try:
        index = service.index(archive)
        written, report_path, result = service.defragment_verified_copy(
            index, output, report,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = result["summary"]
    click.echo(
        f"Verified defragmented RPF copy: {written} · "
        f"{summary['bytes_saved']:,} bytes saved · "
        f"{summary['leaf_payloads_verified']:,} leaf payload(s) exact"
    )
    click.echo(f"Source archive unchanged; verification report: {report_path}")


@main.command("plan-rpf-replacement")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.argument("payload", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_replacement(
    archive: Path, entry_path: str, payload: Path, archive_path: str,
    gta_path: Path | None, workspace_root: Path | None, output: Path,
) -> None:
    """Create a checksummed replacement plan without writing the archive."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        plan = service.replacement_plan(index, entry, payload)
        destination = output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Wrote {plan['status']} plan; no archive was changed: {destination}"
    )


@main.command("plan-rpf-native-workspace")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_native_workspace(
    archive: Path, entry_path: str, workspace: Path, archive_path: str,
    gta_path: Path | None, workspace_root: Path | None, output: Path,
) -> None:
    """Rebuild/reparse a native workspace and create its RPF replacement plan."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        plan, asset, report = service.plan_native_workspace_replacement(
            index, entry, workspace, output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built and reparsed native RPF payload: {asset}")
    click.echo(f"Validation report: {report}")
    click.echo(f"Reviewed replacement plan (archive unchanged): {plan}")


@main.command("plan-rpf-binary-workspace")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_binary_workspace(
    archive: Path, entry_path: str, workspace: Path, archive_path: str,
    gta_path: Path | None, workspace_root: Path | None, output: Path,
) -> None:
    """Build a bound same-size binary diff and create its reviewed RPF plan."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        plan, asset, report = service.plan_binary_workspace_replacement(
            index, entry, workspace, output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built verified binary RPF payload: {asset}")
    click.echo(f"Binary diff report: {report}")
    click.echo(f"Reviewed replacement plan (archive unchanged): {plan}")


@main.command("plan-rpf-gxt2-workspace")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_gxt2_workspace(
    archive: Path, entry_path: str, workspace: Path, archive_path: str,
    gta_path: Path | None, workspace_root: Path | None, output: Path,
) -> None:
    """Rebuild/reparse a bound GXT2 workspace and create its reviewed RPF plan."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        plan, asset, report = service.plan_gxt2_workspace_replacement(
            index, entry, workspace, output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built and reparsed GXT2 RPF payload: {asset}")
    click.echo(f"GXT2 validation report: {report}")
    click.echo(f"Reviewed replacement plan (archive unchanged): {plan}")


@main.command("plan-rpf-add")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.argument("payload", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_add(
    archive: Path, entry_path: str, payload: Path, archive_path: str,
    gta_path: Path | None, workspace_root: Path | None, output: Path,
) -> None:
    """Create a checksummed plan to add a root or nested RPF entry."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        index = service.index(archive)
        plan = service.addition_plan(
            index, entry_path, payload, archive_path=archive_path,
        )
        destination = output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Wrote {plan['status']} add plan; no archive was changed: {destination}")


@main.command("plan-rpf-delete")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_delete(
    archive: Path, entry_path: str, archive_path: str,
    gta_path: Path | None, workspace_root: Path | None, output: Path,
) -> None:
    """Create a checksummed plan to delete a root or nested RPF entry."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        plan = service.deletion_plan(index, entry)
        destination = output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Wrote {plan['status']} delete plan; no archive was changed: {destination}"
    )


@main.command("plan-rpf-batch")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument(
    "change_manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_batch(
    archive: Path, change_manifest: Path, gta_path: Path | None,
    workspace_root: Path | None, output: Path,
) -> None:
    """Plan add/replace/delete/mkdir/rmdir/rename/upsert JSON changes atomically."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        authored = json.loads(change_manifest.read_text(encoding="utf-8"))
        changes = authored.get("changes") if isinstance(authored, dict) else authored
        if not isinstance(changes, list):
            raise ValueError("RPF batch manifest must be a list or contain a changes list")
        resolved_changes = []
        for item in changes:
            if not isinstance(item, dict):
                raise ValueError("Every RPF batch change must be an object")
            normalized = dict(item)
            if normalized.get("payload"):
                payload = Path(str(normalized["payload"])).expanduser()
                if not payload.is_absolute():
                    payload = change_manifest.resolve().parent / payload
                normalized["payload"] = str(payload.resolve())
            resolved_changes.append(normalized)
        plan = service.multi_change_plan(service.index(archive), resolved_changes)
        destination = output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Wrote {plan['status']} atomic plan for {len(plan['changes'])} changes; "
        f"no archive was changed: {destination}"
    )


@main.command("plan-rpf-sync")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument(
    "export_directory", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_sync(
    archive: Path, export_directory: Path, gta_path: Path | None,
    workspace_root: Path | None, output: Path,
) -> None:
    """Plan all file and directory edits in a verified RPF subtree export."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        plan = service.subtree_sync_plan(service.index(archive), export_directory)
        destination = output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Wrote {plan['status']} atomic sync plan for {len(plan['changes'])} changes; "
        f"no archive was changed: {destination}"
    )


@main.command("apply-rpf-plan")
@click.argument("plan", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--receipt-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--acknowledge-write", is_flag=True,
    help="Confirm that GTA V is closed and authorize the guarded mods-copy write.",
)
def apply_rpf_plan(
    plan: Path, gta_path: Path | None, workspace_root: Path | None,
    receipt_dir: Path | None,
    acknowledge_write: bool,
) -> None:
    """Apply a ready RPF plan through backup, staging, verification, and receipt."""
    if not acknowledge_write:
        raise click.ClickException(
            "RPF writes require --acknowledge-write after reviewing the plan"
        )
    service = _rpf_service(gta_path, workspace_root)
    try:
        receipt = service.apply_change_plan(
            plan, receipt_root=receipt_dir, progress=_progress,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Applied and verified RPF transaction. Receipt: {receipt}")


@main.command("verify-rpf-transaction")
@click.argument("receipt", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
def verify_rpf_transaction(
    receipt: Path, gta_path: Path | None, workspace_root: Path | None,
    output: Path | None,
) -> None:
    """Verify a transaction's archive, entry, and rollback snapshot."""
    service = _rpf_service(gta_path, workspace_root)
    try:
        result = service.verify_transaction(receipt)
        rendered = json.dumps(result, indent=2) + "\n"
        if output:
            destination = output.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
        else:
            click.echo(rendered, nl=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if not result["healthy"]:
        raise click.ClickException(
            f"Transaction verification failed ({result['archive_state']})"
        )
    if output:
        click.echo(f"Transaction is healthy ({result['archive_state']}): {destination}")


@main.command("rollback-rpf-transaction")
@click.argument("receipt", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--acknowledge-write", is_flag=True,
    help="Confirm that GTA V is closed and authorize restoration of the snapshot.",
)
def rollback_rpf_transaction(
    receipt: Path, gta_path: Path | None, workspace_root: Path | None,
    acknowledge_write: bool,
) -> None:
    """Roll back an applied receipt if the archive is still transaction-owned."""
    if not acknowledge_write:
        raise click.ClickException(
            "RPF rollback requires --acknowledge-write after reviewing the receipt"
        )
    service = _rpf_service(gta_path, workspace_root)
    try:
        updated = service.rollback_transaction(receipt, progress=_progress)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Rolled back and verified RPF transaction: {updated}")


@main.command("recover-rpf-transaction")
@click.argument("receipt", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--workspace-root", type=click.Path(exists=True, file_okay=False, path_type=Path))
def recover_rpf_transaction(
    receipt: Path, gta_path: Path | None, workspace_root: Path | None,
) -> None:
    """Reconcile an interrupted receipt without committing an archive write."""
    try:
        result = _rpf_service(gta_path, workspace_root).recover_transaction(receipt)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2))


@main.command("list-rpf-transactions")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--receipt-dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
def list_rpf_transactions(
    gta_path: Path | None, receipt_dir: Path | None, output: Path | None,
) -> None:
    """List guarded RPF transaction history, including malformed receipts."""
    try:
        history = _rpf_service(gta_path).list_transactions(receipt_dir)
        rendered = json.dumps(history, indent=2) + "\n"
        if output:
            destination = output.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
            click.echo(f"Wrote {len(history)} transaction record(s): {destination}")
        else:
            click.echo(rendered, nl=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("canary-rpf-transaction")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--acknowledge-write", is_flag=True,
    help="Authorize writes only to a generated disposable copy outside GTA V.",
)
def canary_rpf_transaction(
    archive: Path, gta_path: Path | None, output_dir: Path | None,
    acknowledge_write: bool,
) -> None:
    """Prove real RPF apply/verify/rollback behavior on an isolated archive copy."""
    if not acknowledge_write:
        raise click.ClickException(
            "The disposable canary requires --acknowledge-write; its source remains read-only"
        )
    try:
        report = _rpf_service(gta_path).run_canary(
            archive, output_root=output_dir, progress=_progress,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Real-archive canary passed: {report}")


@main.command("export-native-workspace")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--edition", type=click.Choice(("Legacy", "Enhanced"), case_sensitive=False),
    default="Enhanced", show_default=True,
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA installation used to decrypt encrypted AWC audio streams.",
)
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def export_native_workspace(
    source: Path, edition: str, gta_path: Path | None, output: Path,
) -> None:
    """Export a native resource to an editable XML/dependency workspace."""
    try:
        workspace = NativeAssetInspector(PROJECT_ROOT, gta_path).export_workspace(
            source, output, edition=edition,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Exported verified native editing workspace: {workspace}")


@main.command("inspect-model-materials")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--edition", type=click.Choice(("Legacy", "Enhanced"), case_sensitive=False),
    default="Enhanced", show_default=True,
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation for native decoding; never written.",
)
def inspect_model_materials(
    source: Path, edition: str, gta_path: Path | None,
) -> None:
    """Inspect model hierarchy, shader usage, and typed texture bindings."""
    try:
        project = inspect_model_file(
            PROJECT_ROOT, source, edition=edition, gta_path=gta_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(project.to_dict(), indent=2))


@main.command("create-material-workspace")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output-dir", "-o", required=True,
    type=click.Path(file_okay=False, path_type=Path),
)
@click.option(
    "--edition", type=click.Choice(("Legacy", "Enhanced"), case_sensitive=False),
    default="Enhanced", show_default=True,
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def create_material_workspace(
    source: Path, output_dir: Path, edition: str, gta_path: Path | None,
) -> None:
    """Export one native model into a revisioned material workspace."""
    try:
        workspace = MaterialAuthoringWorkspace.create(
            PROJECT_ROOT, source, output_dir, edition=edition, gta_path=gta_path,
        )
        project = workspace.inspect()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "create_material_workspace",
        "workspace": str(workspace.root),
        "revision": workspace.revision,
        "validation": project.to_dict(),
    }, indent=2))


@main.command("inspect-material-workspace")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def inspect_material_workspace(workspace: Path) -> None:
    """Inspect the exact revision and material state of an editing workspace."""
    try:
        authoring = MaterialAuthoringWorkspace(workspace)
        project = authoring.inspect()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "inspect_material_workspace",
        "workspace": str(authoring.root),
        "revision": authoring.revision,
        "validation": project.to_dict(),
    }, indent=2))


@main.command("set-material-binding")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("material_index", type=click.IntRange(min=0))
@click.option("--shader-name", help="Replace the existing shader Name value.")
@click.option(
    "--texture", "texture_assignments", multiple=True,
    help="Existing sampler assignment as SLOT=TEXTURE; repeat as needed.",
)
@click.option("--expected-revision", required=True, type=click.IntRange(min=0))
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_material_binding(
    workspace: Path, material_index: int, shader_name: str | None,
    texture_assignments: tuple[str, ...], expected_revision: int,
    acknowledge_edit: bool,
) -> None:
    """Edit existing shader and texture values without synthesizing XML nodes."""
    del acknowledge_edit
    try:
        textures = _field_assignments(texture_assignments, "Texture") \
            if texture_assignments else {}
        if shader_name is None and not textures:
            raise ValueError("Provide --shader-name or at least one --texture SLOT=TEXTURE")
        result = MaterialAuthoringWorkspace(workspace).set_material(
            material_index, expected_revision=expected_revision,
            shader_name=shader_name, textures=textures,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("set-geometry-material")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.argument("geometry_index", type=click.IntRange(min=0))
@click.argument("material_index", type=click.IntRange(min=0))
@click.option("--expected-revision", required=True, type=click.IntRange(min=0))
@click.option("--acknowledge-edit", is_flag=True, required=True)
def set_geometry_material(
    workspace: Path, geometry_index: int, material_index: int,
    expected_revision: int, acknowledge_edit: bool,
) -> None:
    """Assign one geometry to an existing shader in its local catalog."""
    del acknowledge_edit
    try:
        result = MaterialAuthoringWorkspace(workspace).set_geometry_material(
            geometry_index, material_index,
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("undo-material-edit")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--expected-revision", required=True, type=click.IntRange(min=0))
@click.option("--acknowledge-edit", is_flag=True, required=True)
def undo_material_edit(
    workspace: Path, expected_revision: int, acknowledge_edit: bool,
) -> None:
    """Restore the last exact material XML snapshot after drift validation."""
    del acknowledge_edit
    try:
        result = MaterialAuthoringWorkspace(workspace).undo(
            expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2))


@main.command("build-material-workspace")
@click.argument(
    "workspace", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def build_material_workspace(
    workspace: Path, gta_path: Path | None, output: Path,
) -> None:
    """Compile edited material XML and reparse it before publication."""
    try:
        asset, report = MaterialAuthoringWorkspace(workspace).build(
            PROJECT_ROOT, output, gta_path=gta_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "operation": "build_material_workspace",
        "workspace": str(workspace.resolve()),
        "output": str(asset),
        "validation_report": str(report),
    }, indent=2))


@main.command("inspect-native-asset")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--edition", type=click.Choice(("Legacy", "Enhanced"), case_sensitive=False),
    default="Enhanced", show_default=True,
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA installation used to decrypt encrypted AWC audio streams.",
)
@click.option(
    "--output-dir", type=click.Path(file_okay=False, path_type=Path),
    help="Publish a new report folder containing any XML/text and PNG preview.",
)
def inspect_native_asset(
    source: Path, edition: str, gta_path: Path | None,
    output_dir: Path | None,
) -> None:
    """Inspect one native asset and optionally publish its bounded preview bundle."""
    try:
        if source.is_symlink():
            raise ValueError("Native inspection source cannot be a symbolic link")
        resolved = source.resolve()
        size = resolved.stat().st_size
        if not 0 < size <= MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Native inspection source is empty or exceeds the 128 MiB limit")
        report = NativeAssetInspector(PROJECT_ROOT, gta_path).inspect_bytes(
            resolved.name, resolved.read_bytes(), edition=edition,
        )
        payload = _native_report_payload(
            report, source=str(resolved), edition=edition,
        )
        if output_dir is not None:
            _publish_native_report(report, payload, output_dir)
        click.echo(json.dumps(payload, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("render-native-model")
@click.argument(
    "source", type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--output", "-o", required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write one verified PNG outside the source package and GTA installation.",
)
@click.option(
    "--edition", type=click.Choice(("Legacy", "Enhanced"), case_sensitive=False),
    default="Enhanced", show_default=True,
)
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Matching GTA installation for native model decoding; never written.",
)
@click.option(
    "--texture-dictionary",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help=(
        "Linked YTD for UV-textured rendering. Vehicle YFTs also discover a safe "
        "same-name sibling automatically (including model_hi.yft → model.ytd)."
    ),
)
@click.option(
    "--blender", type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Optional Blender executable; otherwise use safe runtime detection.",
)
@click.option("--yaw", type=float, default=34.0, show_default=True)
@click.option(
    "--pitch", type=click.FloatRange(-89.0, 89.0), default=18.0,
    show_default=True,
)
@click.option("--lens-mm", type=click.FloatRange(18.0, 200.0), default=52.0, show_default=True)
@click.option("--lod", help="Render one exact decoded LOD; defaults to all LODs.")
@click.option("--component", help="Render one exact drawable component; defaults to all.")
@click.option(
    "--engine", type=click.Choice(tuple(sorted(BLENDER_ENGINES)), case_sensitive=False),
    default="eevee", show_default=True,
)
@click.option(
    "--device", type=click.Choice(tuple(sorted(BLENDER_DEVICES)), case_sensitive=False),
    default="auto", show_default=True,
)
@click.option(
    "--quality", type=click.Choice(
        tuple(sorted(COMPILED_RENDER_QUALITIES)), case_sensitive=False,
    ), default="production", show_default=True,
)
@click.option(
    "--width", type=click.IntRange(256, MAX_COMPILED_RESOLUTION),
    default=1920, show_default=True,
)
@click.option(
    "--height", type=click.IntRange(256, MAX_COMPILED_RESOLUTION),
    default=1080, show_default=True,
)
@click.option(
    "--samples", type=click.IntRange(1, MAX_COMPILED_SAMPLES),
    help="Override the quality preset's engine-specific sample count.",
)
@click.option(
    "--light-rig", type=click.Choice(
        tuple(sorted(COMPILED_LIGHT_RIGS)), case_sensitive=False,
    ), default="studio", show_default=True,
)
@click.option("--light-rotation", type=float, default=0.0, show_default=True)
@click.option(
    "--light-strength", type=click.FloatRange(0.05, 10.0),
    default=1.0, show_default=True,
)
@click.option(
    "--background", type=click.Choice(
        tuple(sorted(COMPILED_BACKGROUNDS)), case_sensitive=False,
    ), default="studio_dark", show_default=True,
)
@click.option("--background-color", default="#111714", show_default=True)
@click.option("--transparent/--opaque", default=False, show_default=True)
@click.option("--ground-plane/--no-ground-plane", default=True, show_default=True)
@click.option("--contact-shadows/--no-contact-shadows", default=True, show_default=True)
def render_native_model(
    source: Path, output: Path, edition: str, gta_path: Path | None,
    texture_dictionary: Path | None, blender: Path | None,
    yaw: float, pitch: float, lens_mm: float,
    lod: str | None, component: str | None, engine: str, device: str,
    quality: str, width: int, height: int, samples: int | None,
    light_rig: str, light_rotation: float, light_strength: float,
    background: str, background_color: str, transparent: bool,
    ground_plane: bool, contact_shadows: bool,
) -> None:
    """Compile one decoded YDR, YDD, or YFT model into an external PNG."""

    try:
        if source.is_symlink():
            raise ValueError("Native render source cannot be a symbolic link")
        resolved_source = source.expanduser().resolve(strict=True)
        if resolved_source.suffix.casefold() not in MODEL_PREVIEW_SUFFIXES:
            choices = ", ".join(sorted(MODEL_PREVIEW_SUFFIXES))
            raise ValueError(f"Native render source must be one of: {choices}")
        size = resolved_source.stat().st_size
        if not 0 < size <= MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Native render source is empty or exceeds the 128 MiB limit")
        resolved_game = gta_path.expanduser().resolve(strict=True) if gta_path else None
        resolved_texture: Path | None = None
        if texture_dictionary is not None:
            if texture_dictionary.is_symlink():
                raise ValueError("Texture dictionary cannot be a symbolic link")
            resolved_texture = texture_dictionary.expanduser().resolve(strict=True)
        else:
            stem = resolved_source.stem
            texture_stem = stem[:-3] if stem.casefold().endswith("_hi") else stem
            candidate = resolved_source.with_name(texture_stem + ".ytd")
            if candidate.is_file() and not candidate.is_symlink():
                resolved_texture = candidate.resolve(strict=True)
            else:
                # Package archives preserve authored filename casing.  On a
                # case-sensitive host, MODEL_HI.YFT must still discover its
                # same-name MODEL.YTD companion just as it does on Windows.
                expected_name = f"{texture_stem}.ytd".casefold()
                matches = tuple(
                    item for item in resolved_source.parent.iterdir()
                    if item.name.casefold() == expected_name
                    and item.is_file() and not item.is_symlink()
                )
                if len(matches) == 1:
                    resolved_texture = matches[0].resolve(strict=True)
        data = resolved_source.read_bytes()
        report = NativeAssetInspector(PROJECT_ROOT, resolved_game).inspect_bytes(
            resolved_source.name, data, edition=edition,
        )
        if report.model_scene is None:
            warning = next((item for item in report.warnings if item), "")
            detail = f": {warning}" if warning else ""
            raise ValueError(
                "The native model did not decode into renderable geometry" + detail
            )
        settings = CompiledRenderSettings(
            width=width, height=height, quality=quality, samples=samples,
            engine=engine, device=device, light_rig=light_rig,
            light_rotation_deg=light_rotation, light_strength=light_strength,
            background=background, background_color=background_color,
            transparent=transparent, lens_mm=lens_mm,
            ground_plane=ground_plane, contact_shadows=contact_shadows,
        )
        protected_roots: tuple[Path, ...] = (
            (resolved_source.parent, resolved_game)
            if resolved_game is not None else (resolved_source.parent,)
        )
        result = compile_vehicle_render(
            report.model_scene, output, settings=settings,
            blender_executable=blender, yaw=yaw, pitch=pitch,
            texture_dictionary=resolved_texture, edition=edition,
            gta_path=resolved_game,
            lod=lod, component=component, protected_roots=protected_roots,
        )
        payload = {
            "operation": "render_native_model",
            "source": str(resolved_source),
            "source_sha256": report.sha256,
            "edition": edition.title(),
            "texture_dictionary": (
                str(resolved_texture) if resolved_texture is not None else None
            ),
            "output": str(result.output_path),
            "width": result.width,
            "height": result.height,
            "elapsed_seconds": result.elapsed_seconds,
            "decode_metadata": report.metadata,
            "decode_warnings": list(report.warnings),
            "render_metadata": result.metadata,
        }
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    except CompiledRenderError as exc:
        detail = exc.as_dict()
        raise click.ClickException(json.dumps(detail, ensure_ascii=False)) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("build-native-workspace")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="GTA installation used to encrypt and reparse AWC audio streams.",
)
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def build_native_workspace(
    workspace: Path, gta_path: Path | None, output: Path,
) -> None:
    """Rebuild and reparse an edited native XML workspace."""
    try:
        asset, report = NativeAssetInspector(PROJECT_ROOT, gta_path).build_workspace(
            workspace, output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built and reparsed native asset: {asset}")
    click.echo(f"Validation report: {report}")


@main.command("inspect-binary-workspace")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--offset", default="0", show_default=True)
@click.option("--length", default=256, type=int, show_default=True)
def inspect_binary_workspace(workspace: Path, offset: str, length: int) -> None:
    """Render a bounded hexdump from an auditable binary workspace."""
    try:
        parsed_offset = int(offset, 0)
        click.echo(BinaryPatchWorkspace.hexdump(
            workspace, offset=parsed_offset, length=length,
        ))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _require_binary_edit_acknowledgement(acknowledged: bool) -> None:
    if not acknowledged:
        raise click.ClickException(
            "Binary workspace edits require --acknowledge-edit; the immutable source "
            "snapshot remains unchanged"
        )


@main.command("patch-binary-workspace")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--offset", required=True, help="Decimal or 0x-prefixed byte offset.")
@click.option("--hex", "replacement_hex", required=True, help="Replacement bytes in hex.")
@click.option("--expected-hex", default="", help="Optional expected bytes at the offset.")
@click.option("--acknowledge-edit", is_flag=True)
def patch_binary_workspace(
    workspace: Path, offset: str, replacement_hex: str,
    expected_hex: str, acknowledge_edit: bool,
) -> None:
    """Apply one same-size offset patch and append its hash-chained history."""
    _require_binary_edit_acknowledgement(acknowledge_edit)
    try:
        parsed_offset = int(offset, 0)
        record = BinaryPatchWorkspace.patch(
            workspace, parsed_offset, replacement_hex, expected_hex=expected_hex,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Applied auditable binary patch: {record}")


@main.command("undo-binary-workspace")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def undo_binary_workspace(workspace: Path, acknowledge_edit: bool) -> None:
    """Reverse the latest binary workspace operation and retain recovery history."""
    _require_binary_edit_acknowledgement(acknowledge_edit)
    try:
        record = BinaryPatchWorkspace.undo(workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Appended binary undo operation: {record}")


@main.command("build-binary-workspace")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def build_binary_workspace(workspace: Path, output: Path) -> None:
    """Build a same-size binary asset and bounded changed-range report."""
    try:
        asset, report = BinaryPatchWorkspace.build(workspace, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built verified binary asset: {asset}")
    click.echo(f"Binary diff report: {report}")


@main.command("list-gxt2-entries")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
def list_gxt2_entries(workspace: Path, output: Path | None) -> None:
    """List validated hash/text records from a GXT2 workspace."""
    try:
        entries = Gxt2Workspace.validate(workspace)["entries"]
        rendered = json.dumps(list(entries), indent=2, ensure_ascii=False) + "\n"
        if output is None:
            click.echo(rendered, nl=False)
        else:
            destination = output.resolve()
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"GXT2 catalog output already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
            click.echo(f"Wrote {len(entries)} GXT2 text record(s): {destination}")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _require_gxt2_edit_acknowledgement(acknowledged: bool) -> None:
    if not acknowledged:
        raise click.ClickException(
            "GXT2 workspace edits require --acknowledge-edit; the immutable source "
            "snapshot remains unchanged"
        )


@main.command("set-gxt2-text")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("label_hash")
@click.argument("text")
@click.option("--acknowledge-edit", is_flag=True)
def set_gxt2_text(
    workspace: Path, label_hash: str, text: str, acknowledge_edit: bool,
) -> None:
    """Replace one GXT2 text value while retaining local undo history."""
    _require_gxt2_edit_acknowledgement(acknowledge_edit)
    try:
        record = Gxt2Workspace.set_text(workspace, label_hash, text)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Updated GXT2 text; undo history: {record}")


@main.command("add-gxt2-entry")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("label_hash")
@click.argument("text")
@click.option("--acknowledge-edit", is_flag=True)
def add_gxt2_entry(
    workspace: Path, label_hash: str, text: str, acknowledge_edit: bool,
) -> None:
    """Add one unique hash/text record to a GXT2 workspace."""
    _require_gxt2_edit_acknowledgement(acknowledge_edit)
    try:
        record = Gxt2Workspace.add(workspace, label_hash, text)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Added GXT2 entry; undo history: {record}")


@main.command("remove-gxt2-entry")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("label_hash")
@click.option("--acknowledge-edit", is_flag=True)
def remove_gxt2_entry(
    workspace: Path, label_hash: str, acknowledge_edit: bool,
) -> None:
    """Remove one GXT2 record while retaining local undo history."""
    _require_gxt2_edit_acknowledgement(acknowledge_edit)
    try:
        record = Gxt2Workspace.remove(workspace, label_hash)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Removed GXT2 entry; undo history: {record}")


@main.command("undo-gxt2-edit")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def undo_gxt2_edit(workspace: Path, acknowledge_edit: bool) -> None:
    """Restore the GXT2 table before its latest recorded operation."""
    _require_gxt2_edit_acknowledgement(acknowledge_edit)
    try:
        record = Gxt2Workspace.undo(workspace)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Appended GXT2 undo operation: {record}")


@main.command("build-gxt2-workspace")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def build_gxt2_workspace(workspace: Path, output: Path) -> None:
    """Rebuild and semantically reparse an edited GXT2 text table."""
    try:
        asset, report = Gxt2Workspace.build(workspace, output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built and reparsed GXT2 dictionary: {asset}")
    click.echo(f"GXT2 validation report: {report}")


@main.command("list-ytd-textures")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
def list_ytd_textures(workspace: Path, output: Path | None) -> None:
    """List validated texture records from a native YTD workspace."""
    try:
        catalog = TextureDictionaryWorkspace(workspace).catalog()
        rendered = json.dumps(catalog.to_dict(), indent=2) + "\n"
        if output is None:
            click.echo(rendered, nl=False)
        else:
            destination = output.resolve()
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"Texture catalog output already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
            click.echo(f"Wrote {len(catalog.textures)} YTD texture record(s): {destination}")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _require_texture_edit_acknowledgement(acknowledged: bool) -> None:
    if not acknowledged:
        raise click.ClickException(
            "Texture workspace edits require --acknowledge-edit; the immutable YTD "
            "source snapshot remains unchanged"
        )


@main.command("replace-ytd-texture")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("texture_name")
@click.argument("image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def replace_ytd_texture(
    workspace: Path, texture_name: str, image: Path, acknowledge_edit: bool,
) -> None:
    """Replace one texture using DDS or a converted raster image."""
    _require_texture_edit_acknowledgement(acknowledge_edit)
    try:
        result = TextureDictionaryWorkspace(workspace).replace(texture_name, image)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Replaced {result.texture.name} ({result.texture.width}x{result.texture.height}, "
        f"{result.texture.format}); undo history: {result.history}"
    )


@main.command("add-ytd-texture")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("texture_name")
@click.argument("image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def add_ytd_texture(
    workspace: Path, texture_name: str, image: Path, acknowledge_edit: bool,
) -> None:
    """Add one named texture using DDS or a converted raster image."""
    _require_texture_edit_acknowledgement(acknowledge_edit)
    try:
        result = TextureDictionaryWorkspace(workspace).add(texture_name, image)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Added {result.texture.name} ({result.texture.width}x{result.texture.height}, "
        f"{result.texture.format}); undo history: {result.history}"
    )


@main.command("remove-ytd-texture")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("texture_name")
@click.option("--acknowledge-edit", is_flag=True)
def remove_ytd_texture(
    workspace: Path, texture_name: str, acknowledge_edit: bool,
) -> None:
    """Remove one named texture while preserving local undo history."""
    _require_texture_edit_acknowledgement(acknowledge_edit)
    try:
        result = TextureDictionaryWorkspace(workspace).remove(texture_name)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Removed {result.texture.name}; undo history: {result.history}")


@main.command("undo-ytd-texture-edit")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--acknowledge-edit", is_flag=True)
def undo_ytd_texture_edit(workspace: Path, acknowledge_edit: bool) -> None:
    """Restore the latest YTD texture edit while retaining recovery history."""
    _require_texture_edit_acknowledgement(acknowledge_edit)
    try:
        result = TextureDictionaryWorkspace(workspace).restore_latest()
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Restored {result.restored.name}; pre-restore recovery history: "
        f"{result.recovery_history}"
    )


@main.command("diff-meta")
@click.argument("before", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("after", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def diff_meta_command(before: Path, after: Path, output: Path) -> None:
    """Write a path-aware semantic diff for authored META/XML files."""
    try:
        report = diff_meta(before, after)
        written = report.write(output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Wrote {len(report.changes)} semantic change(s): {written}")


@main.command("validate-meta-roundtrip")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--serialized-output", type=click.Path(path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
def validate_meta_roundtrip_command(
    source: Path, serialized_output: Path | None, output: Path | None,
) -> None:
    """Prove parse/serialize/reparse semantic equivalence for authored metadata."""
    try:
        result = validate_meta_roundtrip(source, serialized_output=serialized_output)
        rendered = json.dumps(result, indent=2) + "\n"
        if output:
            destination = output.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
            click.echo(f"Wrote META round-trip report: {destination}")
        else:
            click.echo(rendered, nl=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if not result["semantically_equivalent"]:
        raise click.ClickException("Metadata changed semantically during round trip")


@main.command("inspect-package-rpfs")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
def inspect_package_rpfs(source: Path, output_dir: Path, gta_path: Path | None) -> None:
    """Index every loose RPF member of a package using temporary extraction."""
    try:
        game = _game_path(gta_path)
        scan = AddonPackageInspector().inspect(source)
        reader = PackageAssetReader(source)
        members = [entry for entry in scan.entries if entry.suffix == ".rpf"]
        if not members:
            raise ValueError("Package contains no loose RPF members")
        if len(members) > 20:
            raise ValueError("Package contains more than 20 RPF members")
        destination = output_dir.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        service = RpfExplorerService(PROJECT_ROOT, game)
        with tempfile.TemporaryDirectory(prefix="allin1-sdk-rpf-") as temporary:
            for number, member in enumerate(members, start=1):
                if member.size > 512 * 1024 * 1024:
                    raise ValueError(f"RPF exceeds inspection limit: {member.path}")
                content = reader.read(member.path, limit=member.size + 1)
                if content.truncated or len(content.data) != member.size:
                    raise ValueError(f"Could not read complete RPF: {member.path}")
                extracted = Path(temporary) / f"member-{number}.rpf"
                extracted.write_bytes(content.data)
                safe = "-".join(Path(member.path).parts).replace(".rpf", "")
                service.index(extracted).export(destination / safe)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Indexed {len(members)} package RPF member(s): {destination}")


@main.group("sdk")
def sdk_compatibility_group() -> None:
    """Compatibility alias for commands previously hosted by the launcher."""


for _command in (
    list_examples, validate, link, import_package, audit_folder, oiv_plan,
    compile_oiv_xml,
    inspect_rpf, dlc_inventory, compile_vehicle_data,
    inspect_vehicle_project, export_vehicle_project, build_vehicle_package,
    create_vehicle_authoring, inspect_vehicle_authoring,
    set_vehicle_fields, undo_vehicle_edit,
    create_ped_authoring, inspect_ped_authoring,
    plan_ped_clone, clone_ped_bundle,
    set_ped_fields, migrate_ped_identity, undo_ped_edit,
    create_weapon_authoring, inspect_weapon_authoring,
    plan_weapon_clone, clone_weapon_bundle,
    set_weapon_fields, set_weapon_component, set_weapon_attachment,
    inspect_weapon_animation, clone_weapon_animation,
    inspect_weapon_shop, set_weapon_shop_fields,
    undo_weapon_edit,
    index_rpf, catalog_rpfs,
    search_rpf_catalog, build_rpf_tree,
    create_rpf_graph, import_rpf_graph, import_package_graph,
    render_rpf_graph_previews,
    inspect_rpf_graph, validate_rpf_graph,
    analyze_package_graph, inspect_package_graph_relations,
    add_rpf_graph_container, add_rpf_graph_file, expand_rpf_graph_sealed,
    rename_rpf_graph_node,
    reparent_rpf_graph_node, position_rpf_graph_node, remove_rpf_graph_node,
    layout_rpf_graph, refresh_rpf_graph_sources, materialize_rpf_graph, build_rpf_graph,
    plan_rpf_graph_origin,
    create_rpf_program, list_rpf_program_templates,
    inspect_rpf_program, add_rpf_program_node,
    configure_rpf_program_node, connect_rpf_program_nodes,
    disconnect_rpf_program_node, position_rpf_program_node,
    layout_rpf_program, remove_rpf_program_node, plan_rpf_program,
    run_rpf_program,
    create_rpf_change_set, inspect_rpf_change_set, stage_rpf_change,
    unstage_rpf_change, move_rpf_change, plan_rpf_change_set,
    verify_rpf_archive, defragment_rpf,
    extract_rpf_entry, inspect_rpf_native_entry,
    extract_rpf_subtree, export_rpf_native_workspace,
    export_rpf_binary_workspace, export_rpf_gxt2_workspace, diff_rpf,
    derive_rpf_plan,
    plan_rpf_replacement, plan_rpf_native_workspace,
    plan_rpf_binary_workspace, plan_rpf_gxt2_workspace,
    plan_rpf_add, plan_rpf_delete, plan_rpf_batch,
    plan_rpf_sync, apply_rpf_plan,
    verify_rpf_transaction, rollback_rpf_transaction, recover_rpf_transaction,
    list_rpf_transactions, canary_rpf_transaction, diff_meta_command,
    inspect_native_asset, export_native_workspace, build_native_workspace,
    inspect_binary_workspace, patch_binary_workspace,
    undo_binary_workspace, build_binary_workspace,
    list_gxt2_entries, set_gxt2_text, add_gxt2_entry, remove_gxt2_entry,
    undo_gxt2_edit, build_gxt2_workspace,
    list_ytd_textures, replace_ytd_texture, add_ytd_texture, remove_ytd_texture,
    undo_ytd_texture_edit,
    validate_meta_roundtrip_command, inspect_package_rpfs,
):
    sdk_compatibility_group.add_command(_command)


if __name__ == "__main__":
    main()

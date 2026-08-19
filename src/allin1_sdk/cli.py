"""Command-line interface for standalone ALLIN1 SDK workflows."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import click

from allin1_sdk.addon_importer import (
    AddonDraftBuilder, AddonPackageInspector, PackageAssetReader,
)
from allin1_sdk.addon_sdk import AddonLinker, AddonManifest, AddonSdkCatalog
from allin1_sdk.binary_workspace import BinaryPatchWorkspace
from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.detector import detect_gta_path
from allin1_sdk.dlc_inventory import DlcInventory
from allin1_sdk.meta_tools import diff_meta, validate_meta_roundtrip
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.mods import ModIntegrationService, ModManifest
from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
from allin1_sdk.rage_data_compiler import RageVehicleDataCompiler
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_catalog import RpfCatalogService
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_program import NODE_SPECS, RpfPackageProgram
from allin1_sdk.rpf_tools import RpfExplorerService, _running_gta_processes
from allin1_sdk.texture_workspace import TextureDictionaryWorkspace


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


@click.group()
def main() -> None:
    """Author, audit, and inspect GTA V add-on content."""


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


@main.command("install-package")
@click.argument(
    "manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path),
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
    """Install one validated package with receipts, backups, and rollback ownership."""
    if not acknowledge_write:
        raise click.ClickException(
            "Package installation requires --acknowledge-write."
        )
    running = _running_gta_processes()
    if running:
        raise click.ClickException(
            "Close GTA V before installing a package: " + ", ".join(running)
        )
    package = ModManifest.load(manifest)
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
def create_rpf_program(graph: Path, output: Path) -> None:
    """Create a typed visual build program bound to one RPF package graph."""
    try:
        program = RpfPackageProgram.create(graph, output)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created RPF package node program: {program}")
    click.echo("Default flow: Package graph -> Validate package")


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
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def diff_rpf(
    left: Path, right: Path, exact_content: bool,
    gta_path: Path | None, output: Path,
) -> None:
    """Compare two recursive RPF trees and export JSON and Markdown reports."""
    service = RpfExplorerService(PROJECT_ROOT, _game_path(gta_path))
    try:
        left_index = service.index(left)
        right_index = service.index(right)
        report = service.compare_indexes(
            left_index, right_index, exact_content=exact_content,
        )
        json_path, markdown_path = service.export_diff(report, output)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = report["summary"]
    click.echo(
        f"RPF diff: {summary['added']} added, {summary['removed']} removed, "
        f"{summary['modified']} modified; {json_path} and {markdown_path}"
    )


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
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def export_native_workspace(source: Path, edition: str, output: Path) -> None:
    """Export a native resource to an editable XML/dependency workspace."""
    try:
        workspace = NativeAssetInspector(PROJECT_ROOT).export_workspace(
            source, output, edition=edition,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Exported verified native editing workspace: {workspace}")


@main.command("build-native-workspace")
@click.argument("workspace", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def build_native_workspace(workspace: Path, output: Path) -> None:
    """Rebuild and reparse an edited native XML workspace."""
    try:
        asset, report = NativeAssetInspector(PROJECT_ROOT).build_workspace(
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
    inspect_rpf, dlc_inventory, compile_vehicle_data, index_rpf, catalog_rpfs,
    search_rpf_catalog, build_rpf_tree,
    create_rpf_graph, import_rpf_graph, inspect_rpf_graph, validate_rpf_graph,
    add_rpf_graph_container, add_rpf_graph_file, rename_rpf_graph_node,
    reparent_rpf_graph_node, position_rpf_graph_node, remove_rpf_graph_node,
    layout_rpf_graph, refresh_rpf_graph_sources, materialize_rpf_graph, build_rpf_graph,
    plan_rpf_graph_origin,
    create_rpf_program, inspect_rpf_program, add_rpf_program_node,
    configure_rpf_program_node, connect_rpf_program_nodes,
    disconnect_rpf_program_node, position_rpf_program_node,
    layout_rpf_program, remove_rpf_program_node, plan_rpf_program,
    run_rpf_program,
    verify_rpf_archive, defragment_rpf,
    extract_rpf_entry,
    extract_rpf_subtree, export_rpf_native_workspace,
    export_rpf_binary_workspace, export_rpf_gxt2_workspace, diff_rpf,
    plan_rpf_replacement, plan_rpf_native_workspace,
    plan_rpf_binary_workspace, plan_rpf_gxt2_workspace,
    plan_rpf_add, plan_rpf_delete, plan_rpf_batch,
    plan_rpf_sync, apply_rpf_plan,
    verify_rpf_transaction, rollback_rpf_transaction, recover_rpf_transaction,
    list_rpf_transactions, canary_rpf_transaction, diff_meta_command,
    export_native_workspace, build_native_workspace,
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

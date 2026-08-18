"""Command-line interface for standalone ALLIN1 SDK workflows."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import click

from allin1_sdk.addon_importer import (
    AddonDraftBuilder, AddonPackageInspector, PackageAssetReader,
)
from allin1_sdk.addon_sdk import AddonLinker, AddonManifest, AddonSdkCatalog
from allin1_sdk.detector import detect_gta_path
from allin1_sdk.dlc_inventory import DlcInventory
from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
from allin1_sdk.rage_data_compiler import RageVehicleDataCompiler
from allin1_sdk.rpf_tools import RpfExplorerService


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


@click.group()
def main() -> None:
    """Author, audit, and inspect GTA V add-on content."""


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
def oiv_plan(source: Path, output: Path, managed_package: Path | None) -> None:
    """Preview an OIV recipe without executing it."""
    try:
        plan = OivWorkbench().inspect(source)
        written = plan.write_report(output)
        if managed_package:
            OivWorkbench().export_managed_package(plan, managed_package)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    state = "managed export ready" if plan.translatable else "manual review required"
    click.echo(f"Wrote OIV plan ({state}): {written}")


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


@main.command("plan-rpf-replacement")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("entry_path")
@click.argument("payload", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--archive-path", default="")
@click.option("--gta-path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
def plan_rpf_replacement(
    archive: Path, entry_path: str, payload: Path, archive_path: str,
    gta_path: Path | None, output: Path,
) -> None:
    """Create a checksummed replacement plan without writing the archive."""
    service = RpfExplorerService(PROJECT_ROOT, _game_path(gta_path))
    try:
        index, entry = _entry(service, archive, archive_path, entry_path)
        plan = service.replacement_plan(index, entry, payload)
        destination = output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Wrote plan only; no archive was changed: {destination}")


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
    inspect_rpf, dlc_inventory, compile_vehicle_data, index_rpf, extract_rpf_entry,
    plan_rpf_replacement, inspect_package_rpfs,
):
    sdk_compatibility_group.add_command(_command)


if __name__ == "__main__":
    main()

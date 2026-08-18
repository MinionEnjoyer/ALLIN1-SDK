"""Cross-file GTA vehicle metadata compiler and unresolved-reference reports."""

from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape

from allin1_sdk.addon_importer import AddonPackageInspector, PackageScan


@dataclass(frozen=True)
class VehicleDataFinding:
    severity: str
    code: str
    model: str
    message: str


@dataclass(frozen=True)
class CompiledVehicle:
    model: str
    display_name: str
    make_name: str
    vehicle_class: str
    vehicle_type: str
    handling_id: str
    handling_resolved: bool
    layout: str
    audio_name_hash: str
    texture_dictionary: str
    variation_source: str
    tuning_kits: tuple[str, ...]
    unresolved_kits: tuple[str, ...]
    model_assets: tuple[str, ...]
    texture_assets: tuple[str, ...]
    metadata_sources: tuple[str, ...]
    registration_sources: tuple[str, ...]
    label_assets: tuple[str, ...]


@dataclass(frozen=True)
class VehicleDataReport:
    source: Path
    vehicles: tuple[CompiledVehicle, ...]
    findings: tuple[VehicleDataFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "source": str(self.source),
            "summary": {
                "vehicles": len(self.vehicles), "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "vehicles": [asdict(item) for item in self.vehicles],
            "findings": [asdict(item) for item in self.findings],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Compiled GTA V vehicle data", "", f"Source: `{self.source}`", "",
            f"Vehicles: **{len(self.vehicles)}** · Errors: **{self.error_count}** · "
            f"Warnings: **{self.warning_count}**", "",
            "| Model | Display name | Handling | Variation | Kits | Models | Textures |",
            "|---|---|---|---|---:|---:|---:|",
        ]
        for item in self.vehicles:
            lines.append(
                f"| `{item.model}` | {item.display_name or '—'} | "
                f"{'resolved' if item.handling_resolved else 'missing'} | "
                f"{'yes' if item.variation_source else 'missing'} | "
                f"{len(item.tuning_kits)} | {len(item.model_assets)} | "
                f"{len(item.texture_assets)} |"
            )
        lines.extend(["", "## Unresolved and conflicting references", ""])
        if not self.findings:
            lines.append("Every visible cross-file reference resolved.")
        for item in self.findings:
            lines.append(
                f"- **{item.severity.upper()} `{item.code}`** `{item.model}`: "
                f"{item.message}"
            )
        lines.extend([
            "", "Binary `dlc.rpf`, GXT2, REL, and AWC contents remain opaque until "
            "their specific format readers provide evidence. The compiler never assumes "
            "that an opaque archive satisfies a missing link.", "",
        ])
        return "\n".join(lines)

    def write_bundle(self, directory: str | Path) -> tuple[Path, ...]:
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        json_path = root / "vehicles.json"
        csv_path = root / "vehicles.csv"
        unresolved_path = root / "unresolved.csv"
        workbook_path = root / "vehicles.xlsx"
        markdown_path = root / "vehicle-data-report.md"
        json_path.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8",
        )
        rows = [self._vehicle_row(item) for item in self.vehicles]
        headers = list(rows[0]) if rows else list(self._vehicle_headers())
        self._write_csv(csv_path, headers, rows)
        finding_headers = ["severity", "code", "model", "message"]
        finding_rows = [[
            item.severity, item.code, item.model, item.message,
        ] for item in self.findings]
        self._write_csv(unresolved_path, finding_headers, finding_rows)
        self._write_xlsx(
            workbook_path,
            (("Vehicles", headers, rows),
             ("Unresolved", finding_headers, finding_rows)),
        )
        markdown_path.write_text(self.to_markdown(), encoding="utf-8")
        return json_path, csv_path, unresolved_path, workbook_path, markdown_path

    @staticmethod
    def _vehicle_headers() -> tuple[str, ...]:
        return (
            "model", "display_name", "make_name", "vehicle_class", "vehicle_type",
            "handling_id", "handling_resolved", "layout", "audio_name_hash",
            "texture_dictionary", "variation_source", "tuning_kits",
            "unresolved_kits", "model_assets", "texture_assets",
            "metadata_sources", "registration_sources", "label_assets",
        )

    @classmethod
    def _vehicle_row(cls, item: CompiledVehicle) -> dict[str, object]:
        data = asdict(item)
        for key, value in tuple(data.items()):
            if isinstance(value, tuple):
                data[key] = "; ".join(value)
        return {header: data[header] for header in cls._vehicle_headers()}

    @staticmethod
    def _safe_csv(value: object) -> object:
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    @classmethod
    def _write_csv(
        cls, path: Path, headers: list[str], rows: list[dict[str, object] | list[str]],
    ) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            for row in rows:
                values = [row.get(header, "") for header in headers] if isinstance(row, dict) else row
                writer.writerow([cls._safe_csv(value) for value in values])

    @classmethod
    def _write_xlsx(
        cls, path: Path,
        sheets: tuple[tuple[str, list[str], list[dict[str, object] | list[str]]], ...],
    ) -> None:
        """Write a small standards-compliant workbook without a runtime dependency."""
        content_types = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            '<Default Extension="xml" ContentType="application/xml"/>',
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        ]
        for index in range(1, len(sheets) + 1):
            content_types.append(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        content_types.append("</Types>")
        workbook_sheets = "".join(
            f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, (name, _, _) in enumerate(sheets, start=1)
        )
        relationships = "".join(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", "".join(content_types))
            package.writestr("_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>')
            package.writestr("xl/workbook.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets>{workbook_sheets}</sheets></workbook>')
            package.writestr("xl/_rels/workbook.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'{relationships}</Relationships>')
            for index, (_, headers, rows) in enumerate(sheets, start=1):
                package.writestr(
                    f"xl/worksheets/sheet{index}.xml",
                    cls._worksheet(headers, rows),
                )

    @classmethod
    def _worksheet(
        cls, headers: list[str], rows: list[dict[str, object] | list[str]],
    ) -> str:
        all_rows: list[list[object]] = [headers]
        for row in rows:
            all_rows.append(
                [row.get(header, "") for header in headers]
                if isinstance(row, dict) else list(row)
            )
        xml_rows = []
        for row_index, values in enumerate(all_rows, start=1):
            cells = []
            for column_index, value in enumerate(values, start=1):
                column = ""
                number = column_index
                while number:
                    number, remainder = divmod(number - 1, 26)
                    column = chr(65 + remainder) + column
                text = escape(str(value)).replace("\x00", "")
                cells.append(
                    f'<c r="{column}{row_index}" t="inlineStr"><is><t>{text}</t></is></c>'
                )
            xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
        )


class RageVehicleDataCompiler:
    def compile(self, source: str | Path) -> VehicleDataReport:
        scan = AddonPackageInspector().inspect(source)
        return self.compile_scan(scan)

    @staticmethod
    def compile_scan(scan: PackageScan) -> VehicleDataReport:
        findings: list[VehicleDataFinding] = []
        handling = {item.name.casefold(): item for item in scan.handlings}
        variations: dict[str, list] = {}
        for item in scan.variations:
            variations.setdefault(item.model_name.casefold(), []).append(item)
        kits = {}
        for item in scan.kits:
            kits[item.name.casefold()] = item
            if item.kit_id:
                kits[item.kit_id.casefold()] = item
        entries = tuple(item.path for item in scan.entries)
        label_assets = tuple(
            path for path in entries
            if PurePosixPath(path).suffix.casefold() in {".gxt2", ".oxt"}
        )
        registration_sources = tuple(item.source for item in scan.registrations)
        counts: dict[str, int] = {}
        for vehicle in scan.vehicles:
            key = vehicle.model_name.casefold()
            counts[key] = counts.get(key, 0) + 1

        compiled: list[CompiledVehicle] = []
        for vehicle in scan.vehicles:
            model_key = vehicle.model_name.casefold()
            handling_resolved = bool(vehicle.handling_id) and vehicle.handling_id.casefold() in handling
            vehicle_variations = variations.get(model_key, [])
            variation = vehicle_variations[0] if len(vehicle_variations) == 1 else None
            tuning_kits = variation.kits if variation else ()
            unresolved_kits = tuple(
                value for value in tuning_kits if value.casefold() not in kits
            )
            model_assets = tuple(
                path for path in entries
                if PurePosixPath(path).suffix.casefold() in {".yft", ".ydd", ".ydr"}
                and PurePosixPath(path).stem.casefold() in {model_key, f"{model_key}_hi"}
            )
            txd_key = (vehicle.txd_name or vehicle.model_name).casefold()
            texture_assets = tuple(
                path for path in entries
                if PurePosixPath(path).suffix.casefold() == ".ytd"
                and PurePosixPath(path).stem.casefold() == txd_key
            )
            model_findings: list[VehicleDataFinding] = []
            def add(severity: str, code: str, message: str) -> None:
                model_findings.append(VehicleDataFinding(
                    severity, code, vehicle.model_name, message,
                ))
            if counts[model_key] > 1:
                add("error", "duplicate_vehicle", "Multiple vehicles.meta records use this model name.")
            if not handling_resolved:
                add("error", "missing_handling", f"handlingId '{vehicle.handling_id}' did not resolve.")
            if len(vehicle_variations) != 1:
                code = "missing_variation" if not vehicle_variations else "duplicate_variation"
                add("warning", code, "Expected exactly one carvariations record.")
            if unresolved_kits:
                add("warning", "missing_tuning_kit", "Unresolved kits: " + ", ".join(unresolved_kits))
            if not model_assets:
                add("error", "missing_model_asset", "No visible YFT/YDD/YDR model matched the model name.")
            if not texture_assets:
                add("error", "missing_texture_asset", f"No visible YTD matched txdName '{txd_key}'.")
            if not registration_sources:
                add("warning", "missing_registration", "No setup2/content/resource registration was visible.")
            if (vehicle.game_name or vehicle.make_name) and not label_assets:
                add("warning", "missing_text_dictionary", "No visible OXT/GXT2 label dictionary was found.")
            findings.extend(model_findings)
            compiled.append(CompiledVehicle(
                vehicle.model_name, vehicle.game_name, vehicle.make_name,
                vehicle.vehicle_class, vehicle.vehicle_type, vehicle.handling_id,
                handling_resolved, vehicle.layout, vehicle.audio_name_hash,
                vehicle.txd_name, variation.source if variation else "", tuning_kits,
                unresolved_kits, model_assets, texture_assets,
                tuple(dict.fromkeys((vehicle.source,) + tuple(
                    item.source for item in scan.handlings
                    if item.name.casefold() == vehicle.handling_id.casefold()
                ) + tuple(item.source for item in vehicle_variations))),
                registration_sources, label_assets,
            ))
        if not scan.vehicles:
            findings.append(VehicleDataFinding(
                "warning", "no_vehicles", "", "No vehicles.meta records were discovered.",
            ))
        return VehicleDataReport(
            scan.source, tuple(sorted(compiled, key=lambda item: item.model.casefold())),
            tuple(findings),
        )

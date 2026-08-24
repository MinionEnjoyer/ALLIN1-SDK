"""Read-only material progression diagnostics for package-owned RPF assets."""

from __future__ import annotations

import hashlib
import io
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from lxml import etree
from PIL import Image, ImageChops, ImageDraw, ImageStat

from allin1_sdk.mod_package_contract import VisualAssetProgression
from allin1_sdk.native_assets import resolve_shader_name
from allin1_sdk.processes import run_hidden
from allin1_sdk.rpf_tools import RpfEntryRecord, RpfExplorerService, RpfIndex


_NUMBERED_MODEL = re.compile(r"^(?P<prefix>.+)_(?P<level>[0-9]{2,3})$", re.I)
_MAX_AUDIT_LEVELS = 64


@dataclass(frozen=True)
class MaterialProgressionFinding:
    severity: str
    code: str
    message: str
    level: int | None = None


@dataclass(frozen=True)
class MaterialTier:
    level: int
    model: str
    texture: str
    shader: str
    authored_shader: str
    emissive_multiplier: float | None
    geometry_count: int
    vertex_count: int
    triangle_count: int
    alpha_min: float | None
    alpha_max: float | None
    alpha_mean: float | None
    luminance_min: float | None
    luminance_max: float | None
    luminance_mean: float | None
    neighboring_visual_difference: float | None
    texture_missing: bool


@dataclass(frozen=True)
class MaterialProgressionReport:
    source: str
    archive_path: str
    representative_family: str
    families: tuple[str, ...]
    levels: int
    model_count: int
    texture_dictionary: str
    texture_count: int
    archetype_dictionary: str
    archetype_count: int
    inferred: bool
    tiers: tuple[MaterialTier, ...]
    findings: tuple[MaterialProgressionFinding, ...]
    preview_sha256: str | None
    preview_width: int
    preview_height: int
    preview_png: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "archive_path": self.archive_path,
            "representative_family": self.representative_family,
            "families": list(self.families),
            "levels": self.levels,
            "model_count": self.model_count,
            "texture_dictionary": self.texture_dictionary,
            "texture_count": self.texture_count,
            "archetype_dictionary": self.archetype_dictionary,
            "archetype_count": self.archetype_count,
            "inferred": self.inferred,
            "summary": {
                "errors": self.error_count,
                "warnings": self.warning_count,
                "has_visual_preview": self.preview_png is not None,
            },
            "tiers": [asdict(item) for item in self.tiers],
            "findings": [asdict(item) for item in self.findings],
            "preview": {
                "sha256": self.preview_sha256,
                "width": self.preview_width,
                "height": self.preview_height,
                "description": (
                    "Top row: decoded texture over checkerboard. Bottom row: "
                    "approximate alpha/emissive blend over a dark weapon surface."
                ),
            },
        }


@dataclass(frozen=True)
class _DiscoveredProgression:
    archive_path: str
    families: tuple[str, ...]
    levels: int
    model_entries: dict[str, tuple[RpfEntryRecord, ...]]
    ytd: RpfEntryRecord
    ytyp: RpfEntryRecord
    inferred: bool


def _xml(path: Path) -> etree._Element:
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        recover=False, huge_tree=True,
    )
    tree = etree.parse(str(path), parser)
    if tree.docinfo.doctype:
        raise ValueError("Native XML contains a prohibited document type")
    return tree.getroot()


def _text(parent: etree._Element, name: str) -> str:
    result = parent.xpath(f"./*[local-name()='{name}']")
    if not result:
        return ""
    element = result[0]
    return str(element.get("ref") or element.get("value") or element.text or "").strip()


def _model_metrics(path: Path) -> dict[str, Any]:
    root = _xml(path)
    shaders = root.xpath(
        ".//*[local-name()='ShaderGroup']/*[local-name()='Shaders']"
        "/*[local-name()='Item']"
    )
    shader = shaders[0] if shaders else None
    authored_shader = _text(shader, "Name") if shader is not None else ""
    texture = ""
    emissive: float | None = None
    if shader is not None:
        for parameter in shader.xpath(
            "./*[local-name()='Parameters']/*[local-name()='Item']"
        ):
            kind = str(parameter.get("type", "")).casefold()
            if kind == "texture" and not texture:
                texture = _text(parameter, "Name")
            if str(parameter.get("name", "")).casefold() == "emissivemultiplier":
                try:
                    emissive = float(parameter.get("x", ""))
                except ValueError:
                    emissive = None
    geometries = root.xpath(".//*[local-name()='Geometries']/*[local-name()='Item']")
    vertex_count = 0
    triangle_count = 0
    for geometry in geometries:
        for data in geometry.xpath(".//*[local-name()='VertexBuffer']/*[local-name()='Data']"):
            vertex_count += sum(bool(line.strip()) for line in (data.text or "").splitlines())
        tokens = []
        for data in geometry.xpath(".//*[local-name()='IndexBuffer']/*[local-name()='Data']"):
            tokens.extend((data.text or "").split())
        triangle_count += len(tokens) // 3
    return {
        "texture": texture,
        "authored_shader": authored_shader,
        "shader": resolve_shader_name(authored_shader),
        "emissive": emissive,
        "geometry_count": len(geometries),
        "vertex_count": vertex_count,
        "triangle_count": triangle_count,
    }


def _texture_metrics(path: Path) -> tuple[dict[str, float], Image.Image]:
    with Image.open(path) as source:
        image = source.convert("RGBA")
    pixels = tuple(
        image.get_flattened_data()
        if hasattr(image, "get_flattened_data") else image.getdata()
    )
    if not pixels:
        raise ValueError(f"Decoded texture contains no pixels: {path.name}")
    alpha = [pixel[3] / 255.0 for pixel in pixels]
    luminance = [
        (0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]) / 255.0
        for pixel in pixels
    ]
    return ({
        "alpha_min": min(alpha), "alpha_max": max(alpha),
        "alpha_mean": sum(alpha) / len(alpha),
        "luminance_min": min(luminance), "luminance_max": max(luminance),
        "luminance_mean": sum(luminance) / len(luminance),
    }, image)


def _approximate_material(image: Image.Image, multiplier: float | None) -> Image.Image:
    strength = max(0.0, min(4.0, multiplier or 0.0))
    result = Image.new("RGB", image.size)
    output: list[tuple[int, int, int]] = []
    pixels = (
        image.get_flattened_data()
        if hasattr(image, "get_flattened_data") else image.getdata()
    )
    for red, green, blue, alpha_value in pixels:
        alpha = alpha_value / 255.0
        base = 30.0
        output.append(tuple(
            max(0, min(255, round(
                base * (1.0 - alpha) + channel * alpha + channel * strength
            )))
            for channel in (red, green, blue)
        ))
    result.putdata(output)
    return result


def _visual_difference(left: Image.Image, right: Image.Image) -> float:
    size = (128, 16)
    difference = ImageChops.difference(
        left.convert("RGB").resize(size, Image.Resampling.BILINEAR),
        right.convert("RGB").resize(size, Image.Resampling.BILINEAR),
    )
    return sum(ImageStat.Stat(difference).mean) / (3.0 * 255.0)


def _progression_strip(
    tiers: list[dict[str, Any]],
) -> tuple[bytes | None, str | None, int, int]:
    renderable = [item for item in tiers if item.get("image") is not None]
    if not renderable:
        return None, None, 0, 0
    cell_width, row_height, header, footer = 84, 40, 22, 26
    width = cell_width * len(tiers)
    height = header + row_height * 2 + footer
    canvas = Image.new("RGB", (width, height), (14, 19, 16))
    draw = ImageDraw.Draw(canvas)
    for index, tier in enumerate(tiers):
        x = index * cell_width
        draw.rectangle((x, 0, x + cell_width - 1, height - 1), outline=(49, 70, 58))
        draw.text((x + 5, 5), f"L{tier['level']:02d}", fill=(220, 235, 225))
        image = tier.get("image")
        if image is None:
            draw.text((x + 6, header + 28), "MISSING", fill=(235, 93, 82))
            continue
        sample = image.convert("RGBA").resize(
            (cell_width - 8, row_height - 4), Image.Resampling.BILINEAR,
        )
        checker = Image.new("RGB", sample.size, (61, 65, 63))
        checker_draw = ImageDraw.Draw(checker)
        for yy in range(0, checker.height, 8):
            for xx in range(0, checker.width, 8):
                if (xx // 8 + yy // 8) % 2:
                    checker_draw.rectangle((xx, yy, xx + 7, yy + 7), fill=(92, 96, 94))
        checker.paste(sample, (0, 0), sample)
        canvas.paste(checker, (x + 4, header + 2))
        shaded = tier["render"].resize(
            (cell_width - 8, row_height - 4), Image.Resampling.BILINEAR,
        )
        canvas.paste(shaded, (x + 4, header + row_height + 2))
        multiplier = tier.get("emissive")
        draw.text(
            (x + 4, header + row_height * 2 + 6),
            "E —" if multiplier is None else f"E {multiplier:.3f}",
            fill=(155, 179, 165),
        )
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    data = output.getvalue()
    return data, hashlib.sha256(data).hexdigest(), width, height


def _declared_archives(
    declarations: Iterable[VisualAssetProgression],
) -> set[str]:
    return {item.archive.casefold() for item in declarations}


def _discover(
    index: RpfIndex, declarations: Iterable[VisualAssetProgression],
) -> tuple[_DiscoveredProgression, ...]:
    declared = _declared_archives(declarations)
    by_archive: dict[str, list[RpfEntryRecord]] = {}
    for entry in index.entries:
        if entry.kind != "directory":
            by_archive.setdefault(entry.archive_path, []).append(entry)
    result: list[_DiscoveredProgression] = []
    for archive_path, entries in by_archive.items():
        ytds = [item for item in entries if item.suffix == ".ytd"]
        ytyps = [item for item in entries if item.suffix == ".ytyp"]
        if len(ytds) != 1 or len(ytyps) != 1:
            continue
        ydr_by_stem = {Path(item.name).stem.casefold(): item for item in entries if item.suffix == ".ydr"}
        numbered: dict[str, dict[int, RpfEntryRecord]] = {}
        authored_prefix: dict[str, str] = {}
        for stem, entry in ydr_by_stem.items():
            match = _NUMBERED_MODEL.fullmatch(stem)
            if match is None:
                continue
            prefix = match.group("prefix")
            authored_prefix.setdefault(prefix, Path(entry.name).stem.rsplit("_", 1)[0])
            numbered.setdefault(prefix, {})[int(match.group("level"))] = entry
        families: dict[str, tuple[RpfEntryRecord, ...]] = {}
        level_counts: dict[int, list[str]] = {}
        for prefix, levels in numbered.items():
            if not levels or min(levels) != 1:
                continue
            maximum = max(levels)
            base = ydr_by_stem.get(prefix)
            final_level = maximum + 1 if base is not None else maximum
            if not 3 <= final_level <= _MAX_AUDIT_LEVELS:
                continue
            ordered = [levels.get(level) for level in range(1, final_level)]
            if any(item is None for item in ordered):
                continue
            if base is not None:
                ordered.append(base)
            else:
                ordered.append(levels[final_level])
            authored = authored_prefix.get(prefix, prefix)
            families[authored] = tuple(item for item in ordered if item is not None)
            level_counts.setdefault(final_level, []).append(authored)
        if not level_counts:
            continue
        levels = max(level_counts, key=lambda value: (len(level_counts[value]), value))
        selected = {
            name: families[name] for name in sorted(level_counts[levels], key=str.casefold)
        }
        result.append(_DiscoveredProgression(
            archive_path=archive_path,
            families=tuple(selected), levels=levels,
            model_entries=selected, ytd=ytds[0], ytyp=ytyps[0],
            inferred=archive_path.casefold() not in declared,
        ))
    return tuple(result)


def _run_batch_conversion(
    service: RpfExplorerService, inputs: list[Path], output: Path,
    edition: str,
) -> tuple[Path, ...]:
    xml_files: list[Path] = []
    rows: list[str] = []
    for number, source in enumerate(inputs, start=1):
        xml = output / f"{number:04d}{source.suffix}.xml"
        assets = output / f"assets-{number:04d}"
        xml_files.append(xml)
        rows.append(f"{source}\t{xml}\t{assets}\n")
    manifest = output / "asset-xml-batch.tsv"
    manifest.write_text("".join(rows), encoding="utf-8")
    completed = run_hidden(
        [
            service.patcher, "asset-xml-batch", manifest,
            "gen9" if edition.casefold() == "enhanced" else "legacy",
            service.gta_path,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode or any(not path.is_file() for path in xml_files):
        detail = (completed.stderr or completed.stdout or "conversion failed").strip()
        raise ValueError(f"Material progression conversion failed: {detail}")
    return tuple(xml_files)


def audit_material_progressions(
    service: RpfExplorerService,
    index: RpfIndex,
    workspace: str | Path,
    *,
    source: str,
    declarations: Iterable[VisualAssetProgression] = (),
) -> tuple[MaterialProgressionReport, ...]:
    """Decode and compare inferred or explicitly declared RPF material tiers."""
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    reports: list[MaterialProgressionReport] = []
    for report_number, discovered in enumerate(
        _discover(index, tuple(declarations)), start=1,
    ):
        report_root = root / f"progression-{report_number:02d}"
        report_root.mkdir()
        representative = discovered.families[0]
        models = discovered.model_entries[representative]
        selected = (*models, discovered.ytd, discovered.ytyp)
        extracted = service.extract_many(index, selected, report_root / "raw")
        conversion_root = report_root / "converted"
        conversion_root.mkdir()
        xml_files = _run_batch_conversion(
            service, list(extracted), conversion_root, index.edition,
        )
        model_xml = xml_files[:len(models)]
        ytd_xml = xml_files[-2]
        ytyp_xml = xml_files[-1]
        texture_assets = conversion_root / f"assets-{len(models) + 1:04d}"
        textures = {
            item.stem.casefold(): item
            for item in texture_assets.iterdir()
            if item.is_file() and item.suffix.casefold() == ".dds"
        }
        texture_count = len(_xml(ytd_xml).xpath("/*[local-name()='TextureDictionary']/*[local-name()='Item']"))
        archetype_count = len(_xml(ytyp_xml).xpath(".//*[local-name()='archetypes']/*[local-name()='Item']"))
        raw_tiers: list[dict[str, Any]] = []
        findings: list[MaterialProgressionFinding] = []
        previous_render: Image.Image | None = None
        for level, (model, xml_file) in enumerate(zip(models, model_xml), start=1):
            metrics = _model_metrics(xml_file)
            texture_path = textures.get(str(metrics["texture"]).casefold())
            texture_metrics: dict[str, float] = {}
            image: Image.Image | None = None
            render: Image.Image | None = None
            difference: float | None = None
            if texture_path is None:
                findings.append(MaterialProgressionFinding(
                    "error", "missing_texture_reference",
                    f"{model.name} references missing texture {metrics['texture'] or '—'}.",
                    level,
                ))
            else:
                texture_metrics, image = _texture_metrics(texture_path)
                render = _approximate_material(image, metrics["emissive"])
                if previous_render is not None:
                    difference = _visual_difference(previous_render, render)
                previous_render = render
            raw_tiers.append({
                "level": level, "model": model.name, **metrics,
                **texture_metrics, "image": image, "render": render,
                "difference": difference,
            })

        first = raw_tiers[0]
        if first.get("alpha_min") is not None and first["alpha_min"] > 0.15:
            findings.append(MaterialProgressionFinding(
                "warning", "first_tier_alpha_floor_high",
                f"First-tier minimum alpha is {first['alpha_min']:.3f}; the entire "
                "surface may become visibly active at the start of the progression.", 1,
            ))
        if first.get("luminance_min") is not None and first["luminance_min"] > 0.08:
            findings.append(MaterialProgressionFinding(
                "warning", "first_tier_luminance_floor_high",
                f"First-tier minimum luminance is {first['luminance_min']:.3f}.", 1,
            ))
        bindings = [str(item["texture"]).casefold() for item in raw_tiers]
        if len(set(bindings)) != len(bindings):
            findings.append(MaterialProgressionFinding(
                "warning", "identical_texture_bindings",
                "Multiple fade tiers bind the same texture name.",
            ))
        reference_geometry = (
            first["geometry_count"], first["vertex_count"], first["triangle_count"],
        )
        reference_shader = first["shader"]
        previous_emissive: float | None = None
        previous_alpha: float | None = None
        for item in raw_tiers:
            level = int(item["level"])
            emissive = item["emissive"]
            alpha_mean = item.get("alpha_mean")
            if previous_emissive is not None and emissive is not None and emissive + 1e-7 < previous_emissive:
                findings.append(MaterialProgressionFinding(
                    "warning", "non_monotonic_emissive",
                    "Emissive multiplier decreases from the previous tier.", level,
                ))
            if previous_alpha is not None and alpha_mean is not None and alpha_mean + 1e-4 < previous_alpha:
                findings.append(MaterialProgressionFinding(
                    "warning", "non_monotonic_alpha",
                    "Mean texture alpha decreases from the previous tier.", level,
                ))
            if emissive is not None:
                previous_emissive = emissive
            if alpha_mean is not None:
                previous_alpha = alpha_mean
            geometry = (item["geometry_count"], item["vertex_count"], item["triangle_count"])
            if geometry != reference_geometry:
                findings.append(MaterialProgressionFinding(
                    "warning", "tier_geometry_changed",
                    "Geometry topology changes between visual tiers.", level,
                ))
            if item["shader"] != reference_shader:
                findings.append(MaterialProgressionFinding(
                    "warning", "tier_shader_changed",
                    "Resolved shader changes between visual tiers.", level,
                ))
            if level > 1 and item.get("difference") is not None and item["difference"] < 0.002:
                findings.append(MaterialProgressionFinding(
                    "info", "neighboring_tiers_too_similar",
                    f"Approximate visual difference from tier {level - 1} is only "
                    f"{item['difference']:.4f}.", level,
                ))
        expected_models = len(discovered.families) * discovered.levels
        actual_models = sum(len(items) for items in discovered.model_entries.values())
        if actual_models != expected_models:
            findings.append(MaterialProgressionFinding(
                "error", "progression_model_count_mismatch",
                f"Expected {expected_models} progression models but resolved {actual_models}.",
            ))
        if texture_count != discovered.levels:
            findings.append(MaterialProgressionFinding(
                "warning", "progression_texture_count_mismatch",
                f"Expected {discovered.levels} textures but decoded {texture_count}.",
            ))
        if archetype_count != expected_models:
            findings.append(MaterialProgressionFinding(
                "warning", "progression_archetype_count_mismatch",
                f"Expected {expected_models} archetypes but decoded {archetype_count}.",
            ))
        preview, preview_hash, width, height = _progression_strip(raw_tiers)
        tiers = tuple(MaterialTier(
            level=item["level"], model=item["model"], texture=item["texture"],
            shader=item["shader"], authored_shader=item["authored_shader"],
            emissive_multiplier=item["emissive"],
            geometry_count=item["geometry_count"],
            vertex_count=item["vertex_count"], triangle_count=item["triangle_count"],
            alpha_min=item.get("alpha_min"), alpha_max=item.get("alpha_max"),
            alpha_mean=item.get("alpha_mean"),
            luminance_min=item.get("luminance_min"),
            luminance_max=item.get("luminance_max"),
            luminance_mean=item.get("luminance_mean"),
            neighboring_visual_difference=item.get("difference"),
            texture_missing=item.get("image") is None,
        ) for item in raw_tiers)
        reports.append(MaterialProgressionReport(
            source=source, archive_path=discovered.archive_path,
            representative_family=representative,
            families=discovered.families, levels=discovered.levels,
            model_count=actual_models,
            texture_dictionary=discovered.ytd.name, texture_count=texture_count,
            archetype_dictionary=discovered.ytyp.name,
            archetype_count=archetype_count, inferred=discovered.inferred,
            tiers=tiers, findings=tuple(findings), preview_sha256=preview_hash,
            preview_width=width, preview_height=height, preview_png=preview,
        ))
    return tuple(reports)

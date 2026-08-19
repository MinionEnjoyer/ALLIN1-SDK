"""Hash-bound visual previews for RPF package graph asset nodes."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from allin1_sdk.native_assets import MAX_NATIVE_PREVIEW_BYTES, NativeAssetInspector
from allin1_sdk.rpf_graph import RpfPackageGraph


ASSET_PREVIEW_WIDTH = 84
ASSET_PREVIEW_HEIGHT = 46
DIRECT_IMAGE_SUFFIXES = frozenset({
    ".bmp", ".dds", ".gif", ".jpeg", ".jpg", ".png", ".tga", ".webp",
})
NATIVE_VISUAL_SUFFIXES = frozenset({
    ".ybn", ".ydd", ".ydr", ".yft", ".ymap", ".ynd", ".ynv", ".ytd", ".ytyp",
})
TEXT_PREVIEW_SUFFIXES = frozenset({
    ".cfg", ".ini", ".json", ".lua", ".md", ".meta", ".toml", ".txt", ".xml",
})


@dataclass(frozen=True)
class AssetPreviewRequest:
    node_id: str
    source: Path
    expected_size: int
    expected_sha256: str
    edition: str
    cache_key: str


def fit_asset_thumbnail(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        image = ImageOps.exif_transpose(source).convert("RGBA")
    image.thumbnail(
        (ASSET_PREVIEW_WIDTH - 4, ASSET_PREVIEW_HEIGHT - 4),
        Image.Resampling.LANCZOS,
    )
    background = Image.new(
        "RGBA", (ASSET_PREVIEW_WIDTH, ASSET_PREVIEW_HEIGHT), "#0C1512",
    )
    background.alpha_composite(
        image,
        ((ASSET_PREVIEW_WIDTH - image.width) // 2,
         (ASSET_PREVIEW_HEIGHT - image.height) // 2),
    )
    output = io.BytesIO()
    background.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def fallback_asset_thumbnail(name: str, data: bytes) -> bytes:
    suffix = Path(name).suffix.upper().lstrip(".") or "FILE"
    digest = hashlib.sha256(data[:1_048_576]).digest()
    color = (35 + digest[0] % 70, 78 + digest[1] % 75, 82 + digest[2] % 90)
    image = Image.new("RGB", (ASSET_PREVIEW_WIDTH, ASSET_PREVIEW_HEIGHT), "#0C1512")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (1, 1, ASSET_PREVIEW_WIDTH - 2, ASSET_PREVIEW_HEIGHT - 2),
        radius=5, fill=color, outline="#A8C8B7",
    )
    if Path(name).suffix.casefold() in TEXT_PREVIEW_SUFFIXES:
        text = data[:4096].decode("utf-8", errors="replace")
        lengths = [len(line.strip()) for line in text.splitlines() if line.strip()][:5]
        for index, length in enumerate(lengths):
            width = 10 + min(52, max(5, length))
            draw.rounded_rectangle(
                (8, 8 + index * 6, 8 + width, 11 + index * 6),
                radius=1, fill="#D9EEE3",
            )
        label_y = 34
    else:
        label_y = 17
    draw.text((7, label_y), suffix[:9], fill="#FFFFFF")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_asset_preview(
    request: AssetPreviewRequest, project_root: Path,
    game_path: Path | None,
) -> bytes:
    """Render one bounded preview only when its graph source binding still matches."""
    if request.source.is_symlink():
        raise ValueError("source is a symbolic link")
    source = request.source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("source is not a regular file")
    if not 0 < request.expected_size <= MAX_NATIVE_PREVIEW_BYTES:
        raise ValueError("source exceeds the guarded preview limit")
    if source.stat().st_size != request.expected_size:
        raise ValueError("source size changed")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != request.expected_sha256:
        raise ValueError("source hash changed")
    suffix = source.suffix.casefold()
    preview: bytes | None = None
    if suffix in DIRECT_IMAGE_SUFFIXES:
        preview = fit_asset_thumbnail(data)
    elif suffix in NATIVE_VISUAL_SUFFIXES:
        report = NativeAssetInspector(
            project_root, game_path,
        ).inspect_bytes(source.name, data, edition=request.edition)
        if report.image_png:
            preview = fit_asset_thumbnail(report.image_png)
    return preview or fallback_asset_thumbnail(source.name, data)


def render_graph_preview_bundle(
    graph: str | Path, destination: str | Path, project_root: str | Path,
    *, game_path: str | Path | None = None, limit: int = 2500,
) -> tuple[Path, Path]:
    """Publish hash-bound node thumbnails and a portable report atomically."""
    if not 1 <= limit <= 2500:
        raise ValueError("Asset preview limit must be between 1 and 2,500")
    authored_graph = Path(graph).expanduser()
    if authored_graph.is_symlink():
        raise ValueError("RPF package graph cannot be a symbolic link")
    graph_path = authored_graph.resolve(strict=True)
    graph_bytes = graph_path.read_bytes()
    graph_sha256 = hashlib.sha256(graph_bytes).hexdigest()
    state = RpfPackageGraph.validate(graph_path, verify_sources=True)
    target = Path(destination).expanduser().resolve()
    if target.exists() or target.is_symlink():
        raise ValueError(f"Asset preview destination already exists: {target}")
    selected_game = (
        Path(game_path).expanduser().resolve() if game_path is not None else None
    )
    if selected_game is not None:
        if not selected_game.is_dir():
            raise ValueError(f"GTA installation was not found: {selected_game}")
        if target == selected_game or target.is_relative_to(selected_game):
            raise ValueError("Asset previews cannot be published inside GTA V")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".{target.name}.allin1-stage-", dir=target.parent,
    )).resolve()
    published = False
    try:
        preview_dir = staging / "previews"
        preview_dir.mkdir()
        origin = state.get("payload", {}).get("origin", {})
        authored_edition = origin.get("edition") if isinstance(origin, dict) else None
        edition = (
            authored_edition.title()
            if isinstance(authored_edition, str)
            and authored_edition.casefold() in {"legacy", "enhanced"}
            else (
                "Legacy" if selected_game is not None
                and (selected_game / "GTA5.exe").is_file() else "Enhanced"
            )
        )
        files = [
            node for node in state["nodes"].values() if node["type"] == "file"
        ]
        records: list[dict] = []
        for node in files[:limit]:
            node_id = str(node["id"])
            request = AssetPreviewRequest(
                node_id=node_id,
                source=Path(str(node["source"])),
                expected_size=int(node["size"]),
                expected_sha256=str(node["sha256"]).casefold(),
                edition=edition,
                cache_key=hashlib.sha256(
                    f"{node['source']}|{node['size']}|{node['sha256']}|{edition}".encode(
                        "utf-8"
                    )
                ).hexdigest(),
            )
            record = {
                "node_id": node_id, "name": node["name"],
                "source": str(request.source), "source_size": request.expected_size,
                "source_sha256": request.expected_sha256,
            }
            try:
                preview = render_asset_preview(
                    request, Path(project_root).resolve(), selected_game,
                )
                output = preview_dir / f"{node_id}.png"
                output.write_bytes(preview)
                record.update({
                    "status": "rendered",
                    "preview": output.relative_to(staging).as_posix(),
                    "preview_size": len(preview),
                    "preview_sha256": hashlib.sha256(preview).hexdigest(),
                })
            except (
                OSError, RuntimeError, ValueError, Image.DecompressionBombError,
            ) as exc:
                record.update({"status": "failed", "error": str(exc)})
            records.append(record)
        if hashlib.sha256(graph_path.read_bytes()).hexdigest() != graph_sha256:
            raise RuntimeError("RPF package graph changed while previews were rendered")
        RpfPackageGraph.validate(graph_path, verify_sources=True)
        rendered = sum(item["status"] == "rendered" for item in records)
        report = {
            "schema_version": 1,
            "operation": "rpf_graph_asset_preview_bundle",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "graph": str(graph_path), "graph_sha256": graph_sha256,
            "edition": edition,
            "game_path_stored": False,
            "summary": {
                "graph_files": len(files), "processed": len(records),
                "rendered": rendered, "failed": len(records) - rendered,
                "truncated": len(files) > limit,
            },
            "assets": records,
        }
        report_path = staging / "preview-report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        staging.replace(target)
        published = True
        return target, target / report_path.name
    finally:
        if not published and staging.is_dir() and staging.parent == target.parent:
            shutil.rmtree(staging)

"""Regenerate the SDK's official vehicle model/hash reservation snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORE_ROOT = SDK_ROOT.parent / "ALLIN1"
DEFAULT_OUTPUT = SDK_ROOT / "src" / "allin1_sdk" / "official_vehicle_models.py"
SOURCE_PATHS = ("data/vehicles.toml", "data/story_vehicles.json")


def _load_snapshot(core_root: Path) -> tuple[str, dict[str, str], list[tuple[str, int]]]:
    core_root = core_root.resolve()
    sys.path.insert(0, str(core_root / "src"))
    try:
        from allin1.vehicle_catalog import VehicleCatalog, vehicle_model_hash
        from allin1.vehicles.database import VehicleDatabase
    finally:
        sys.path.pop(0)

    online = VehicleDatabase.load(core_root / SOURCE_PATHS[0])
    story = VehicleCatalog.load(core_root / SOURCE_PATHS[1])
    models = sorted(
        {vehicle.model.casefold() for vehicle in online.all_vehicles}
        | {vehicle.model.casefold() for vehicle in story.vehicles}
    )
    hashes = [vehicle_model_hash(model) for model in models]
    if len(hashes) != len(set(hashes)):
        raise ValueError("Official vehicle catalogs contain a duplicate Jenkins hash")
    project = tomllib.loads((core_root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    sources = {
        path: hashlib.sha256((core_root / path).read_bytes()).hexdigest()
        for path in SOURCE_PATHS
    }
    return version, sources, list(zip(models, hashes, strict=True))


def _render_snapshot(
    version: str, sources: dict[str, str], pairs: list[tuple[str, int]]
) -> str:
    lines = [
        '"""Generated official GTA V vehicle model/hash reservation snapshot.',
        "",
        "Regenerate with ``tools/generate_official_vehicle_snapshot.py`` from a sibling",
        "ALLIN1 checkout. Do not edit this module by hand.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "SNAPSHOT_SCHEMA_VERSION = 1",
        f"SOURCE_VERSION = {json.dumps(version)}",
        "SOURCE_FILES = {",
    ]
    lines.extend(
        f"    {json.dumps(path)}: {json.dumps(digest)},"
        for path, digest in sources.items()
    )
    lines.extend([
        "}",
        "",
        "OFFICIAL_VEHICLE_MODEL_HASH_PAIRS: tuple[tuple[str, int], ...] = (",
    ])
    lines.extend(
        f"    ({json.dumps(model)}, 0x{model_hash:08X}),"
        for model, model_hash in pairs
    )
    lines.extend([
        ")",
        "",
        "OFFICIAL_VEHICLE_MODELS = frozenset(",
        "    model for model, _model_hash in OFFICIAL_VEHICLE_MODEL_HASH_PAIRS",
        ")",
        "OFFICIAL_VEHICLE_HASHES = frozenset(",
        "    model_hash for _model, model_hash in OFFICIAL_VEHICLE_MODEL_HASH_PAIRS",
        ")",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-root", type=Path, default=DEFAULT_CORE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero instead of rewriting when the snapshot is stale.",
    )
    args = parser.parse_args()
    rendered = _render_snapshot(*_load_snapshot(args.core_root))
    current = args.output.read_text(encoding="utf-8") if args.output.is_file() else None
    if args.check:
        if current != rendered:
            print(f"Official vehicle snapshot is stale: {args.output}", file=sys.stderr)
            return 1
        print(f"Official vehicle snapshot is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

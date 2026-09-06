"""Keep the versioned React format matrix aligned with the actual adapters."""
import json
from pathlib import Path

from allin1_sdk.native_assets import NATIVE_ASSET_SUFFIXES, NATIVE_XML_IMPORT_SUFFIXES


def test_format_matrix_covers_all_native_routes_without_claiming_universal_acceptance():
    root = Path(__file__).resolve().parents[1]
    matrix = json.loads((root / "desktop/src/formatCapabilities.json").read_text())
    assert matrix["schemaVersion"] == 1 and matrix["revision"]
    rows = matrix["formats"]
    assert len({row["suffix"] for row in rows}) == len(rows)
    assert {row["suffix"] for row in rows} == NATIVE_ASSET_SUFFIXES
    assert {row["suffix"] for row in rows if row["nativeXmlEdit"]} == NATIVE_XML_IMPORT_SUFFIXES
    for row in rows:
        for field in ("label", "inspection", "preview", "export", "editing", "rebuild", "limitations"):
            assert isinstance(row[field], str) and row[field].strip()
        assert set(row["editionEvidence"]) == {"Legacy", "Enhanced"}
        assert all(row["editionEvidence"].values())
        assert (root / row["implementation"]).is_file()
        for reference in row["evidence"]:
            assert (root / reference).is_file()
    animation = next(row for row in rows if row["suffix"] == ".ycd")
    assert "not implemented" in animation["limitations"]
    assert "skinning" in animation["preview"] and "XML" in animation["preview"]
    assert "tests/test_animation_model.py" in animation["evidence"]
    gfx = next(row for row in rows if row["suffix"] == ".gfx")
    assert gfx["rebuild"] == "No semantic rebuild"

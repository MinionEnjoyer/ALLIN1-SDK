"""Source-contract guard for loose RSC7 YMT XML-import classification.

This intentionally does not carry a third-party native YMT fixture.  Native
round-trip acceptance is covered by local authoring probes; this guard keeps
the loader choice that makes a loose RSC7 source classify as META rather than
as an untyped binary entry.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "RpfPatcher" / "Program.cs"


def _ymt_source_branch(source: str) -> str:
    marker = 'else if (suffix == ".ymt")'
    start = source.index(marker)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("Unterminated YMT source-classification branch")


def test_loose_rsc7_ymt_routes_to_the_native_classifier_contract():
    """Keep the CLI wired to the native synthetic-RSC regression helper."""
    branch = _ymt_source_branch(SOURCE.read_text(encoding="utf-8"))
    assert "RpfFile.GetFile<YmtFile>" not in branch
    assert "ClassifyLooseYmtSource(sourceData, out meta, out pso, out rbf);" in branch

    helper_start = SOURCE.read_text(encoding="utf-8").index("static void ClassifyLooseYmtSource(")
    helper = SOURCE.read_text(encoding="utf-8")[helper_start:]
    assert "RbfFile.IsRBF(stream)" in helper
    assert "PsoFile.IsPSO(stream)" in helper
    assert "var parsed = new YmtFile();" in helper
    assert "parsed.Load(sourceData);" in helper
    assert "meta = parsed.Meta;" in helper

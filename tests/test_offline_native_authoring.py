"""Explicit native dependency gates; never substitute fixture success for them."""
import os
from pathlib import Path
import runpy

import pytest


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Requires the actual built RpfPatcher/CodeWalker native dependency")
def test_real_map_graph_flow_and_archive_binary_lifecycle_in_disposable_roots():
    script = Path(__file__).resolve().parents[1] / "scripts" / "smoke_offline_authoring.py"
    runpy.run_path(str(script))["main"]()

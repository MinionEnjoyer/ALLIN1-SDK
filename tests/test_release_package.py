"""Self-contained SDK release packaging contract."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.package_release import _build_id, _validate_example_sources, package_release


def test_release_package_contains_launcher_contract_and_checksums(tmp_path):
    root = tmp_path / "source"
    app = tmp_path / "app"
    rpf = tmp_path / "rpf"
    output = tmp_path / "output"
    (root / "sdk").mkdir(parents=True)
    (root / "assets").mkdir(parents=True)
    app.mkdir()
    rpf.mkdir()
    (root / "sdk" / "addon.schema.json").write_text("{}")
    (root / "assets" / "ALLIN1_SDK.png").write_bytes(b"png")
    (root / "assets" / "favicon.ico").write_bytes(b"ico")
    (root / "README.md").write_text("SDK")
    (root / "LICENSE").write_text("GPL")
    (app / "ALLIN1-SDK-Desktop.exe").write_bytes(b"MZapp")
    (app / "allin1-sdk.exe").write_bytes(b"MZcli")
    (app / "ALLIN1-SDK-Agent.exe").write_bytes(b"MZagent")
    (app / "_internal").mkdir()
    (app / "_internal" / "python312.dll").write_bytes(b"runtime")
    (rpf / "RpfPatcher.exe").write_bytes(b"MZhelper")

    build_id = "0123456789abcdef0123456789abcdef01234567"
    archive, checksum = package_release(
        root, app, rpf, output, "0.5.0", build_id,
    )

    assert archive.name == "ALLIN1-SDK-0.5.0-win-x64.zip"
    assert checksum.read_text().startswith(hashlib.sha256(archive.read_bytes()).hexdigest())
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert {
            "ALLIN1-SDK-Desktop.exe", "allin1-sdk.exe", "ALLIN1-SDK-Agent.exe",
            "release.json", "checksums.json",
            "sdk/addon.schema.json", "tools/RpfPatcher/RpfPatcher.exe",
            "assets/ALLIN1_SDK.png", "assets/favicon.ico",
        } <= names
        metadata = json.loads(package.read("release.json"))
        checksums = json.loads(package.read("checksums.json"))
        assert metadata == {
            "build_id": build_id,
            "entrypoint": "ALLIN1-SDK-Desktop.exe", "platform": "win-x64",
            "cli_entrypoint": "allin1-sdk.exe",
            "agent_entrypoint": "ALLIN1-SDK-Agent.exe",
            "product": "ALLIN1-SDK", "version": "0.5.0",
        }
        assert set(checksums) == names - {"checksums.json"}
        assert all(
            hashlib.sha256(package.read(name)).hexdigest() == digest
            for name, digest in checksums.items()
        )


@pytest.mark.parametrize("value", ("", "contains spaces", "../escape", "x" * 129))
def test_release_build_id_rejects_ambiguous_or_unsafe_values(value):
    with pytest.raises(argparse.ArgumentTypeError, match="invalid release build ID"):
        _build_id(value)


def test_release_workflow_binds_package_identity_to_github_commit():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-release.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count('--build-id "$env:GITHUB_SHA"') == 2


def test_release_rejects_bundled_example_with_missing_source(tmp_path):
    root = tmp_path / "source"
    example = root / "sdk" / "examples" / "broken"
    example.mkdir(parents=True)
    (example / "addon.json").write_text(json.dumps({
        "nodes": [{"id": "node", "source": "tools/missing.cs"}],
        "install_steps": [{"id": "step", "source": "tools/missing.cs"}],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="missing source tools.missing.cs"):
        _validate_example_sources(root)

"""Self-contained SDK release packaging contract."""

import hashlib
import json
import zipfile

from scripts.package_release import package_release


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
    (app / "ALLIN1-SDK.exe").write_bytes(b"MZapp")
    (app / "ALLIN1-SDK-Agent.exe").write_bytes(b"MZagent")
    (app / "_internal").mkdir()
    (app / "_internal" / "python312.dll").write_bytes(b"runtime")
    (rpf / "RpfPatcher.exe").write_bytes(b"MZhelper")

    archive, checksum = package_release(root, app, rpf, output, "0.4.9")

    assert archive.name == "ALLIN1-SDK-0.4.9-win-x64.zip"
    assert checksum.read_text().startswith(hashlib.sha256(archive.read_bytes()).hexdigest())
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert {
            "ALLIN1-SDK.exe", "ALLIN1-SDK-Agent.exe", "release.json", "checksums.json",
            "sdk/addon.schema.json", "tools/RpfPatcher/RpfPatcher.exe",
            "assets/ALLIN1_SDK.png", "assets/favicon.ico",
        } <= names
        metadata = json.loads(package.read("release.json"))
        checksums = json.loads(package.read("checksums.json"))
        assert metadata == {
            "entrypoint": "ALLIN1-SDK.exe", "platform": "win-x64",
            "agent_entrypoint": "ALLIN1-SDK-Agent.exe",
            "product": "ALLIN1-SDK", "version": "0.4.9",
        }
        assert set(checksums) == names - {"checksums.json"}
        assert all(
            hashlib.sha256(package.read(name)).hexdigest() == digest
            for name, digest in checksums.items()
        )

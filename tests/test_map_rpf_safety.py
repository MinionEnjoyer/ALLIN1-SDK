from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from allin1_sdk.addon_importer import PackageEntry, PackageFinding, PackageScan
from allin1_sdk.map_project import MapProjectResolver
from allin1_sdk.map_rpf_safety import inspect_startup_map_registration


def _documents(
    archive_paths: list[str], *, group: str = "GROUP_STARTUP",
) -> tuple[tuple[str, ET.Element], ...]:
    data_files = "".join(
        "<Item>"
        f"<filename>dlc_allin1_maps:/%PLATFORM%/{path[4:]}</filename>"
        "<fileType>RPF_FILE</fileType><disabled value='true'/>"
        + (
            "<contents>CONTENTS_DLC_MAP_DATA</contents>"
            if "placement" in path else ""
        )
        + "</Item>"
        for path in archive_paths
    )
    enabled = "".join(
        f"<Item>dlc_allin1_maps:/%PLATFORM%/{path[4:]}</Item>"
        for path in archive_paths
    )
    content = ET.fromstring(
        "<CDataFileMgr__ContentsOfDataFileXml>"
        f"<dataFiles>{data_files}</dataFiles>"
        "<contentChangeSets><Item>"
        "<changeSetName>ALLIN1_MAPS_AUTOGEN</changeSetName>"
        f"<filesToEnable>{enabled}</filesToEnable>"
        "</Item></contentChangeSets>"
        "</CDataFileMgr__ContentsOfDataFileXml>"
    )
    setup = ET.fromstring(
        "<SSetupData><contentChangeSetGroups><Item>"
        f"<NameHash>{group}</NameHash>"
        "<ContentChangeSets><Item>ALLIN1_MAPS_AUTOGEN</Item>"
        "</ContentChangeSets></Item></contentChangeSetGroups></SSetupData>"
    )
    return (("::content.xml", content), ("::setup2.xml", setup))


def _index(archive_paths: list[str]) -> SimpleNamespace:
    archives = [SimpleNamespace(path="")]
    entries = []
    for number, path in enumerate(archive_paths):
        archives.append(SimpleNamespace(path=path))
        # The observed allin1_maps pack owned 187 YMAPs across copied interior
        # and yacht archives. A small deterministic distribution preserves the
        # decisive topology without checking a 170 MiB binary into the repo.
        entries.append(SimpleNamespace(
            archive_path=path,
            suffix=".ymap" if number % 2 == 0 else ".ydr",
        ))
        if "placement" in path:
            entries.append(SimpleNamespace(archive_path=path, suffix=".ymap"))
    return SimpleNamespace(archives=tuple(archives), entries=tuple(entries))


def _allin1_map_archives() -> list[str]:
    return [
        "x64/levels/gta5/interiors/dlc_garage_high_new.rpf",
        "x64/levels/gta5/interiors/dlc_int_01_tr.rpf",
        "x64/levels/gta5/interiors/int_02_ba.rpf",
        "x64/levels/gta5/interiors/int_03.rpf",
        "x64/levels/gta5/interiors/int_placement.rpf",
        "x64/levels/gta5/interiors/int_placement_ba.rpf",
        "x64/levels/gta5/interiors/int_placement_tr.rpf",
        "x64/levels/gta5/interiors/int_placement_vw.rpf",
        "x64/levels/gta5/interiors/mpheist_yacht.rpf",
        "x64/levels/gta5/interiors/vwdlc_int_03.rpf",
        "x64/levels/gta5/_hills/cityhills_01/ch1_yacht.rpf",
        "x64/levels/gta5/_hills/cityhills_01/ch1_yacht_lod.rpf",
        "x64/levels/gta5/_hills/cityhills_01/yacht.rpf",
        "x64/levels/gta5/_hills/cityhills_01/yacht_metadata.rpf",
        "x64/levels/gta5/_citye/hollywood_01/hollywood_metadata.rpf",
        "x64/levels/gta5/_citye/hollywood_01/hw1_blimp.rpf",
    ]


def test_bulk_group_startup_map_registration_is_a_blocking_finding() -> None:
    archives = _allin1_map_archives()

    findings = inspect_startup_map_registration(
        _index(archives), _documents(archives),
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "error"
    assert finding.code == "rpf_map_startup_bulk_enable"
    assert "16/16 indexed nested RPFs" in finding.message
    assert "GROUP_STARTUP" in finding.message
    assert "YMAP entries" in finding.message


def test_non_startup_map_change_set_is_not_misreported() -> None:
    archives = _allin1_map_archives()

    findings = inspect_startup_map_registration(
        _index(archives), _documents(archives, group="GROUP_MAP"),
    )

    assert findings == ()


def test_small_startup_map_set_remains_visible_as_a_review_warning() -> None:
    archives = ["x64/levels/gta5/interiors/int_placement.rpf"]

    findings = inspect_startup_map_registration(
        _index(archives), _documents(archives),
    )

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].code == "rpf_map_startup_eager_enable"


def test_map_report_promotes_startup_safety_findings_for_cli_and_workbench(
    tmp_path: Path,
) -> None:
    scan = PackageScan(
        source=tmp_path / "dlc.rpf",
        source_kind="rpf",
        entries=(PackageEntry("fixture.ymap", 64),),
        findings=(PackageFinding(
            "error", "rpf_map_startup_bulk_enable",
            "GROUP_STARTUP enables every nested placement archive.",
            "::content.xml",
        ),),
        weapons=(), ammo=(), animation_weapons=(), shop_weapons=(),
    )

    report = MapProjectResolver.inspect_scan(scan)

    assert not report.valid
    assert report.error_count == 1
    assert report.findings[0].to_dict() == {
        "severity": "error",
        "code": "package_rpf_map_startup_bulk_enable",
        "message": "GROUP_STARTUP enables every nested placement archive.",
        "path": "::content.xml",
    }


def test_map_report_does_not_hide_small_startup_review_warning(tmp_path: Path) -> None:
    scan = PackageScan(
        source=tmp_path / "dlc.rpf",
        source_kind="rpf",
        entries=(PackageEntry("fixture.ymap", 64),),
        findings=(PackageFinding(
            "warning", "rpf_map_startup_eager_enable",
            "One placement archive is enabled during startup.",
            "::content.xml",
        ),),
        weapons=(), ammo=(), animation_weapons=(), shop_weapons=(),
    )

    report = MapProjectResolver.inspect_scan(scan)

    assert report.valid
    assert "package_rpf_map_startup_eager_enable" in {
        finding.code for finding in report.findings
    }

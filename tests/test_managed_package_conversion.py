from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from allin1_sdk.addon_importer import (
    PackageEntry,
    PackageRegistrationRecord,
    PackageScan,
    RpfPackageRecord,
    VehicleRecord,
)
from allin1_sdk.managed_package_conversion import ManagedVehiclePackageConverter
from allin1_sdk.mods import ModManifest, open_mod_package
from allin1_sdk.vehicle_catalog import VehicleCatalog


LEGACY_RPF = b"legacy-rpf-fixture"
ENHANCED_RPF = b"enhanced-rpf-fixture"


def _source_archive(root: Path) -> Path:
    source = root / "pagani-fixture.zip"
    with zipfile.ZipFile(source, "w") as package:
        package.writestr("Legacy/lunga/dlc.rpf", LEGACY_RPF)
        package.writestr("Enhanced/lunga/dlc.rpf", ENHANCED_RPF)
    return source


def _vehicle(edition: str, source: str) -> VehicleRecord:
    return VehicleRecord(
        source=source,
        model_name="lunga",
        txd_name="lunga",
        handling_id="lunga",
        game_name="LUNGA",
        make_name="PAGANI",
        audio_name_hash="T20",
        layout="LAYOUT_STANDARD",
        vehicle_type="VEHICLE_TYPE_CAR",
        vehicle_class="VC_SUPER",
        edition=edition,
    )


def _scan(source: Path, *, enhanced_members: int = 1, registration: bool = True):
    entries = [
        PackageEntry("Legacy/lunga/dlc.rpf", len(LEGACY_RPF)),
        PackageEntry("Enhanced/lunga/dlc.rpf", len(ENHANCED_RPF)),
    ]
    archives = [
        RpfPackageRecord("Legacy/lunga/dlc.rpf", "legacy", 2, 12, {".yft": 1}),
        RpfPackageRecord("Enhanced/lunga/dlc.rpf", "enhanced", 2, 10, {".yft": 1}),
    ]
    if enhanced_members == 2:
        entries.append(PackageEntry("Enhanced/other/dlc.rpf", len(ENHANCED_RPF)))
        archives.append(
            RpfPackageRecord(
                "Enhanced/other/dlc.rpf", "enhanced", 2, 10, {".yft": 1},
            )
        )
    registrations = ()
    if registration:
        registrations = (
            PackageRegistrationRecord(
                "Legacy/lunga/dlc.rpf!content.xml",
                "single-player-content", ("dlc_lunga",), ("vehicles.meta",),
            ),
            PackageRegistrationRecord(
                "Enhanced/lunga/dlc.rpf!content.xml",
                "single-player-content", ("dlc_lunga",), ("vehicles.meta",),
            ),
        )
    return PackageScan(
        source=source,
        source_kind="zip",
        entries=tuple(entries),
        findings=(),
        weapons=(),
        ammo=(),
        animation_weapons=(),
        shop_weapons=(),
        vehicles=(
            _vehicle(
                "legacy",
                "Legacy/lunga/dlc.rpf!common/data/levels/gta5/vehicles.meta",
            ),
            _vehicle("enhanced", "Enhanced/lunga/dlc.rpf!data/vehicles.meta"),
        ),
        registrations=registrations,
        rpf_archives=tuple(archives),
    )


class _Inspector:
    def __init__(self, scan: PackageScan) -> None:
        self.scan = scan

    def inspect(self, source: Path) -> PackageScan:
        assert source.resolve() == self.scan.source.resolve()
        return self.scan


def _converter(root: Path, source: Path, scan: PackageScan):
    project = root / "sdk"
    game = root / "game"
    project.mkdir(exist_ok=True)
    game.mkdir(exist_ok=True)
    return ManagedVehiclePackageConverter(
        project, game, inspector=_Inspector(scan),
    )


def test_dual_edition_conversion_selects_one_exact_branch_and_validates_schema_2(
    tmp_path: Path,
):
    source = _source_archive(tmp_path)
    converter = _converter(tmp_path, source, _scan(source))

    plan = converter.plan(source, edition="enhanced")
    assert plan.source_member == "Enhanced/lunga/dlc.rpf"
    assert plan.edition == "enhanced"
    assert plan.vehicles == ("lunga",)
    assert plan.dlc_pack == "lunga"
    assert plan.destination == "mods/update/x64/dlcpacks/lunga/dlc.rpf"
    assert plan.source_member_sha256 == hashlib.sha256(ENHANCED_RPF).hexdigest()
    assert plan.registered_package_names == ("dlc_lunga",)
    assert plan.registration_sources == (
        "Enhanced/lunga/dlc.rpf!content.xml",
    )

    result = converter.export(plan, tmp_path / "managed-enhanced")
    assert result.payload_path.read_bytes() == ENHANCED_RPF
    assert LEGACY_RPF not in result.payload_path.read_bytes()
    manifest = ModManifest.load(result.manifest_path)
    assert manifest.schema_version == 2
    assert manifest.editions == ("enhanced",)
    assert manifest.dependencies == ("openrpf",)
    assert manifest.dlc_packs == ("lunga",)
    assert manifest.files[0].sha256 == plan.source_member_sha256
    assert manifest.extension is not None

    content = json.loads(result.content_path.read_text(encoding="utf-8"))
    assert content["id"] == plan.package_id
    assert content["capabilities"] == ["gbay.catalogs"]
    assert content["gbay"]["catalogs"][0]["kind"] == "vehicle"
    assert content["systems"][0]["enabled_by_default"] is True
    catalog = json.loads(result.catalog_path.read_text(encoding="utf-8"))
    assert catalog["vehicles"][0]["model"] == "lunga"
    assert catalog["vehicles"][0]["traffic"]["enabled"] is False
    review = json.loads(result.review_path.read_text(encoding="utf-8"))
    assert review["source"] == source.name
    assert str(tmp_path) not in result.review_path.read_text(encoding="utf-8")
    assert review["install_performed"] is False
    assert result.launcher_contract == {
        "valid": True,
        "schema_version": 2,
        "id": "imported.lunga.enhanced",
        "type": "mixed",
        "editions": ["enhanced"],
        "dependencies": ["openrpf"],
        "dlc_packs": ["lunga"],
        "files": 2,
        "allin1_extension": True,
        "payload_sha256": hashlib.sha256(ENHANCED_RPF).hexdigest(),
        "catalog_sha256": result.launcher_contract["catalog_sha256"],
        "traffic_opt_in": False,
    }

    first = converter.publish(result.package_root, tmp_path / "release-a.zip")
    second = converter.publish(result.package_root, tmp_path / "release-b.zip")
    assert first.archive_sha256 == second.archive_sha256
    assert first.archive_size == second.archive_size
    assert first.members == (
        "allin1.content.json",
        "allin1.review.json",
        "mod.toml",
        "payload/dlc.rpf",
        "payload/vehicles.json",
    )
    with zipfile.ZipFile(first.archive) as archive:
        assert tuple(item.filename for item in archive.infolist()) == first.members
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())
    with open_mod_package(first.archive) as packaged:
        assert packaged.schema_version == 2
        assert packaged.editions == ("enhanced",)
        assert packaged.dlc_packs == ("lunga",)
        assert packaged.files[0].sha256 == plan.source_member_sha256
    assert first.launcher_contract == result.launcher_contract


def test_conversion_refuses_ambiguous_or_unregistered_edition_branches(
    tmp_path: Path,
):
    source = _source_archive(tmp_path)
    ambiguous = _converter(
        tmp_path, source, _scan(source, enhanced_members=2),
    )
    with pytest.raises(ValueError, match="exactly one Enhanced"):
        ambiguous.plan(source, edition="enhanced")

    unregistered = _converter(
        tmp_path, source, _scan(source, registration=False),
    )
    with pytest.raises(ValueError, match="does not register"):
        unregistered.plan(source, edition="enhanced")


def test_traffic_distribution_is_explicit_capability_and_defaults_off(tmp_path: Path):
    source = _source_archive(tmp_path)
    converter = _converter(tmp_path, source, _scan(source))
    catalog = VehicleCatalog.from_dict({
        "schema_version": 1,
        "id": "example.lunga",
        "name": "Lunga",
        "vehicles": [{
            "model": "lunga", "name": "Lunga", "manufacturer": "Pagani",
            "category": "super", "price": 2_000_000, "storage": "garage",
            "source_pack": "lunga", "size_tier": 1,
            "preview_dictionary": "lunga_previews",
            "preview_texture": "lunga",
            "traffic": {"enabled": True, "weight": 0.5},
        }],
    })
    plan = converter.plan(
        source, edition="enhanced", package_id="example.lunga", catalog=catalog,
    )
    result = converter.export(plan, tmp_path / "traffic-package")
    descriptor = json.loads(result.content_path.read_text(encoding="utf-8"))
    assert descriptor["capabilities"] == [
        "gbay.catalogs", "launcher.settings", "traffic.catalog",
    ]
    setting = descriptor["systems"][0]["settings"][0]
    assert setting["key"] == "traffic_enabled"
    assert setting["default"] is False
    manifest = ModManifest.load(result.manifest_path)
    assert str(manifest.package_requirements[0]) == "allin1.online-content>=0.5.5"


@pytest.mark.parametrize(("vehicle_type", "category", "storage"), (
    ("VEHICLE_TYPE_BOAT", "boats", "harbour"),
    ("VEHICLE_TYPE_HELI", "helicopters", "helipad"),
    ("VEHICLE_TYPE_PLANE", "planes", "hangar"),
))
def test_physical_vehicle_type_overrides_military_class_for_storage(
    tmp_path: Path, vehicle_type: str, category: str, storage: str,
):
    source = _source_archive(tmp_path)
    base = _scan(source)
    scan = replace(base, vehicles=tuple(
        replace(item, vehicle_class="VC_MILITARY", vehicle_type=vehicle_type)
        if item.edition == "enhanced" else item
        for item in base.vehicles
    ))

    plan = _converter(tmp_path, source, scan).plan(source, edition="enhanced")

    listing = plan.catalog.vehicles[0]
    assert listing.category == category
    assert listing.storage == storage


@pytest.mark.parametrize("requirement", (
    "allin1.online-content",
    "allin1.online-content>=0.5.3",
    "allin1.online-content==0.5.5",
))
def test_vehicle_catalog_requires_explicit_online_content_minimum(
    tmp_path: Path, requirement: str,
):
    source = _source_archive(tmp_path)
    converter = _converter(tmp_path, source, _scan(source))
    plan = converter.plan(source, edition="enhanced")
    result = converter.export(plan, tmp_path / "managed")
    manifest_text = result.manifest_path.read_text(encoding="utf-8").replace(
        'requires = ["allin1.online-content>=0.5.5"]',
        f"requires = [{json.dumps(requirement)}]",
    )
    result.manifest_path.write_text(manifest_text, encoding="utf-8")
    with pytest.raises(ValueError, match="allin1.online-content>=0.5.5"):
        ModManifest.load(result.manifest_path)


@pytest.mark.parametrize("model", ("cog552", "xll6c000"))
def test_standalone_package_preflight_reserves_official_model_names_and_hashes(
    tmp_path: Path, model: str,
):
    source = _source_archive(tmp_path)
    converter = _converter(tmp_path, source, _scan(source))
    result = converter.export(
        converter.plan(source, edition="enhanced"),
        tmp_path / f"managed-{model}",
    )
    catalog = json.loads(result.catalog_path.read_text(encoding="utf-8"))
    catalog["vehicles"][0]["model"] = model
    result.catalog_path.write_text(
        json.dumps(catalog, indent=2) + "\n", encoding="utf-8",
    )
    old_sha256 = next(
        item.sha256 for item in ModManifest.load(
            result.manifest_path, reserved_models=(), validate_payload=False,
        ).files
        if item.destination.as_posix().endswith("/vehicles.json")
    )
    new_sha256 = hashlib.sha256(result.catalog_path.read_bytes()).hexdigest()
    result.manifest_path.write_text(
        result.manifest_path.read_text(encoding="utf-8").replace(
            old_sha256, new_sha256,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="official GTA model"):
        ModManifest.load(result.manifest_path)
    assert ModManifest.load(result.manifest_path, reserved_models=()).mod_id == (
        "imported.lunga.enhanced"
    )


def test_conversion_accepts_one_unlabelled_branch_resolved_by_native_inspection(
    tmp_path: Path,
):
    source = tmp_path / "rx7-fixture.zip"
    member = "install/addon/veilsiderx7/dlc.rpf"
    with zipfile.ZipFile(source, "w") as package:
        package.writestr(member, ENHANCED_RPF)
    scan = PackageScan(
        source=source,
        source_kind="zip",
        entries=(PackageEntry(member, len(ENHANCED_RPF)),),
        findings=(),
        weapons=(),
        ammo=(),
        animation_weapons=(),
        shop_weapons=(),
        vehicles=(
            VehicleRecord(
                source=f"{member}!common/data/levels/gta5/vehicles.meta",
                model_name="vsrx7",
                txd_name="vsrx7",
                handling_id="VSRX7",
                game_name="VSRX7",
                make_name="MAZDA",
                audio_name_hash="TYRUS",
                layout="LAYOUT_LOW",
                vehicle_type="VEHICLE_TYPE_CAR",
                vehicle_class="VC_SPORT",
                edition="enhanced",
            ),
        ),
        registrations=(
            PackageRegistrationRecord(
                f"{member}!content.xml",
                "single-player-content", ("dlc_veilsiderx7",),
                ("vehicles.meta",),
            ),
        ),
        rpf_archives=(
            RpfPackageRecord(member, "enhanced", 14, 45, {".yft": 2, ".ytd": 1}),
        ),
    )
    converter = _converter(tmp_path, source, scan)

    plan = converter.plan(source, edition="enhanced")

    assert plan.source_member == member
    assert plan.dlc_pack == "veilsiderx7"
    assert plan.vehicles == ("vsrx7",)
    assert plan.destination == "mods/update/x64/dlcpacks/veilsiderx7/dlc.rpf"


def test_conversion_refuses_unlabelled_branch_without_matching_native_evidence(
    tmp_path: Path,
):
    source = tmp_path / "unresolved.zip"
    member = "install/addon/vehicle/dlc.rpf"
    with zipfile.ZipFile(source, "w") as package:
        package.writestr(member, ENHANCED_RPF)
    scan = PackageScan(
        source=source, source_kind="zip",
        entries=(PackageEntry(member, len(ENHANCED_RPF)),),
        findings=(), weapons=(), ammo=(), animation_weapons=(),
        shop_weapons=(), rpf_archives=(),
    )
    converter = _converter(tmp_path, source, scan)

    with pytest.raises(ValueError, match="exactly one Enhanced"):
        converter.plan(source, edition="enhanced")


def test_conversion_refuses_stale_sources_overwrites_and_game_destinations(
    tmp_path: Path,
):
    source = _source_archive(tmp_path)
    converter = _converter(tmp_path, source, _scan(source))
    plan = converter.plan(source, edition="legacy")

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        converter.export(plan, occupied)

    with pytest.raises(ValueError, match="GTA V installation"):
        converter.export(plan, converter.gta_path / "review")

    with zipfile.ZipFile(source, "w") as package:
        package.writestr("Legacy/lunga/dlc.rpf", b"changed-rpf-fixture")
        package.writestr("Enhanced/lunga/dlc.rpf", ENHANCED_RPF)
    with pytest.raises(ValueError, match="changed after"):
        converter.export(plan, tmp_path / "stale")
    assert not (tmp_path / "stale").exists()


def test_publication_refuses_bad_destinations_and_payload_drift(tmp_path: Path):
    source = _source_archive(tmp_path)
    converter = _converter(tmp_path, source, _scan(source))
    plan = converter.plan(source, edition="enhanced")
    result = converter.export(plan, tmp_path / "managed")

    with pytest.raises(ValueError, match=".zip filename"):
        converter.publish(result.package_root, tmp_path / "release.bin")
    with pytest.raises(ValueError, match="inside GTA V"):
        converter.publish(result.package_root, converter.gta_path / "release.zip")

    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"occupied")
    with pytest.raises(ValueError, match="already exists"):
        converter.publish(result.package_root, existing)

    review_text = result.review_path.read_text(encoding="utf-8")
    evidence = json.loads(review_text)
    evidence["edition"] = "legacy"
    result.review_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        converter.publish(result.package_root, tmp_path / "bad-review.zip")
    assert not (tmp_path / "bad-review.zip").exists()
    result.review_path.write_text(review_text, encoding="utf-8")

    result.payload_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        converter.publish(result.package_root, tmp_path / "tampered.zip")
    assert not (tmp_path / "tampered.zip").exists()

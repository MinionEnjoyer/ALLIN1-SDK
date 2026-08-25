from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from allin1_sdk.addon_importer import (
    PackageEntry,
    PackageRegistrationRecord,
    PackageScan,
    RpfNativeEntryRecord,
    RpfPackageRecord,
    VehicleRecord,
)
from allin1_sdk.vehicle_quick_import import (
    VehicleQuickImportService,
    launcher_package_library_root,
    parse_listing_assignments,
)


LEGACY_RPF = b"quick-import-legacy"
ENHANCED_RPF = b"quick-import-enhanced"


def _source(root: Path) -> Path:
    source = root / "pagani-huayra-codalunga.zip"
    with zipfile.ZipFile(source, "w") as package:
        package.writestr("Legacy/lunga/dlc.rpf", LEGACY_RPF)
        package.writestr("Enhanced/lunga/dlc.rpf", ENHANCED_RPF)
    return source


def _vehicle(edition: str, member: str) -> VehicleRecord:
    return VehicleRecord(
        source=f"{member}!data/vehicles.meta",
        model_name="lunga",
        txd_name="lunga",
        handling_id="lunga",
        game_name="LUNGA",
        make_name="null",
        audio_name_hash="T20",
        layout="LAYOUT_STANDARD",
        vehicle_type="VEHICLE_TYPE_CAR",
        vehicle_class="VC_SUPER",
        edition=edition,
    )


def _scan(source: Path) -> PackageScan:
    legacy = "Legacy/lunga/dlc.rpf"
    enhanced = "Enhanced/lunga/dlc.rpf"
    return PackageScan(
        source=source,
        source_kind="zip",
        entries=(
            PackageEntry(legacy, len(LEGACY_RPF)),
            PackageEntry(enhanced, len(ENHANCED_RPF)),
        ),
        findings=(),
        weapons=(),
        ammo=(),
        animation_weapons=(),
        shop_weapons=(),
        vehicles=(
            _vehicle("legacy", legacy),
            _vehicle("enhanced", enhanced),
        ),
        registrations=(
            PackageRegistrationRecord(
                f"{legacy}!content.xml", "single-player-content",
                ("dlc_lunga",), ("vehicles.meta",),
            ),
            PackageRegistrationRecord(
                f"{enhanced}!content.xml", "single-player-content",
                ("dlc_lunga",), ("vehicles.meta",),
            ),
        ),
        rpf_archives=(
            RpfPackageRecord(legacy, "legacy", 2, 10, {".yft": 1}),
            RpfPackageRecord(enhanced, "enhanced", 2, 10, {".yft": 1}),
        ),
        rpf_native_assets=(
            RpfNativeEntryRecord(
                enhanced, "", "x64/vehicles/lunga.ytd", "ytd-1",
                "texture_dictionary", ".ytd", 512,
            ),
            RpfNativeEntryRecord(
                enhanced, "", "x64/vehicles/shared_vehicle.ytd", "ytd-2",
                "texture_dictionary", ".ytd", 256,
            ),
        ),
    )


class _Inspector:
    def __init__(self, scan: PackageScan) -> None:
        self.scan = scan

    def inspect(self, source: Path) -> PackageScan:
        assert source.resolve() == self.scan.source.resolve()
        return self.scan


def _service(tmp_path: Path):
    source = _source(tmp_path)
    project = tmp_path / "sdk"
    game = tmp_path / "game"
    project.mkdir()
    game.mkdir()
    return source, VehicleQuickImportService(
        project, game, inspector=_Inspector(_scan(source)),
    )


def test_quick_import_discovers_explicit_editions_and_prefers_active_target(
    tmp_path: Path,
):
    source, service = _service(tmp_path)

    inspection = service.inspect(source, preferred_edition="legacy")

    assert inspection.available_editions == ("legacy", "enhanced")
    assert inspection.suggested_edition == "legacy"
    assert inspection.to_dict()["vehicles"] == [
        {
            "model": "lunga",
            "edition": "legacy",
            "display_name": "LUNGA",
            "manufacturer": "null",
            "vehicle_class": "VC_SUPER",
        },
        {
            "model": "lunga",
            "edition": "enhanced",
            "display_name": "LUNGA",
            "manufacturer": "null",
            "vehicle_class": "VC_SUPER",
        },
    ]
    with pytest.raises(ValueError, match="was not detected"):
        service.plan(inspection, edition="unknown")


def test_quick_import_edits_storefront_only_and_keeps_traffic_off(
    tmp_path: Path,
):
    source, service = _service(tmp_path)
    inspection = service.inspect(source)
    initial = service.plan(
        inspection,
        edition="enhanced",
        package_id="fixture.pagani",
        name="Pagani Vehicle Pack",
    )
    assert initial.plan.catalog.vehicles[0].display_name == "Lunga"
    assert initial.plan.catalog.vehicles[0].manufacturer == "Pagani"
    assert any("inferred display name" in item for item in initial.warnings)
    assert all("manufacturer" not in item for item in initial.warnings)
    assert any("GBAY price" in item for item in initial.warnings)

    reviewed = service.customize(initial.plan, {
        "lunga": {
            "name": "Huayra Codalunga",
            "manufacturer": "Pagani",
            "category": "super",
            "price": 2_350_000,
            "size_tier": 1,
            "traffic_enabled": False,
        },
    })
    listing = reviewed.plan.catalog.vehicles[0]
    assert listing.model == "lunga"
    assert listing.source_pack == "lunga"
    assert listing.display_name == "Huayra Codalunga"
    assert listing.manufacturer == "Pagani"
    assert listing.price == 2_350_000
    assert listing.storage == "garage"
    assert listing.traffic.enabled is False
    assert all("technical display" not in item for item in reviewed.warnings)
    assert source.read_bytes().startswith(b"PK")


def test_quick_import_derives_specialized_storage_and_validates_traffic(
    tmp_path: Path,
):
    _source_path, service = _service(tmp_path)
    inspection = service.inspect(inspection_source := service.inspector.scan.source)
    initial = service.plan(inspection, edition="enhanced")

    boat = service.customize(initial.plan, {
        "lunga": {"category": "boats", "traffic_enabled": False},
    })
    assert boat.plan.catalog.vehicles[0].storage == "harbour"
    with pytest.raises(ValueError, match="cannot opt into ambient traffic"):
        service.customize(initial.plan, {
            "lunga": {"category": "boats", "traffic_enabled": True},
        })
    assert inspection_source.is_file()


def test_quick_import_prepares_launcher_library_package_without_game_write(
    tmp_path: Path,
):
    source, service = _service(tmp_path)
    inspection = service.inspect(source)
    review = service.plan(
        inspection, edition="enhanced", package_id="fixture.pagani",
    )
    review = service.customize(review.plan, {
        "lunga": {"free_price_confirmed": True},
    })
    library = tmp_path / "launcher-library"
    destination = service.library_destination(review.plan, library_root=library)

    prepared = service.prepare(
        review, destination, library_root=library,
    )

    assert prepared.launcher_library is True
    assert prepared.result.manifest_path.is_file()
    assert prepared.result.payload_path.read_bytes() == ENHANCED_RPF
    assert prepared.result.launcher_contract["traffic_opt_in"] is False
    assert prepared.to_dict()["launcher_install_required"] is True
    assert prepared.to_dict()["game_write_performed"] is False
    assert prepared.to_dict()["traffic_requested"] is False
    assert prepared.result.launcher_contract["payload_sha256"] == hashlib.sha256(
        ENHANCED_RPF,
    ).hexdigest()
    replaced = service.prepare(review, destination, library_root=library)
    assert replaced.replaced_existing is True
    assert replaced.to_dict()["replaced_existing"] is True
    assert replaced.result.package_root == destination


def test_quick_import_never_replaces_an_unowned_existing_folder(tmp_path: Path):
    source, service = _service(tmp_path)
    inspection = service.inspect(source)
    review = service.plan(inspection, edition="enhanced")
    review = service.customize(review.plan, {
        "lunga": {"free_price_confirmed": True},
    })
    destination = tmp_path / "launcher-library" / review.plan.package_id
    destination.mkdir(parents=True)
    marker = destination / "user-file.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="not an intact SDK-managed package"):
        service.prepare(review, destination)

    assert marker.read_text(encoding="utf-8") == "keep"


def test_quick_import_requires_new_review_when_source_changes(tmp_path: Path):
    source, service = _service(tmp_path)
    inspection = service.inspect(source)
    review = service.plan(
        inspection, edition="enhanced", package_id="fixture.pagani",
    )
    review = service.customize(review.plan, {
        "lunga": {"free_price_confirmed": True},
    })
    destination = tmp_path / "launcher-library" / review.plan.package_id
    with zipfile.ZipFile(source, "a") as package:
        package.writestr("changed-after-review.txt", "new evidence")

    with pytest.raises(ValueError, match="changed after review"):
        service.prepare(review, destination)

    assert not destination.exists()


def test_shared_launcher_library_path_and_assignment_parser(tmp_path: Path):
    root = launcher_package_library_root(
        {"LOCALAPPDATA": str(tmp_path / "local")}, home=tmp_path / "home",
    )
    assert root == (tmp_path / "local" / "ALLIN1" / "Packages").resolve()
    assert parse_listing_assignments((
        "lunga.name=Huayra Codalunga",
        "lunga.price=2350000",
        "lunga.traffic_enabled=false",
        "lunga.traffic_weight=0.5",
        "lunga.free_price_confirmed=true",
    )) == {
        "lunga": {
            "name": "Huayra Codalunga",
            "price": 2_350_000,
            "traffic_enabled": False,
            "traffic_weight": 0.5,
            "free_price_confirmed": True,
        },
    }
    with pytest.raises(ValueError, match="MODEL.FIELD=VALUE"):
        parse_listing_assignments(("bad",))
    with pytest.raises(ValueError, match="Duplicate"):
        parse_listing_assignments(("lunga.price=1", "lunga.price=2"))


def test_quick_import_requires_explicit_free_acknowledgement(tmp_path: Path):
    source, service = _service(tmp_path)
    inspection = service.inspect(source)
    review = service.plan(inspection, edition="enhanced")

    with pytest.raises(ValueError, match="explicit confirmation"):
        service.prepare(review, tmp_path / "unconfirmed-free")

    confirmed = service.customize(review.plan, {
        "lunga": {"price": 0, "free_price_confirmed": True},
    })
    assert confirmed.acknowledged_free_models == ("lunga",)
    assert all("free listing" not in item for item in confirmed.warnings)


def test_traffic_choice_is_handoff_intent_with_safe_launcher_default(
    tmp_path: Path,
):
    source, service = _service(tmp_path)
    inspection = service.inspect(source)
    initial = service.plan(
        inspection, edition="enhanced", package_id="fixture.traffic",
    )
    review = service.customize(initial.plan, {
        "lunga": {"price": 1, "traffic_enabled": True},
    })

    prepared = service.prepare(review, tmp_path / "traffic-package")
    descriptor = json.loads(
        prepared.result.content_path.read_text(encoding="utf-8")
    )

    assert prepared.to_dict()["traffic_requested"] is True
    assert prepared.result.plan.catalog.vehicles[0].traffic.enabled is True
    assert descriptor["systems"][0]["settings"][0]["default"] is False


def test_preview_candidates_are_package_owned_ytd_names_not_texture_guesses(
    tmp_path: Path,
):
    source, service = _service(tmp_path)
    inspection = service.inspect(source)

    candidates = service.preview_dictionary_candidates(
        inspection, edition="enhanced", model="lunga",
    )

    assert candidates == ("lunga", "shared_vehicle")
    assert all("texture" not in item for item in candidates)

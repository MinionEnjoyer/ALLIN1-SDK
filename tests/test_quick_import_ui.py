from __future__ import annotations

import threading
import time
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from tkinter import ttk

import pytest

from allin1_sdk.app import _configure_style
from allin1_sdk import quick_import_ui
from allin1_sdk.quick_import_ui import QuickImportFrame
from allin1_sdk.vehicle_catalog import VehicleCatalog
from allin1_sdk.vehicle_quick_import import (
    PreparedVehicleQuickImport,
    VehicleQuickImportReview,
)


@dataclass(frozen=True)
class _Plan:
    package_id: str
    name: str
    version: str
    dlc_pack: str
    catalog: VehicleCatalog

    @property
    def traffic_opt_in(self) -> bool:
        return any(item.traffic.enabled for item in self.catalog.vehicles)


class _Service:
    def __init__(self, root: Path, *, inspection_gate: threading.Event | None = None):
        self.root = root
        self.gta_path = root / "game"
        self.inspection_gate = inspection_gate
        self.prepared_review: VehicleQuickImportReview | None = None
        self.prepare_calls = 0

    def inspect(self, source: Path, *, preferred_edition: str | None = None):
        if self.inspection_gate is not None:
            self.inspection_gate.wait(timeout=3)
        return SimpleNamespace(
            source=Path(source).resolve(),
            available_editions=("legacy", "enhanced"),
            suggested_edition=(
                preferred_edition
                if preferred_edition in {"legacy", "enhanced"} else "enhanced"
            ),
            scan=SimpleNamespace(error_count=0, warning_count=1),
        )

    def plan(
        self, inspection, *, edition: str, package_id: str | None = None,
        name: str | None = None, version: str = "1.0.0",
    ) -> VehicleQuickImportReview:
        catalog_id = package_id or f"fixture.import.{edition}"
        catalog_name = name or f"Fixture Import ({edition.title()})"
        catalog = VehicleCatalog.from_dict({
            "schema_version": 1,
            "id": catalog_id,
            "name": catalog_name,
            "vehicles": [
                {
                    "model": "fixturecar",
                    "name": "FIXTURECAR",
                    "manufacturer": "",
                    "category": "sports",
                    "price": 0,
                    "storage": "garage",
                    "source_pack": "fixture",
                    "traffic": {"enabled": False, "weight": 1.0},
                },
                {
                    "model": "fixturecar2",
                    "name": "Fixture Two",
                    "manufacturer": "Example",
                    "category": "super",
                    "price": 250000,
                    "storage": "garage",
                    "source_pack": "fixture",
                    "traffic": {"enabled": False, "weight": 1.0},
                },
            ],
        })
        return VehicleQuickImportReview(
            _Plan(catalog_id, catalog_name, version, "fixture", catalog),
            ("fixturecar: replace its technical display label.",),
        )

    def customize(self, plan: _Plan, updates) -> VehicleQuickImportReview:
        records = []
        acknowledged_free: list[str] = []
        for entry in plan.catalog.vehicles:
            values = entry.to_dict()
            changes = dict(updates.get(entry.model, {}))
            free_confirmed = changes.pop("free_price_confirmed", False)
            traffic = dict(values["traffic"])
            traffic["enabled"] = changes.pop("traffic_enabled", traffic["enabled"])
            traffic["weight"] = changes.pop("traffic_weight", traffic["weight"])
            for key, value in changes.items():
                if key in {"preview_dictionary", "preview_texture"} and not value:
                    values.pop(key, None)
                else:
                    values[key] = value
            values["traffic"] = traffic
            if values.get("price") == 0 and free_confirmed:
                acknowledged_free.append(entry.model)
            records.append(values)
        catalog = VehicleCatalog.from_dict({
            "schema_version": 1,
            "id": plan.package_id,
            "name": plan.name,
            "vehicles": records,
        })
        return VehicleQuickImportReview(
            replace(plan, catalog=catalog), (), tuple(acknowledged_free),
        )

    def library_destination(self, plan: _Plan, *, library_root=None) -> Path:
        root = Path(library_root) if library_root is not None else self.root / "library"
        return root.resolve() / plan.package_id

    def prepare(
        self, review: VehicleQuickImportReview, destination: Path, *,
        publish_zip=None, library_root=None,
    ) -> PreparedVehicleQuickImport:
        self.prepare_calls += 1
        self.prepared_review = review
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        manifest = destination / "mod.toml"
        manifest.write_text("schema_version = 2\n", encoding="utf-8")
        result = SimpleNamespace(
            plan=review.plan,
            manifest_path=manifest,
            package_root=destination,
        )
        return PreparedVehicleQuickImport(
            result=result, published=None, warnings=review.warnings,
            launcher_library=True,
        )


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    _configure_style(root)
    root.geometry("1120x760+10+10")
    root.update()
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()


def _wait(root: tk.Tk, predicate, *, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update_idletasks()
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for Quick Import background work")


def _loaded_frame(
    tmp_path: Path, tk_root: tk.Tk, *, callback=None, help_callback=None,
    launcher_callback=None,
) -> tuple[QuickImportFrame, _Service]:
    source = tmp_path / "fixture.zip"
    source.write_bytes(b"PK fixture")
    service = _Service(tmp_path)
    frame = QuickImportFrame(
        tk_root, tmp_path, service=service,
        library_root=tmp_path / "launcher-library",
        on_open_workbench=callback, on_help=help_callback,
        on_open_launcher=launcher_callback,
    )
    frame.pack(fill="both", expand=True)
    assert frame.open_source(source, preferred_edition="enhanced")
    _wait(tk_root, lambda: frame.review is not None and not frame.has_active_work())
    return frame, service


def test_quick_import_is_one_embedded_three_tab_workspace(tmp_path, tk_root):
    routed: list[str] = []
    helped: list[str] = []
    frame, _service = _loaded_frame(
        tmp_path, tk_root,
        callback=routed.append,
        help_callback=helped.append,
    )

    assert isinstance(frame, ttk.Frame)
    assert [
        frame.tabs.tab(page, "text")
        for page in (frame.vehicle_page, frame.weapon_page, frame.ped_page)
    ] == ["Vehicles", "Weapons", "Peds"]
    assert frame.model_tree.get_children() == ("fixturecar", "fixturecar2")
    assert frame.edition.get() == "enhanced"
    assert frame.traffic_enabled.get() is False
    assert str(frame.free_price_check.cget("state")) == "normal"
    assert frame.size_tier.get() == quick_import_ui.SIZE_TIER_LABELS[0]
    assert frame.custom_preview.get() is False
    assert str(frame.preview_texture_entry.cget("state")) == "disabled"
    assert frame.editor_canvas.bind("<MouseWheel>")
    assert not any(isinstance(child, tk.Toplevel) for child in frame.winfo_children())

    frame.weapon_workbench_button.invoke()
    frame.ped_workbench_button.invoke()
    frame.advanced_vehicle_button.invoke()
    frame.help_button.invoke()
    assert routed == ["weapons", "peds", "vehicles"]
    assert helped == ["quick-import"]
    frame.destroy()


def test_friendly_size_labels_preserve_numeric_placement_contract():
    assert quick_import_ui.SIZE_TIER_LABELS == {
        0: "Standard — regular garage spaces",
        1: "Large — left-row garage spaces",
        2: "Oversize — Harmony floor garage only",
    }
    assert quick_import_ui.SIZE_TIER_VALUES[
        "Oversize — Harmony floor garage only"
    ] == 2


def test_oiv_export_is_available_without_launcher_and_requires_author(
    tmp_path, tk_root, monkeypatch,
):
    frame, _service = _loaded_frame(tmp_path, tk_root)
    destination = tmp_path / "fixture.oiv"
    exported: dict[str, object] = {}

    class _Exporter:
        def __init__(self, gta_path):
            exported["gta_path"] = gta_path

        def export_plan(self, plan, output, *, author):
            exported.update({"plan": plan, "output": output, "author": author})
            output.write_bytes(b"oiv")
            return SimpleNamespace(archive=output)

    monkeypatch.setattr(quick_import_ui, "LegacyVehicleOivExporter", _Exporter)
    monkeypatch.setattr(
        quick_import_ui.simpledialog, "askstring", lambda *_args, **_kwargs: "Author",
    )
    monkeypatch.setattr(
        quick_import_ui.filedialog, "asksaveasfilename",
        lambda *_args, **_kwargs: str(destination),
    )

    assert str(frame.export_oiv_button.cget("state")) == "normal"
    frame.export_oiv_button.invoke()
    _wait(tk_root, lambda: destination.is_file() and not frame.has_active_work())

    assert exported["author"] == "Author"
    assert "Legacy OIV package" in frame.status.get()
    assert "not included" in frame.status.get()
    frame.destroy()


def test_vehicle_quick_import_edits_typed_listing_and_prepares_library_package(
    tmp_path, tk_root,
):
    launcher_calls: list[tuple[str, bool]] = []
    frame, service = _loaded_frame(
        tmp_path, tk_root, launcher_callback=lambda package_id, traffic: (
            launcher_calls.append((package_id, traffic))
        ),
    )

    frame.listing_name.set("Example Sport")
    frame.manufacturer.set("Example Motors")
    frame.price.set("185000")
    frame.category.set("sports")
    frame.size_tier.set(quick_import_ui.SIZE_TIER_LABELS[1])
    frame.traffic_enabled.set(True)
    frame._traffic_changed()
    assert str(frame.listing_entries[-1].cget("state")) == "normal"
    assert not frame.confirm_navigation()

    frame.prepare_button.invoke()
    _wait(tk_root, lambda: frame.prepared is not None and not frame.has_active_work())

    assert service.prepare_calls == 1
    assert service.prepared_review is not None
    listing = service.prepared_review.plan.catalog.vehicles[0]
    assert listing.display_name == "Example Sport"
    assert listing.manufacturer == "Example Motors"
    assert listing.price == 185000
    assert listing.size_tier == 1
    assert listing.traffic.enabled is True
    assert listing.traffic.weight == 1.0
    assert frame.prepared.result.manifest_path == (
        tmp_path / "launcher-library" / "fixture.import.enhanced" / "mod.toml"
    )
    assert frame.confirm_navigation()
    assert "GTA V was not changed" in frame.status.get()
    assert frame.open_launcher_button.winfo_manager()
    frame.open_launcher_button.invoke()
    assert launcher_calls == [("fixture.import.enhanced", True)]

    frame.listing_name.set("Example Sport Revised")
    assert not frame.open_launcher_button.winfo_manager()
    frame.prepare_button.invoke()
    _wait(tk_root, lambda: service.prepare_calls == 2 and not frame.has_active_work())
    assert service.prepared_review.plan.catalog.vehicles[0].display_name == (
        "Example Sport Revised"
    )
    frame.destroy()


def test_invalid_form_and_busy_navigation_fail_inline_without_popups(
    tmp_path, tk_root,
):
    frame, service = _loaded_frame(tmp_path, tk_root)
    frame.price.set("free")
    frame.prepare_button.invoke()

    assert service.prepare_calls == 0
    assert frame.validation.get() == "Price must be a whole number."
    assert not frame.confirm_navigation()
    frame.discard_button.invoke()
    assert frame.price.get() == "0"
    assert frame.confirm_navigation()
    frame.destroy()

    source = tmp_path / "slow.zip"
    source.write_bytes(b"PK slow")
    gate = threading.Event()
    slow = QuickImportFrame(
        tk_root, tmp_path, service=_Service(tmp_path, inspection_gate=gate),
    )
    slow.pack(fill="both", expand=True)
    assert slow.open_source(source)
    assert slow.has_active_work()
    assert not slow.confirm_navigation()
    assert "Wait for" in slow.validation.get()
    gate.set()
    _wait(tk_root, lambda: slow.review is not None and not slow.has_active_work())
    slow.destroy()


def test_free_price_and_custom_preview_require_explicit_valid_choices(
    tmp_path, tk_root,
):
    frame, service = _loaded_frame(tmp_path, tk_root)

    frame.prepare_button.invoke()
    assert service.prepare_calls == 0
    assert "intentionally free" in frame.validation.get()

    frame.free_price_confirmed.set(True)
    frame.custom_preview.set(True)
    frame._preview_mode_changed()
    frame.preview_dictionary.set("fixture_previews")
    frame.prepare_button.invoke()
    assert service.prepare_calls == 0
    assert "exact texture name" in frame.validation.get()

    frame.custom_preview.set(False)
    frame._preview_mode_changed()
    frame.prepare_button.invoke()
    _wait(tk_root, lambda: frame.prepared is not None and not frame.has_active_work())
    listing = service.prepared_review.plan.catalog.vehicles[0]
    assert listing.price == 0
    assert listing.preview_dictionary is None
    assert listing.preview_texture is None
    frame.destroy()


def test_preparing_one_edition_does_not_hide_another_editions_dirty_draft(
    tmp_path, tk_root,
):
    frame, service = _loaded_frame(tmp_path, tk_root)
    frame.listing_name.set("Enhanced draft")
    frame.free_price_confirmed.set(True)
    assert "enhanced" in frame._dirty_editions

    frame.edition.set("legacy")
    frame._edition_changed()
    _wait(
        tk_root,
        lambda: frame._active_edition == "legacy" and not frame.has_active_work(),
    )
    frame.listing_name.set("Legacy draft")
    frame.free_price_confirmed.set(True)
    assert frame._dirty_editions == {"legacy", "enhanced"}

    frame.prepare_button.invoke()
    _wait(tk_root, lambda: service.prepare_calls == 1 and not frame.has_active_work())
    assert frame._dirty_editions == {"enhanced"}
    assert not frame.confirm_navigation()

    frame.edition.set("enhanced")
    frame._edition_changed()
    _wait(
        tk_root,
        lambda: frame._active_edition == "enhanced" and not frame.has_active_work(),
    )
    frame.discard_button.invoke()
    assert frame._dirty_editions == set()
    assert frame.confirm_navigation()
    frame.destroy()


def test_edition_services_use_the_matching_configured_game_root(
    tmp_path, tk_root, monkeypatch,
):
    legacy = tmp_path / "Grand Theft Auto V"
    enhanced = tmp_path / "Grand Theft Auto V Enhanced"
    legacy.mkdir()
    enhanced.mkdir()
    (legacy / "GTA5.exe").write_bytes(b"")
    (enhanced / "GTA5_Enhanced.exe").write_bytes(b"")
    selected_roots: list[Path] = []

    def service_factory(project_root: Path, gta_path: Path):
        selected_roots.append(Path(gta_path))
        return _Service(tmp_path)

    monkeypatch.setattr(
        quick_import_ui, "VehicleQuickImportService", service_factory,
    )
    frame = QuickImportFrame(
        tk_root, tmp_path, installation_roots=(legacy, enhanced),
    )
    frame.pack(fill="both", expand=True)

    assert frame._preferred_configured_edition() == "enhanced"
    frame._service("legacy")
    frame._service("enhanced")
    assert selected_roots == [legacy.resolve(), enhanced.resolve()]
    frame.destroy()

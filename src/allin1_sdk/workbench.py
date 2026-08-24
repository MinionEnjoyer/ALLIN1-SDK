"""Unified content workbench for the SDK's primary add-on families."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from allin1_sdk.addon_importer import AddonPackageInspector, PackageScan
from allin1_sdk.ped_workbench import PedWorkbenchFrame
from allin1_sdk.vehicle_workbench import VehicleWorkbenchFrame
from allin1_sdk.weapon_workbench import WeaponWorkbenchFrame


class WorkbenchFrame(ttk.Frame):
    """Keep vehicle, weapon, and ped projects in one shared package context."""

    CATEGORIES = ("vehicles", "weapons", "peds")

    def __init__(
        self,
        parent: tk.Misc,
        project_root: str | Path,
        *,
        installation_roots: tuple[Path, ...] = (),
        on_help=None,
        on_close=None,
        on_open_asset=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.installation_roots = installation_roots
        self._on_help = on_help
        self._on_close = on_close
        self._on_open_asset = on_open_asset
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.status = tk.StringVar(
            value="Open a package once, then inspect its vehicles, weapons, and peds."
        )
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=(8, 5, 8, 7))
        outer.pack(fill="both", expand=True)

        command_row = ttk.Frame(outer)
        command_row.pack(fill="x", pady=(0, 4))
        ttk.Label(
            command_row, text="Content Workbench", style="DialogTitle.TLabel",
        ).pack(side="left")
        open_menu = tk.Menu(command_row, tearoff=False)
        open_menu.add_command(label="Open package folder…", command=self._choose_folder)
        open_menu.add_command(label="Open package archive…", command=self._choose_archive)
        ttk.Button(
            command_row, text="Workbench help",
            command=lambda: self._on_help("workbench") if self._on_help else None,
        ).pack(side="right")
        self.reload_button = ttk.Button(
            command_row, text="Reload", state="disabled", command=self.reload,
        )
        self.reload_button.pack(side="right", padx=(0, 7))
        ttk.Menubutton(
            command_row, text="Open package", style="Accent.TButton", menu=open_menu,
        ).pack(side="right", padx=(0, 7))
        self.status_label = ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            anchor="w", justify="left",
        )
        self.status_label.pack(fill="x", pady=(0, 4))

        # An unconstrained notebook requests the widest preferred size from
        # every page, including the two hidden specialist workbenches. That
        # can make the complete workspace wider than the application after a
        # resize. A nominal pane request lets pack allocate the available
        # client area while each active workbench still expands to fill it.
        self.tabs = ttk.Notebook(outer, width=1, height=1)
        self.tabs.pack(fill="both", expand=True)
        vehicle_page = ttk.Frame(self.tabs)
        weapon_page = ttk.Frame(self.tabs)
        ped_page = ttk.Frame(self.tabs)
        self.pages = {
            "vehicles": vehicle_page,
            "weapons": weapon_page,
            "peds": ped_page,
        }
        self.tabs.add(vehicle_page, text="Vehicles")
        self.tabs.add(weapon_page, text="Weapons")
        self.tabs.add(ped_page, text="Peds")

        self.vehicle_workspace = VehicleWorkbenchFrame(
            vehicle_page, self.project_root,
            installation_roots=self.installation_roots,
            on_help=self._on_help, on_open_asset=self._route_asset,
            show_context_header=False, show_open_control=False,
        )
        self.weapon_workspace = WeaponWorkbenchFrame(
            weapon_page, on_open_asset=self._route_asset, on_help=self._on_help,
        )
        self.ped_workspace = PedWorkbenchFrame(
            ped_page, self.project_root,
            installation_roots=self.installation_roots,
            on_open_asset=self._route_asset, on_help=self._on_help,
        )
        for workspace in (
            self.vehicle_workspace, self.weapon_workspace, self.ped_workspace,
        ):
            workspace.pack(fill="both", expand=True)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select a loose DLC or add-on package folder",
        )
        if selected:
            self.open_source(selected)

    def _choose_archive(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select an add-on package archive",
            filetypes=(
                ("GTA package", "*.oiv *.zip *.rar *.7z"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            self.open_source(selected)

    def open_source(
        self, source: str | Path, scan: PackageScan | None = None,
        *, category: str = "auto",
    ) -> bool:
        """Inspect a package once and route the shared result to all three tabs."""
        if self.source is not None and not self.confirm_navigation():
            return False
        try:
            resolved = Path(source).expanduser().resolve(strict=True)
            game = next(
                (path for path in self.installation_roots if path.is_dir()), None,
            )
            loaded_scan = scan or AddonPackageInspector(
                self.project_root, game,
            ).inspect(resolved)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open package", str(exc), parent=self)
            return False
        self.source = resolved
        self.scan = loaded_scan
        self.vehicle_workspace.open_source(resolved, loaded_scan)
        self.weapon_workspace.open_source(resolved, loaded_scan)
        self.ped_workspace.open_source(resolved, loaded_scan)
        counts = {
            "vehicles": len(loaded_scan.vehicles),
            "weapons": (
                len(loaded_scan.weapons)
                + len(loaded_scan.weapon_enhancements)
                + len(loaded_scan.scripted_weapon_systems)
            ),
            "peds": len(loaded_scan.peds),
        }
        for key, label in (
            ("vehicles", "Vehicles"), ("weapons", "Weapons"), ("peds", "Peds"),
        ):
            self.tabs.tab(self.pages[key], text=f"{label} ({counts[key]})")
        self.reload_button.configure(state="normal")
        self.status.set(
            f"{resolved.name} · {counts['vehicles']} vehicles · "
            f"{counts['weapons']} weapons · {counts['peds']} peds · "
            f"{loaded_scan.error_count} errors / {loaded_scan.warning_count} warnings"
        )
        selected = category
        if selected == "auto":
            selected = next((key for key in self.CATEGORIES if counts[key]), "vehicles")
        self.select_category(selected)
        return True

    def _request_close(self) -> None:
        if self.confirm_navigation() and self._on_close is not None:
            self._on_close()

    def confirm_navigation(self) -> bool:
        """Keep unapplied specialist-workbench edits from being discarded."""
        return (
            self.vehicle_workspace.confirm_navigation()
            and self.weapon_workspace.confirm_navigation()
            and self.ped_workspace.confirm_navigation()
        )

    def reload(self) -> bool:
        if self.source is None:
            return False
        current = self.current_category()
        return self.open_source(self.source, category=current)

    def current_category(self) -> str:
        selected = self.tabs.select()
        return next(
            (key for key, page in self.pages.items() if str(page) == selected),
            "vehicles",
        )

    def select_category(self, category: str) -> bool:
        page = self.pages.get(category)
        if page is None:
            return False
        self.tabs.select(page)
        return True

    def select_vehicle(self, model: str) -> bool:
        self.select_category("vehicles")
        return self.vehicle_workspace.select_model(model)

    def select_weapon(self, name: str) -> bool:
        self.select_category("weapons")
        return self.weapon_workspace.select_weapon(name)

    def select_ped(self, name: str) -> bool:
        self.select_category("peds")
        return self.ped_workspace.select_ped(name)

    def _route_asset(self, path: str) -> None:
        if self._on_open_asset is not None:
            self._on_open_asset(path)

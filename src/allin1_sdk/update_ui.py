"""Compact built-in update dialog for packaged SDK installations."""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from allin1_sdk import __version__
from allin1_sdk.self_update import (
    SDK_REPOSITORY_URL,
    SdkRelease,
    StagedUpdate,
    current_install_root,
    discard_staged_update,
    fetch_latest_release,
    schedule_staged_update,
    stage_release,
    update_available,
)
from allin1_sdk.ui_foundation import place_window


class SdkUpdateDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, close_sdk) -> None:
        super().__init__(parent)
        self.close_sdk = close_sdk
        self.release: SdkRelease | None = None
        self.staged: StagedUpdate | None = None
        self.install_root: Path | None = current_install_root()
        self.status = tk.StringVar(value="Checking the latest public release…")
        self.detail = tk.StringVar(value=f"Installed version: {__version__}")
        self.title("ALLIN1 SDK Update")
        self.transient(parent)
        self.resizable(False, False)
        place_window(self, preferred=(560, 300), minimum=(520, 280))
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.grab_set()
        self.after_idle(self.check)

    def _build(self) -> None:
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="SDK updates", style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="Download verified public SDK releases without opening the ALLIN1 Launcher.",
            wraplength=510, justify="left",
        ).pack(anchor="w", pady=(4, 14))
        panel = ttk.LabelFrame(body, text="Release status", padding=12)
        panel.pack(fill="x")
        ttk.Label(
            panel, textvariable=self.status, font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        ttk.Label(
            panel, textvariable=self.detail, foreground="#52635c",
            wraplength=490, justify="left",
        ).pack(anchor="w", pady=(5, 0))
        self.progress = ttk.Progressbar(body, maximum=100, mode="determinate")
        self.progress.pack(fill="x", pady=(14, 0))
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(14, 0))
        self.install_button = ttk.Button(
            actions, text="Download update", style="Accent.TButton",
            command=self.install, state="disabled",
        )
        self.install_button.pack(side="left")
        self.check_button = ttk.Button(actions, text="Check again", command=self.check)
        self.check_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions, text="Release page",
            command=lambda: webbrowser.open(
                self.release.page_url if self.release else SDK_REPOSITORY_URL
            ),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Close", command=self.close).pack(side="right")

    def close(self) -> None:
        if self.staged is not None and self.staged.staged_root.exists():
            try:
                discard_staged_update(self.staged)
            except OSError as exc:
                messagebox.showerror(
                    "Could not discard staged update", str(exc), parent=self,
                )
                return
        self.destroy()

    def _busy(self, value: bool) -> None:
        state = "disabled" if value else "normal"
        self.check_button.configure(state=state)
        if (
            value or self.install_root is None or self.release is None
            or not update_available(__version__, self.release.version)
        ):
            self.install_button.configure(state="disabled")
        else:
            self.install_button.configure(state="normal")

    def _run(self, operation, success) -> None:
        self._busy(True)

        def worker() -> None:
            try:
                result = operation()
            except Exception as exc:
                self.after(0, lambda error=exc: self._failed(error))
            else:
                self.after(0, lambda value=result: success(value))

        threading.Thread(target=worker, daemon=True).start()

    def _failed(self, error: Exception) -> None:
        self.status.set("Update check failed")
        self.detail.set(str(error))
        self._busy(False)

    def check(self) -> None:
        self.status.set("Checking the latest public release…")
        self.detail.set(f"Installed version: {__version__}")
        self.progress.configure(value=0)
        self._run(fetch_latest_release, self._checked)

    def _checked(self, release: SdkRelease) -> None:
        self.release = release
        if update_available(__version__, release.version):
            self.status.set(f"ALLIN1 SDK {release.version} is available")
            self.detail.set(f"Installed: {__version__}  ·  Latest: {release.version}")
        else:
            self.status.set("ALLIN1 SDK is up to date")
            self.detail.set(f"Installed: {__version__}  ·  Latest: {release.version}")
        if self.install_root is None:
            self.detail.set(
                self.detail.get() + "\nSelf-update is available from packaged SDK builds; "
                "this source checkout will not replace itself."
            )
        self._busy(False)

    def _progress(self, label: str, current: int, total: int) -> None:
        percentage = int(current * 100 / total) if total else 0
        self.after(0, lambda: (
            self.status.set(label + "…"),
            self.progress.configure(value=max(0, min(100, percentage))),
        ))

    def install(self) -> None:
        if self.release is None or self.install_root is None:
            return
        self._run(
            lambda: stage_release(
                self.release, self.install_root, progress=self._progress,
            ),
            self._staged,
        )

    def _staged(self, staged: StagedUpdate) -> None:
        self.staged = staged
        self.progress.configure(value=100)
        self.status.set(f"ALLIN1 SDK {staged.version} is ready")
        self.detail.set("Restart the SDK to finish the verified update.")
        self._busy(False)
        self.install_button.configure(text="Restart and update", command=self.restart)
        self.install_button.configure(state="normal")

    def restart(self) -> None:
        if self.staged is None:
            return
        if not messagebox.askyesno(
            "Restart ALLIN1 SDK",
            "Close the SDK, install the verified update, and reopen it now?",
            parent=self,
        ):
            return
        self.grab_release()
        if not self.close_sdk():
            self.grab_set()
            return
        try:
            schedule_staged_update(self.staged)
        except Exception as exc:
            messagebox.showerror("Could not start SDK update", str(exc))

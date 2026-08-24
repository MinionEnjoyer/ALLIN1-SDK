"""Compact independently collapsible side panes for three-column workspaces."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


DIVIDER_WIDTH = 16


class CollapsibleSidePanes:
    """Manage two focusable divider arrows around a persistent center pane.

    The three host frames remain in the ``ttk.Panedwindow`` in stable order so
    its native sashes stay draggable.  Collapsing hides only a side pane's
    content and narrows its host to the divider strip; the center therefore
    receives the released space while both arrows remain reachable.
    """

    def __init__(
        self, paned: ttk.Panedwindow, *, left_width: int = 230,
        center_width: int = 520, right_width: int = 310,
        left_weight: int = 2, center_weight: int = 5, right_weight: int = 3,
        left_label: str = "left", right_label: str = "right",
    ) -> None:
        self.paned = paned
        self.left_width = max(DIVIDER_WIDTH + 32, int(left_width))
        self.right_width = max(DIVIDER_WIDTH + 32, int(right_width))
        self._left_weight = max(1, int(left_weight))
        self._center_weight = max(1, int(center_weight))
        self._right_weight = max(1, int(right_weight))
        self.left_label = str(left_label).strip() or "left"
        self.right_label = str(right_label).strip() or "right"
        self.left_collapsed = False
        self.right_collapsed = False
        self.left_content: tk.Widget | None = None
        self.center_content: tk.Widget | None = None
        self.right_content: tk.Widget | None = None
        self._layout_job: str | None = None
        self._restore_left = False
        self._restore_right = False

        self.left_host = ttk.Frame(paned, width=self.left_width)
        self.center_host = ttk.Frame(paned, width=max(160, int(center_width)))
        self.right_host = ttk.Frame(paned, width=self.right_width)
        for host in (self.left_host, self.center_host, self.right_host):
            host.grid_propagate(False)
            host.rowconfigure(0, weight=1)

        self.left_host.columnconfigure(0, weight=1)
        self.left_host.columnconfigure(1, minsize=DIVIDER_WIDTH)
        self.center_host.columnconfigure(0, weight=1)
        self.right_host.columnconfigure(0, minsize=DIVIDER_WIDTH)
        self.right_host.columnconfigure(1, weight=1)

        self.left_divider, self.left_toggle = self._divider(
            self.left_host, "left", column=1,
        )
        self.right_divider, self.right_toggle = self._divider(
            self.right_host, "right", column=0,
        )
        paned.add(self.left_host, weight=self._left_weight)
        paned.add(self.center_host, weight=self._center_weight)
        paned.add(self.right_host, weight=self._right_weight)
        paned.bind("<Configure>", self._configured, add="+")
        paned.bind("<ButtonRelease-1>", self._sash_released, add="+")
        paned.bind("<Destroy>", self._destroyed, add="+")

    def _divider(
        self, parent: ttk.Frame, side: str, *, column: int,
    ) -> tuple[tk.Frame, tk.Button]:
        divider = tk.Frame(
            parent, width=DIVIDER_WIDTH, background="#d5ded9",
            borderwidth=0, highlightthickness=0,
        )
        divider.grid(row=0, column=column, sticky="ns")
        divider.grid_propagate(False)
        tk.Frame(
            divider, width=1, background="#aebdb5",
            borderwidth=0, highlightthickness=0,
        ).place(relx=0.5, y=0, relheight=1.0, anchor="n")
        command = self.toggle_left if side == "left" else self.toggle_right
        button = tk.Button(
            divider, text="<" if side == "left" else ">", command=command,
            width=1, padx=0, pady=0, borderwidth=0, relief="flat",
            background="#1f7f42", foreground="#ffffff",
            activebackground="#176b36", activeforeground="#ffffff",
            highlightbackground="#76cf8e", highlightcolor="#c8f3d4",
            highlightthickness=0, takefocus=True, cursor="hand2",
            font=("Segoe UI Semibold", 9),
        )
        button.place(
            relx=0.5, rely=0.5, anchor="center",
            width=DIVIDER_WIDTH, height=30,
        )
        action = self.left_label if side == "left" else self.right_label
        button.bind("<Return>", command)
        button.bind("<KP_Enter>", command)
        button.bind("<space>", command)
        button.bind(
            "<FocusIn>",
            lambda _event, control=button: control.configure(background="#176b36"),
        )
        button.bind(
            "<FocusOut>",
            lambda _event, control=button: control.configure(background="#1f7f42"),
        )
        # Exposed for help text, tests, and screen-reader adapters that inspect
        # widget metadata rather than relying on the one-character label.
        button.accessible_name = f"Collapse {action} pane"  # type: ignore[attr-defined]
        return divider, button

    def set_contents(
        self, left: tk.Widget, center: tk.Widget, right: tk.Widget,
    ) -> None:
        """Attach one content widget to each stable host frame."""

        expected = (self.left_host, self.center_host, self.right_host)
        supplied = (left.master, center.master, right.master)
        if supplied != expected:
            raise ValueError("Side-pane content must be created in its matching host")
        self.left_content, self.center_content, self.right_content = left, center, right
        left.grid(row=0, column=0, sticky="nsew")
        center.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew")

    @property
    def has_collapsed_side(self) -> bool:
        return self.left_collapsed or self.right_collapsed

    def pane_order(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.paned.panes())

    def toggle_left(self, _event: object | None = None) -> str:
        self.set_left_collapsed(not self.left_collapsed)
        return "break"

    def toggle_right(self, _event: object | None = None) -> str:
        self.set_right_collapsed(not self.right_collapsed)
        return "break"

    def set_left_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self.left_collapsed or self.left_content is None:
            return
        if collapsed:
            self._capture_width("left")
            self._move_focus_before_hide(self.left_content, self.left_toggle)
            self.left_collapsed = True
            self.left_content.grid_remove()
            self.left_host.configure(width=DIVIDER_WIDTH)
            self.paned.pane(self.left_host, weight=0)
        else:
            self.left_collapsed = False
            self.left_content.grid()
            self.left_host.configure(width=self.left_width)
            self.paned.pane(self.left_host, weight=self._left_weight)
            self._restore_left = True
        self._update_labels()
        self._schedule_layout()

    def set_right_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self.right_collapsed or self.right_content is None:
            return
        if collapsed:
            self._capture_width("right")
            self._move_focus_before_hide(self.right_content, self.right_toggle)
            self.right_collapsed = True
            self.right_content.grid_remove()
            self.right_host.configure(width=DIVIDER_WIDTH)
            self.paned.pane(self.right_host, weight=0)
        else:
            self.right_collapsed = False
            self.right_content.grid()
            self.right_host.configure(width=self.right_width)
            self.paned.pane(self.right_host, weight=self._right_weight)
            self._restore_right = True
        self._update_labels()
        self._schedule_layout()

    def remember_expanded_widths(self) -> None:
        """Capture widths after a user finishes dragging either native sash."""

        if not self.left_collapsed:
            self._capture_width("left")
        if not self.right_collapsed:
            self._capture_width("right")

    def enforce_layout(self) -> None:
        """Reapply collapsed edges after a parent resize or balancing pass."""

        self._apply_layout()

    def _capture_width(self, side: str) -> None:
        host = self.left_host if side == "left" else self.right_host
        width = host.winfo_width()
        if width > DIVIDER_WIDTH + 24:
            if side == "left":
                self.left_width = width
            else:
                self.right_width = width

    def _update_labels(self) -> None:
        self.left_toggle.configure(text=">" if self.left_collapsed else "<")
        self.right_toggle.configure(text="<" if self.right_collapsed else ">")
        self.left_toggle.accessible_name = (  # type: ignore[attr-defined]
            f"Expand {self.left_label} pane"
            if self.left_collapsed else f"Collapse {self.left_label} pane"
        )
        self.right_toggle.accessible_name = (  # type: ignore[attr-defined]
            f"Expand {self.right_label} pane"
            if self.right_collapsed else f"Collapse {self.right_label} pane"
        )

    def _move_focus_before_hide(self, content: tk.Widget, toggle: tk.Button) -> None:
        try:
            focused = self.paned.focus_get()
        except tk.TclError:
            return
        current = focused
        while current is not None:
            if current is content:
                toggle.focus_set()
                return
            current = getattr(current, "master", None)

    def _configured(self, _event: object | None = None) -> None:
        if self.has_collapsed_side:
            self._schedule_layout()

    def _sash_released(self, _event: object | None = None) -> None:
        if self.has_collapsed_side:
            self._schedule_layout()
            return
        self.remember_expanded_widths()

    def _schedule_layout(self) -> None:
        if self._layout_job is not None:
            try:
                self.paned.after_cancel(self._layout_job)
            except tk.TclError:
                pass
        self._layout_job = self.paned.after_idle(self._apply_layout)

    def _apply_layout(self) -> None:
        self._layout_job = None
        if not self.paned.winfo_exists():
            return
        width = self.paned.winfo_width()
        if width <= DIVIDER_WIDTH * 3:
            return
        sash_width = self._sash_width()
        try:
            if self.left_collapsed:
                self.paned.sashpos(0, DIVIDER_WIDTH)
            elif self._restore_left:
                self.paned.sashpos(
                    0, min(max(DIVIDER_WIDTH + 32, self.left_width), width - 160),
                )
            if self.right_collapsed:
                self.paned.sashpos(1, width - DIVIDER_WIDTH - sash_width)
            elif self._restore_right:
                self.paned.sashpos(
                    1, max(
                        160,
                        width - max(DIVIDER_WIDTH + 32, self.right_width) - sash_width,
                    ),
                )
        except tk.TclError:
            return
        finally:
            self._restore_left = False
            self._restore_right = False

    def _sash_width(self) -> int:
        """Return the live ttk sash width without assuming a platform theme."""

        total = self.paned.winfo_width() - sum(
            host.winfo_width()
            for host in (self.left_host, self.center_host, self.right_host)
        )
        if 0 < total <= 32:
            return max(1, round(total / 2))
        try:
            configured = ttk.Style(self.paned).lookup(
                self.paned.winfo_class(), "sashwidth",
            )
            if configured:
                return max(1, int(float(configured)))
        except (tk.TclError, TypeError, ValueError):
            pass
        return 5

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is not self.paned or self._layout_job is None:
            return
        try:
            self.paned.after_cancel(self._layout_job)
        except tk.TclError:
            pass
        self._layout_job = None


__all__ = ["CollapsibleSidePanes", "DIVIDER_WIDTH"]

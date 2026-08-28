"""Shared desktop-window behavior for the ALLIN1 SDK.

The SDK is still a Tk application, so a small amount of explicit Windows/DPI
handling is needed before any widgets are created.  Keeping it here prevents
each compatibility window from inventing its own sizing rules.
"""

from __future__ import annotations

import json
import os
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Mapping


# Keep the desktop shell on the same restrained palette as the Launcher. The
# specialist workspaces inherit these named styles, so they can stay dense
# without each inventing a separate visual language.
BRAND_GREEN = "#2d9c50"
BRAND_DARK_GREEN = "#1f7f42"
BRAND_DEEP_GREEN = "#176b36"
BODY_BACKGROUND = "#f4f7f5"
SURFACE_BACKGROUND = "#ffffff"
PRIMARY_TEXT = "#173d32"
MUTED_TEXT = "#52635c"
SUCCESS_TEXT = "#18753a"
WARNING_TEXT = "#9a6700"
ERROR_TEXT = "#b42318"

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_SYSTEM = "system"
THEME_MODES = (THEME_LIGHT, THEME_DARK, THEME_SYSTEM)
UI_SETTINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SdkPalette:
    """Semantic desktop colors shared by ttk and native Tk widgets."""

    body: str
    surface: str
    surface_alt: str
    input: str
    border: str
    primary: str
    secondary: str
    muted: str
    brand: str
    brand_hover: str
    brand_deep: str
    selection: str
    selection_hover: str
    disabled_background: str
    disabled_foreground: str
    inverse_text: str
    success: str
    warning: str
    error: str
    info: str


LIGHT_PALETTE = SdkPalette(
    body=BODY_BACKGROUND,
    surface=SURFACE_BACKGROUND,
    surface_alt="#eef3f0",
    input="#ffffff",
    border="#d4ddd9",
    primary=PRIMARY_TEXT,
    secondary="#26332e",
    muted=MUTED_TEXT,
    brand=BRAND_GREEN,
    brand_hover=BRAND_DARK_GREEN,
    brand_deep=BRAND_DEEP_GREEN,
    selection="#dcefe3",
    selection_hover="#d2e8da",
    disabled_background="#c8d4cc",
    disabled_foreground="#66756e",
    inverse_text="#ffffff",
    success=SUCCESS_TEXT,
    warning=WARNING_TEXT,
    error=ERROR_TEXT,
    info="#2563a3",
)

DARK_PALETTE = SdkPalette(
    body="#0f1512",
    surface="#171d19",
    surface_alt="#1d2722",
    input="#202a25",
    border="#34443c",
    primary="#e7f0eb",
    secondary="#cedbd4",
    muted="#9db0a6",
    brand="#50bd70",
    brand_hover="#65cf82",
    brand_deep="#3aa65c",
    selection="#23643a",
    selection_hover="#2b7545",
    disabled_background="#29332e",
    disabled_foreground="#718078",
    inverse_text="#ffffff",
    success="#65c982",
    warning="#f0b64a",
    error="#ff766d",
    info="#73aee8",
)


def normalize_theme_mode(value: object, *, default: str = THEME_SYSTEM) -> str:
    """Return one canonical persisted theme value."""

    normalized = str(value or "").strip().casefold()
    return normalized if normalized in THEME_MODES else default


def shared_ui_settings_path(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Return the Launcher/SDK shared per-user appearance settings file."""

    values = os.environ if environ is None else environ
    base = str(
        values.get("LOCALAPPDATA", "") or values.get("XDG_CONFIG_HOME", "")
    ).strip()
    if base:
        root = Path(base).expanduser()
    else:
        user_home = Path.home() if home is None else Path(home)
        root = (
            user_home / "AppData" / "Local"
            if os.name == "nt" else user_home / ".config"
        )
    return root / "ALLIN1" / "ui-settings.json"


def load_ui_theme(path: str | Path | None = None) -> str:
    """Load a shared preference defensively; missing/corrupt files use System."""

    source = Path(path) if path is not None else shared_ui_settings_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return THEME_SYSTEM
    if not isinstance(payload, dict):
        return THEME_SYSTEM
    return normalize_theme_mode(payload.get("theme"))


def save_ui_theme(mode: object, path: str | Path | None = None) -> Path:
    """Atomically update the shared preference while preserving future fields."""

    normalized = normalize_theme_mode(mode)
    destination = Path(path) if path is not None else shared_ui_settings_path()
    payload: dict[str, object] = {}
    try:
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            payload.update(existing)
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    payload["schema_version"] = UI_SETTINGS_SCHEMA_VERSION
    payload["theme"] = normalized
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def system_theme_from_apps_use_light_theme(value: object) -> str:
    """Translate the Windows AppsUseLightTheme registry value."""

    try:
        return THEME_LIGHT if int(value) else THEME_DARK
    except (TypeError, ValueError):
        return THEME_LIGHT


def detect_system_theme() -> str:
    """Resolve the OS application theme, falling back safely to Light."""

    if os.name != "nt":
        return THEME_LIGHT
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return system_theme_from_apps_use_light_theme(value)
    except (ImportError, OSError, ValueError):
        return THEME_LIGHT


def resolve_theme_mode(mode: object) -> str:
    """Resolve Light/Dark/System to the effective two-palette theme."""

    normalized = normalize_theme_mode(mode)
    return detect_system_theme() if normalized == THEME_SYSTEM else normalized


def palette_for_theme(mode: object) -> SdkPalette:
    """Return a palette for an effective or requested theme value."""

    return DARK_PALETTE if resolve_theme_mode(mode) == THEME_DARK else LIGHT_PALETTE


@dataclass(frozen=True)
class ShellStatusPresentation:
    """One compact, non-modal activity-bar presentation."""

    tone: str
    glyph: str
    label_style: str
    indicator_style: str


def shell_status_presentation(message: str) -> ShellStatusPresentation:
    """Classify shell activity without coupling operations to Tk widgets."""

    normalized = str(message).strip().casefold()
    diagnostic = re.sub(r"\b0\s+errors?\b", "", normalized)
    diagnostic = re.sub(r"\b0\s+warnings?\b", "", diagnostic)
    if any(token in diagnostic for token in (
        "failed", "error", "could not", "invalid", "blocked", "refused",
    )):
        tone = "error"
    elif any(token in diagnostic for token in (
        "warning", "not found", "required", "no packages", "select a ",
    )):
        tone = "warning"
    elif normalized.endswith("…") or any(token in normalized for token in (
        "loading", "auditing", "inspecting", "compiling", "working",
    )):
        tone = "busy"
    elif any(token in normalized for token in (
        "written", "wrote ", "exported", "copied", "refreshed", "equivalent",
        "ready", "loaded", "compiled",
    )):
        tone = "success"
    else:
        tone = "info"
    glyph = "◌" if tone == "busy" else "●"
    return ShellStatusPresentation(
        tone=tone,
        glyph=glyph,
        label_style=f"Activity.{tone.title()}.TLabel",
        indicator_style=f"ActivityDot.{tone.title()}.TLabel",
    )


def configure_sdk_style(root: tk.Misc, theme: object = THEME_LIGHT) -> None:
    """Configure the complete SDK ttk catalog for one effective theme."""

    palette = palette_for_theme(theme)
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.option_add("*tearOff", False)
    root.option_add("*Menu.font", ("Segoe UI", 10))
    root.option_add("*Menu.background", palette.surface)
    root.option_add("*Menu.foreground", palette.secondary)
    root.option_add("*Menu.activeBackground", palette.selection)
    root.option_add("*Menu.activeForeground", palette.primary)
    root.option_add("*Menu.disabledForeground", palette.disabled_foreground)
    root.option_add("*Menu.selectColor", palette.brand)
    root.option_add("*Listbox.background", palette.input)
    root.option_add("*Listbox.foreground", palette.primary)
    root.option_add("*Listbox.selectBackground", palette.selection)
    root.option_add("*Listbox.selectForeground", palette.primary)
    root.option_add("*Text.background", palette.input)
    root.option_add("*Text.foreground", palette.primary)
    root.option_add("*Text.insertBackground", palette.primary)
    root.option_add("*TCombobox*Listbox.background", palette.input)
    root.option_add("*TCombobox*Listbox.foreground", palette.primary)
    root.option_add("*TCombobox*Listbox.selectBackground", palette.selection)
    root.option_add(
        "*TCombobox*Listbox.selectForeground", palette.primary,
    )
    try:
        root.configure(background=palette.body)
    except tk.TclError:
        pass

    style.configure(
        ".", font=("Segoe UI", 10), background=palette.body,
        foreground=palette.primary, bordercolor=palette.border,
        lightcolor=palette.border, darkcolor=palette.border,
    )
    style.configure("TFrame", background=palette.body)
    style.configure("TLabel", background=palette.body, foreground=palette.primary)
    style.configure("Surface.TFrame", background=palette.surface)
    style.configure("Workspace.TFrame", background=palette.body)
    style.configure("StatusBar.TFrame", background=palette.surface)
    for name, padding in (("TButton", (11, 7)), ("Quiet.TButton", (10, 7))):
        style.configure(
            name, padding=padding, background=palette.surface_alt,
            foreground=palette.primary, bordercolor=palette.border,
        )
        style.map(
            name,
            background=[
                ("active", palette.selection),
                ("pressed", palette.selection_hover),
                ("disabled", palette.disabled_background),
            ],
            foreground=[("disabled", palette.disabled_foreground)],
        )
    style.configure(
        "Danger.TButton", foreground=palette.error, padding=(10, 7),
        background=palette.surface_alt,
    )
    style.configure(
        "TEntry", padding=(7, 6), fieldbackground=palette.input,
        foreground=palette.primary, insertcolor=palette.primary,
        bordercolor=palette.border,
    )
    style.map(
        "TEntry", fieldbackground=[("disabled", palette.disabled_background)],
        foreground=[("disabled", palette.disabled_foreground)],
    )
    style.configure(
        "TCombobox", padding=(6, 5), fieldbackground=palette.input,
        background=palette.surface_alt, foreground=palette.primary,
        arrowcolor=palette.primary, bordercolor=palette.border,
    )
    style.map(
        "TCombobox",
        fieldbackground=[
            ("readonly", palette.input),
            ("disabled", palette.disabled_background),
        ],
        foreground=[("disabled", palette.disabled_foreground)],
        selectbackground=[("readonly", palette.input)],
        selectforeground=[("readonly", palette.primary)],
    )
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(
            name, padding=(0, 2), background=palette.body,
            foreground=palette.primary, indicatorcolor=palette.input,
        )
        style.map(
            name,
            background=[("active", palette.body)],
            foreground=[("disabled", palette.disabled_foreground)],
            indicatorcolor=[
                ("selected", palette.brand),
                ("disabled", palette.disabled_background),
            ],
        )
    style.configure(
        "TLabelframe", background=palette.surface, bordercolor=palette.border,
    )
    style.configure(
        "TLabelframe.Label", background=palette.body,
        foreground=palette.brand_hover, font=("Segoe UI Semibold", 10),
    )
    for name in ("Accent.TButton", "Accent.TMenubutton"):
        style.configure(
            name, background=palette.brand, foreground=palette.inverse_text,
            font=("Segoe UI Semibold", 10), padding=(13, 8),
            bordercolor=palette.brand_deep,
        )
        style.map(
            name,
            background=[
                ("active", palette.brand_hover),
                ("pressed", palette.brand_deep),
                ("disabled", palette.disabled_background),
            ],
            foreground=[("disabled", palette.disabled_foreground)],
        )
    style.configure(
        "TMenubutton", background=palette.surface_alt,
        foreground=palette.primary, arrowcolor=palette.primary,
        bordercolor=palette.border,
    )
    style.configure("Quiet.TMenubutton", padding=(11, 7))
    style.configure(
        "Nav.TButton", anchor="w", padding=(16, 11), relief="flat",
        background=palette.surface_alt, foreground=palette.muted,
    )
    style.map(
        "Nav.TButton",
        background=[("active", palette.selection), ("focus", palette.selection)],
        foreground=[("disabled", palette.disabled_foreground)],
    )
    style.configure(
        "NavSelected.TButton", anchor="w", padding=(16, 11), relief="flat",
        background=palette.selection, foreground=palette.brand_hover,
        font=("Segoe UI Semibold", 10),
    )
    style.map(
        "NavSelected.TButton",
        background=[
            ("active", palette.selection_hover),
            ("focus", palette.selection_hover),
        ],
    )
    style.configure(
        "DialogTitle.TLabel", font=("Segoe UI Semibold", 17),
        foreground=palette.primary,
    )
    style.configure(
        "PageTitle.TLabel", font=("Segoe UI Semibold", 15),
        foreground=palette.primary,
    )
    style.configure("PageIntro.TLabel", foreground=palette.muted)
    style.configure(
        "Section.TLabel", font=("Segoe UI Semibold", 11),
        foreground=palette.primary,
    )
    style.configure(
        "Treeview", rowheight=28, font=("Segoe UI", 10),
        background=palette.surface, fieldbackground=palette.surface,
        foreground=palette.primary, bordercolor=palette.border,
    )
    style.configure(
        "Treeview.Heading", font=("Segoe UI Semibold", 9), padding=(7, 7),
        background=palette.surface_alt, foreground=palette.secondary,
        bordercolor=palette.border,
    )
    style.map(
        "Treeview",
        background=[("selected", palette.selection)],
        foreground=[("selected", palette.primary)],
    )
    style.configure("TNotebook", background=palette.body, borderwidth=0)
    style.configure(
        "TNotebook.Tab", font=("Segoe UI Semibold", 10), padding=(14, 8),
        background=palette.surface_alt, foreground=palette.muted,
    )
    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", palette.surface),
            ("active", palette.selection),
        ],
        foreground=[("selected", palette.brand_hover)],
    )
    style.configure("TScrollbar", background=palette.surface_alt,
                    troughcolor=palette.body, arrowcolor=palette.primary,
                    bordercolor=palette.border)
    style.configure("TSeparator", background=palette.border)
    style.configure("TPanedwindow", background=palette.border)
    style.configure(
        "FieldLabel.TLabel", font=("Segoe UI Semibold", 9),
        foreground=palette.muted,
    )
    style.configure("Muted.TLabel", foreground=palette.muted)
    style.configure("Success.TLabel", foreground=palette.success)
    style.configure("Warning.TLabel", foreground=palette.warning)
    style.configure("Error.TLabel", foreground=palette.error)
    for name, background in (
        ("Link.TButton", palette.body),
        ("HeaderLink.TButton", palette.surface),
    ):
        style.configure(
            name, relief="flat", borderwidth=0,
            padding=(4, 3) if name == "Link.TButton" else (4, 2),
            background=background, foreground=palette.brand_hover,
            font=("Segoe UI Semibold", 9, "underline"),
        )
        style.map(
            name,
            foreground=[("active", palette.brand), ("focus", palette.brand)],
            background=[
                ("active", palette.selection),
                ("focus", palette.selection),
            ],
        )
    for tone, color in (
        ("Info", palette.muted), ("Busy", palette.info),
        ("Success", palette.success), ("Warning", palette.warning),
        ("Error", palette.error),
    ):
        style.configure(
            f"Activity.{tone}.TLabel", background=palette.surface,
            foreground=color,
        )
        style.configure(
            f"ActivityDot.{tone}.TLabel", background=palette.surface,
            foreground=color, font=("Segoe UI Semibold", 10),
        )
    style.configure(
        "StatusHint.TLabel", background=palette.surface,
        foreground=palette.muted, font=("Segoe UI", 9),
    )


_LIGHT_BACKGROUND_ROLES = {
    "#f4f7f5": "body",
    "#ffffff": "surface",
    "white": "surface",
    "#eef3f0": "surface_alt",
    "#e2ebe6": "selection",
    "#e6efe9": "selection",
    "#e3eee7": "surface_alt",
    "#dcefe3": "selection",
    "#d2e8da": "selection_hover",
    "#c9e4d3": "selection_hover",
    "#dce8e1": "surface_alt",
    "#dfe9e2": "surface_alt",
    "#d7e0dc": "border",
    "#d5ded9": "border",
    "#d4ddd9": "border",
    "#aebdb5": "border",
    "#c8d4cc": "disabled_background",
    "#2d9c50": "brand",
    "#1f7f42": "brand_hover",
    "#176b36": "brand_deep",
}

_LIGHT_FOREGROUND_ROLES = {
    "#173d32": "primary",
    "#24332d": "primary",
    "#26332e": "secondary",
    "#1e2925": "primary",
    "#3c5048": "muted",
    "#37584d": "muted",
    "#52635c": "muted",
    "#646e69": "muted",
    "#66756e": "disabled_foreground",
    "#66756f": "disabled_foreground",
    "#6d7a74": "muted",
    "#76847e": "muted",
    "#84928c": "disabled_foreground",
    "#2d9c50": "brand",
    "#1f7f42": "brand_hover",
    "#176b36": "brand_deep",
    "#0e5228": "brand_hover",
    "#18753a": "success",
    "#2563a3": "info",
    "#9a6700": "warning",
    "#8a5a00": "warning",
    "#9a6500": "warning",
    "#b42318": "error",
    "#a52a2a": "error",
    "#9f1d20": "error",
    "#9a3412": "error",
    "#ffffff": "inverse_text",
    "white": "inverse_text",
}


def _theme_role_lookups() -> tuple[dict[str, str], dict[str, str]]:
    backgrounds = dict(_LIGHT_BACKGROUND_ROLES)
    foregrounds = dict(_LIGHT_FOREGROUND_ROLES)
    background_roles = {
        "body", "surface", "surface_alt", "input", "border", "brand",
        "brand_hover", "brand_deep", "selection", "selection_hover",
        "disabled_background", "success", "warning", "error", "info",
    }
    foreground_roles = {
        "primary", "secondary", "muted", "brand", "brand_hover",
        "brand_deep", "disabled_foreground", "inverse_text", "success",
        "warning", "error", "info",
    }
    for palette in (LIGHT_PALETTE, DARK_PALETTE):
        for role in SdkPalette.__dataclass_fields__:
            value = str(getattr(palette, role)).casefold()
            if role in background_roles:
                backgrounds.setdefault(value, role)
            if role in foreground_roles:
                foregrounds.setdefault(value, role)
    return backgrounds, foregrounds


_BACKGROUND_ROLES, _FOREGROUND_ROLES = _theme_role_lookups()

_NATIVE_COLOR_OPTIONS = (
    "background", "foreground", "activebackground", "activeforeground",
    "selectbackground", "selectforeground", "insertbackground",
    "highlightbackground", "highlightcolor", "disabledforeground",
    "readonlybackground", "troughcolor",
)


def _theme_root(widget: tk.Misc) -> tk.Misc:
    try:
        return widget._root()
    except (AttributeError, tk.TclError):
        return widget


def _widget_menu_children(widget: tk.Misc) -> tuple[tk.Misc, ...]:
    menus: list[tk.Misc] = []
    if isinstance(widget, tk.Menu):
        end = widget.index("end")
        if end is not None:
            for index in range(int(end) + 1):
                try:
                    name = str(widget.entrycget(index, "menu")).strip()
                    if name:
                        menus.append(widget.nametowidget(name))
                except (KeyError, tk.TclError):
                    continue
    else:
        try:
            name = str(widget.cget("menu")).strip()
            if name:
                menus.append(widget.nametowidget(name))
        except (KeyError, tk.TclError):
            pass
    return tuple(menus)


def _is_fixed_dark_surface(widget: tk.Misc) -> bool:
    """Recognize intentionally dark technical roots without color-name guesses."""

    if isinstance(widget, ttk.Widget):
        return False
    try:
        if "background" not in widget.keys():
            return False
        value = str(widget.cget("background")).strip().casefold()
    except (AttributeError, tk.TclError):
        return False
    if value in _BACKGROUND_ROLES or not re.fullmatch(r"#[0-9a-f]{6}", value):
        return False
    red, green, blue = (int(value[index:index + 2], 16) for index in (1, 3, 5))
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0
    return luminance < 0.22


def _walk_native_widgets(widget: tk.Misc):
    seen: set[str] = set()
    pending: list[tk.Misc] = [widget]
    while pending:
        current = pending.pop()
        identity = str(current)
        if identity in seen:
            continue
        seen.add(identity)
        # Technical work surfaces may opt out once at their ancestor. Their
        # diagnostic colors and every descendant are then left untouched.
        if (
            bool(getattr(current, "_allin1_theme_exempt", False))
            or _is_fixed_dark_surface(current)
        ):
            continue
        yield current
        try:
            pending.extend(current.winfo_children())
        except tk.TclError:
            pass
        pending.extend(_widget_menu_children(current))


def _mapped_color(
    value: object, palette: SdkPalette, *, foreground: bool,
) -> str | None:
    roles = _FOREGROUND_ROLES if foreground else _BACKGROUND_ROLES
    role = roles.get(str(value).strip().casefold())
    return str(getattr(palette, role)) if role is not None else None


def _recolor_native_widget(widget: tk.Misc, palette: SdkPalette) -> None:
    """Translate explicit widget colors without touching Canvas items."""

    if getattr(widget, "_allin1_theme_exempt", False):
        return
    if isinstance(widget, tk.Menu):
        try:
            widget.configure(
                background=palette.surface,
                foreground=palette.primary,
                activebackground=palette.selection,
                activeforeground=palette.primary,
                disabledforeground=palette.disabled_foreground,
                selectcolor=palette.brand,
            )
        except tk.TclError:
            pass
        return
    try:
        available = set(widget.keys())
    except (AttributeError, tk.TclError):
        return
    updates: dict[str, str] = {}
    for option in _NATIVE_COLOR_OPTIONS:
        if option not in available:
            continue
        try:
            replacement = _mapped_color(
                widget.cget(option), palette,
                foreground=option in {
                    "foreground", "activeforeground", "selectforeground",
                    "insertbackground", "disabledforeground", "highlightcolor",
                },
            )
        except tk.TclError:
            continue
        if replacement is not None:
            updates[option] = replacement
    if updates:
        try:
            widget.configure(**updates)
        except tk.TclError:
            pass
    if isinstance(widget, tk.Text):
        try:
            tags = widget.tag_names()
        except tk.TclError:
            tags = ()
        for tag in tags:
            tag_updates: dict[str, str] = {}
            for option in ("background", "foreground"):
                try:
                    replacement = _mapped_color(
                        widget.tag_cget(tag, option), palette,
                        foreground=option == "foreground",
                    )
                except tk.TclError:
                    continue
                if replacement is not None:
                    tag_updates[option] = replacement
            if tag_updates:
                try:
                    widget.tag_configure(tag, **tag_updates)
                except tk.TclError:
                    pass


def apply_native_widget_theme(widget: tk.Misc, effective_theme: object) -> None:
    """Restyle existing classic Tk widgets, menus, and compatibility windows."""

    palette = palette_for_theme(effective_theme)
    for current in _walk_native_widgets(widget):
        _recolor_native_widget(current, palette)


def apply_windows_dark_title_bar(window: tk.Misc, dark: bool) -> bool:
    """Ask DWM for a matching Windows title bar when the API is available."""

    if os.name != "nt":
        return False
    try:
        import ctypes

        window.update_idletasks()
        client = int(window.winfo_id())
        parent = int(ctypes.windll.user32.GetParent(client))
        hwnd = parent or client
        enabled = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # Win10 20H1+, then older Win10 fallback.
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), ctypes.c_uint(attribute),
                ctypes.byref(enabled), ctypes.sizeof(enabled),
            )
            if int(result) == 0:
                return True
    except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
        pass
    return False


def _apply_title_bars(widget: tk.Misc, dark: bool) -> None:
    for current in _walk_native_widgets(widget):
        if isinstance(current, (tk.Tk, tk.Toplevel)):
            apply_windows_dark_title_bar(current, dark)


def current_theme_mode(widget: tk.Misc) -> str:
    return normalize_theme_mode(
        getattr(_theme_root(widget), "_allin1_theme_mode", THEME_SYSTEM),
    )


def current_effective_theme(widget: tk.Misc) -> str:
    return normalize_theme_mode(
        getattr(_theme_root(widget), "_allin1_effective_theme", THEME_LIGHT),
        default=THEME_LIGHT,
    )


def apply_sdk_theme(
    widget: tk.Misc,
    mode: object,
    *,
    persist: bool = False,
    settings_path: str | Path | None = None,
) -> str:
    """Apply one requested theme immediately across the whole Tk application."""

    root = _theme_root(widget)
    requested = normalize_theme_mode(mode)
    effective = resolve_theme_mode(requested)
    previous = current_effective_theme(root)
    if persist:
        save_ui_theme(requested, settings_path)
    root._allin1_theme_mode = requested
    configure_sdk_style(root, effective)
    # Repeated Dark application is intentional: lazily created workspaces and
    # compatibility Toplevels begin with legacy light literals and need one pass.
    if effective != previous or effective == THEME_DARK:
        apply_native_widget_theme(root, effective)
    root._allin1_effective_theme = effective
    _apply_title_bars(root, effective == THEME_DARK)
    try:
        root.event_generate("<<SdkThemeChanged>>", when="tail")
    except tk.TclError:
        pass
    return effective


def install_theme_window_hook(root: tk.Misc) -> None:
    """Theme Toplevels and lazy widgets as they are mapped."""

    owner = _theme_root(root)
    if getattr(owner, "_allin1_theme_hook_installed", False):
        return
    owner._allin1_theme_hook_installed = True
    pending: set[str] = set()

    def mapped(event: tk.Event) -> None:
        try:
            top = event.widget.winfo_toplevel()
            identity = str(top)
        except (AttributeError, tk.TclError):
            return
        if identity in pending:
            return
        pending.add(identity)

        def finish() -> None:
            pending.discard(identity)
            try:
                effective = current_effective_theme(owner)
                if effective == THEME_DARK:
                    apply_native_widget_theme(top, effective)
                apply_windows_dark_title_bar(top, effective == THEME_DARK)
            except tk.TclError:
                pass

        try:
            owner.after_idle(finish)
        except tk.TclError:
            pending.discard(identity)

    owner.bind_all("<Map>", mapped, add="+")


def start_system_theme_polling(root: tk.Misc, *, interval_ms: int = 2000) -> None:
    """Follow Windows app-theme changes while the shared mode is System."""

    owner = _theme_root(root)
    if getattr(owner, "_allin1_theme_poll_started", False):
        return
    owner._allin1_theme_poll_started = True

    def poll() -> None:
        try:
            if current_theme_mode(owner) == THEME_SYSTEM:
                effective = detect_system_theme()
                if effective != current_effective_theme(owner):
                    apply_sdk_theme(owner, THEME_SYSTEM)
            owner._allin1_theme_poll_after = owner.after(
                max(int(interval_ms), 250), poll,
            )
        except (tk.TclError, TypeError, ValueError):
            owner._allin1_theme_poll_started = False

    owner._allin1_theme_poll_after = owner.after(max(int(interval_ms), 250), poll)


def enable_windows_dpi_awareness() -> None:
    """Opt into the best DPI-awareness mode available on this Windows build."""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except (AttributeError, OSError):
            pass
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) in (0, -2147024891):
                return
        except (AttributeError, OSError):
            pass
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    except (ImportError, OSError):
        pass


def configure_tk_scaling(root: tk.Misc) -> float:
    """Use the monitor's reported DPI while keeping pathological values bounded."""
    try:
        scale = float(root.winfo_fpixels("1i")) / 72.0
        scale = min(max(scale, 1.0), 2.5)
        root.tk.call("tk", "scaling", scale)
        return scale
    except (tk.TclError, TypeError, ValueError):
        return 1.0


def fitted_geometry(
    preferred_width: int,
    preferred_height: int,
    screen_width: int,
    screen_height: int,
    *,
    margin: int = 24,
    taskbar_allowance: int = 56,
) -> tuple[int, int, int, int]:
    """Return a centered geometry that stays within the visible desktop."""
    screen_width = max(int(screen_width), 1)
    screen_height = max(int(screen_height), 1)
    usable_width = max(screen_width - margin * 2, 1)
    usable_height = max(screen_height - margin - taskbar_allowance, 1)
    width = max(min(int(preferred_width), usable_width), 1)
    height = max(min(int(preferred_height), usable_height), 1)
    x = max((screen_width - width) // 2, 0)
    y = max((screen_height - taskbar_allowance - height) // 2, 0)
    return width, height, x, y


def place_window(
    window: tk.Misc,
    *,
    preferred: tuple[int, int],
    minimum: tuple[int, int],
) -> tuple[int, int]:
    """Apply a screen-safe initial geometry and a reachable minimum size."""
    try:
        tk_scale = float(window.tk.call("tk", "scaling"))
        monitor_ratio = min(max(tk_scale / (96.0 / 72.0), 1.0), 2.5)
    except (tk.TclError, TypeError, ValueError):
        monitor_ratio = 1.0
    preferred_scaled = (
        round(preferred[0] * monitor_ratio),
        round(preferred[1] * monitor_ratio),
    )
    minimum_scaled = (
        round(minimum[0] * monitor_ratio),
        round(minimum[1] * monitor_ratio),
    )
    width, height, x, y = fitted_geometry(
        preferred_scaled[0], preferred_scaled[1],
        int(window.winfo_screenwidth()), int(window.winfo_screenheight()),
    )
    min_width = min(minimum_scaled[0], width)
    min_height = min(minimum_scaled[1], height)
    window.minsize(min_width, min_height)
    window.geometry(f"{width}x{height}+{x}+{y}")
    return width, height

"""GTA V installation path detection.

Searches using Steam appmanifest files, Windows Registry, Epic Games
manifests, Rockstar Games Launcher data, and drive scanning to find
GTA V (Legacy or Enhanced Edition).

When a path is detected, it is cached to ``.gta_path`` in the project
root so subsequent runs (including uninstall) can skip detection.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import string
from pathlib import Path

from allin1_sdk.paths import user_data_root

log = logging.getLogger("allin1_sdk.detector")

# Cache file written next to config.toml / pyproject.toml
_CACHE_FILE = ".gta_path"

# Steam App IDs for GTA V
_GTA_V_LEGACY_APPID = "271590"
_GTA_V_ENHANCED_APPID = "3240220"
_GTA_APPIDS = [_GTA_V_LEGACY_APPID, _GTA_V_ENHANCED_APPID]

# Known GTA V folder names (lowercase) for deep scan fallback
_GTA_FOLDER_NAMES = {
    "grand theft auto v",
    "grand theft auto v enhanced",
    "gtav",
    "gta v",
    "gta5",
}


def _project_root() -> Path:
    """Return the SDK's writable per-user state directory."""
    root = user_data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_cached_path() -> Path | None:
    """Read a previously-cached GTA V path from ``.gta_path``."""
    cache = _project_root() / _CACHE_FILE
    if not cache.exists():
        return None
    try:
        raw = cache.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        p = Path(raw)
        if _validate_gta_path(p):
            log.info("Loaded cached GTA V path: %s", p)
            return p
        log.warning("Cached path no longer valid: %s", raw)
        return None
    except OSError as exc:
        log.debug("Could not read cache file: %s", exc)
        return None


def save_cached_path(path: Path) -> None:
    """Write a detected GTA V path to ``.gta_path`` for reuse."""
    cache = _project_root() / _CACHE_FILE
    try:
        cache.write_text(str(path), encoding="utf-8")
        log.debug("Cached GTA V path to %s", cache)
    except OSError as exc:
        log.warning("Could not write cache file: %s", exc)


def detect_gta_path() -> Path | None:
    """Attempt to auto-detect the GTA V installation directory.

    Checks the ``.gta_path`` cache first, then runs platform-specific
    detection.  On success the result is cached for future runs.
    """
    cached = load_cached_path()
    if cached is not None:
        return cached

    system = platform.system()
    log.info("Starting GTA V auto-detection on %s", system)

    result: Path | None = None
    if system == "Windows":
        result = _detect_windows()
    elif system == "Linux":
        result = _detect_linux()
    else:
        log.info("macOS detected — no native GTA V, user must specify path manually")

    if result:
        log.info("GTA V found: %s", result)
        save_cached_path(result)
    else:
        log.warning("GTA V auto-detection failed — no valid install found")

    return result


# ---------------------------------------------------------------------------
# Windows detection
# ---------------------------------------------------------------------------

def _detect_windows() -> Path | None:
    """Main Windows detection pipeline, ordered by reliability."""

    # 1. Steam appmanifest files — THE most reliable method
    log.debug("=== Method 1: Steam appmanifest files ===")
    steam_result = _find_via_steam_appmanifest()
    if steam_result:
        return steam_result

    # 2. Windows Registry (Rockstar Games keys)
    log.debug("=== Method 2: Windows Registry ===")
    reg_result = _check_registry()
    if reg_result:
        log.debug("Registry hit: %s", reg_result)
        return reg_result
    log.debug("Registry: no valid GTA V paths found")

    # 3. Steam uninstall registry entries
    log.debug("=== Method 3: Steam uninstall registry ===")
    steam_unreg = _check_steam_uninstall_registry()
    if steam_unreg:
        return steam_unreg

    # 4. Epic Games manifests
    log.debug("=== Method 4: Epic Games manifests ===")
    epic_result = _find_epic_install_windows()
    if epic_result:
        return epic_result

    # 5. Rockstar Games Launcher
    log.debug("=== Method 5: Rockstar Games Launcher ===")
    rgl_result = _find_rockstar_launcher_windows()
    if rgl_result:
        return rgl_result

    # 6. Common hardcoded paths
    log.debug("=== Method 6: Common hardcoded paths ===")
    drives = _get_windows_drives()
    log.debug("Available drives: %s", [str(d) for d in drives])
    hardcoded = _check_hardcoded_paths(drives)
    if hardcoded:
        return hardcoded

    # 7. Deep filesystem scan (last resort)
    log.info("=== Method 7: Deep scan of all drives (last resort) ===")
    deep = _deep_scan_windows(drives)
    if deep:
        log.info("Deep scan found GTA V: %s", deep)
        return deep

    return None


# ---------------------------------------------------------------------------
# Steam detection (appmanifest-based — primary method)
# ---------------------------------------------------------------------------

def _find_via_steam_appmanifest() -> Path | None:
    """Find GTA V by parsing Steam appmanifest files.

    This is the most reliable method because appmanifest files contain
    the actual ``installdir`` — the real folder name, not a guess.
    """
    libraries = _find_all_steam_libraries()
    log.debug("Found %d Steam library folder(s)", len(libraries))

    for lib in libraries:
        steamapps = lib / "steamapps"
        if not steamapps.is_dir():
            # Some libraries have SteamApps (capital A) on case-sensitive FS
            steamapps = lib / "SteamApps"
            if not steamapps.is_dir():
                continue

        for appid in _GTA_APPIDS:
            manifest = steamapps / f"appmanifest_{appid}.acf"
            if not manifest.exists():
                continue

            log.debug("Found appmanifest: %s", manifest)
            installdir = _parse_appmanifest_installdir(manifest)
            if not installdir:
                log.debug("Could not parse installdir from %s", manifest)
                continue

            game_path = steamapps / "common" / installdir
            log.debug("Resolved game path: %s", game_path)

            if _validate_gta_path(game_path):
                log.info("Found GTA V via Steam appmanifest: %s", game_path)
                return game_path
            else:
                log.debug("Path from appmanifest not valid: %s", game_path)

    return None


def _parse_appmanifest_installdir(manifest_path: Path) -> str | None:
    """Extract the ``installdir`` value from a Steam appmanifest .acf file."""
    try:
        text = manifest_path.read_text(encoding="utf-8")
        match = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
        if match:
            return match.group(1)
    except OSError as exc:
        log.debug("Error reading appmanifest: %s", exc)
    return None


def _find_all_steam_libraries() -> list[Path]:
    """Find all Steam library folders from every available source.

    Checks:
    1. Registry for Steam install path
    2. libraryfolders.vdf (both old and new formats)
    3. config/config.vdf (BaseInstallFolder entries)
    4. Common paths on all drives
    """
    steam_roots: list[Path] = []

    # From registry
    steam_roots.extend(_get_steam_path_from_registry())

    # From common locations on all drives
    drives = _get_windows_drives()
    for drive in drives:
        steam_roots.extend([
            drive / "Program Files (x86)" / "Steam",
            drive / "Program Files" / "Steam",
            drive / "Steam",
            drive / "SteamLibrary",
            drive / "Programs" / "Steam",
        ])

    # Deduplicate steam roots
    seen_roots: set[str] = set()
    unique_roots: list[Path] = []
    for sr in steam_roots:
        key = str(sr).lower()
        if key not in seen_roots:
            seen_roots.add(key)
            unique_roots.append(sr)

    # Parse libraryfolders.vdf and config.vdf from each Steam root
    libraries: list[Path] = []
    seen_libs: set[str] = set()

    def _add_lib(lib_path: Path) -> None:
        key = str(lib_path).lower()
        if key not in seen_libs:
            seen_libs.add(key)
            libraries.append(lib_path)

    for root in unique_roots:
        if not root.is_dir():
            continue
        # The Steam root itself is a library
        _add_lib(root)

        # Parse libraryfolders.vdf
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.exists():
            vdf = root / "SteamApps" / "libraryfolders.vdf"
        for lib in _parse_steam_vdf(vdf):
            _add_lib(lib)

        # Parse config/config.vdf for BaseInstallFolder entries
        config_vdf = root / "config" / "config.vdf"
        for lib in _parse_config_vdf(config_vdf):
            _add_lib(lib)

    return libraries


def _get_steam_path_from_registry() -> list[Path]:
    """Read Steam's install path from the Windows registry."""
    paths: list[Path] = []
    try:
        import winreg
        # HKLM (64-bit OS, Steam is 32-bit)
        for subkey in [r"SOFTWARE\WOW6432Node\Valve\Steam", r"SOFTWARE\Valve\Steam"]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "InstallPath")
                    if value:
                        paths.append(Path(value))
                        log.debug("Registry Steam path (HKLM): %s", value)
            except (FileNotFoundError, OSError):
                continue
        # HKCU (per-user, uses forward slashes)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
                if value:
                    # SteamPath uses forward slashes
                    paths.append(Path(value.replace("/", "\\")))
                    log.debug("Registry Steam path (HKCU): %s", value)
        except (FileNotFoundError, OSError):
            pass
    except ImportError:
        pass
    return paths


def _parse_steam_vdf(vdf_path: Path) -> list[Path]:
    """Parse a Steam libraryfolders.vdf file for library paths.

    Handles both old format (``"1" "D:\\Games\\Steam"``) and new format
    (``"path" "D:\\Games\\Steam"`` inside numbered blocks).
    """
    if not vdf_path.exists():
        return []
    try:
        text = vdf_path.read_text(encoding="utf-8")
    except OSError:
        return []

    paths: list[Path] = []

    # New format: "path" "C:\\..."
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\")
        paths.append(Path(raw))

    # Old format: "1" "D:\\Games\\Steam"  (numeric key directly maps to path)
    # Only if new format found nothing
    if not paths:
        for match in re.finditer(r'^\s*"\d+"\s+"([A-Za-z]:\\[^"]+)"', text, re.MULTILINE):
            raw = match.group(1).replace("\\\\", "\\")
            paths.append(Path(raw))

    if paths:
        log.debug("Parsed %d library path(s) from %s", len(paths), vdf_path)
    return paths


def _parse_config_vdf(config_path: Path) -> list[Path]:
    """Parse Steam's config/config.vdf for BaseInstallFolder entries."""
    if not config_path.exists():
        return []
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []

    paths: list[Path] = []
    for match in re.finditer(r'"BaseInstallFolder_\d+"\s+"([^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\")
        paths.append(Path(raw))

    if paths:
        log.debug("Parsed %d library path(s) from config.vdf", len(paths))
    return paths


# ---------------------------------------------------------------------------
# Registry detection
# ---------------------------------------------------------------------------

def _check_registry() -> Path | None:
    """Check Windows Registry for GTA V install path (Rockstar keys)."""
    try:
        import winreg
    except ImportError:
        return None

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Rockstar Games\Grand Theft Auto V", "InstallFolder"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Rockstar Games\Grand Theft Auto V", "InstallFolderSteam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Rockstar Games\GTAV", "InstallFolderEpic"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Rockstar Games\GTAV", "InstallFolderXboxPc"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Rockstar Games\Grand Theft Auto V", "InstallFolder"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Rockstar Games\Grand Theft Auto V", "InstallFolderSteam"),
        # Rockstar Launcher uninstall GUID
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{5EFC6C07-6B87-43FC-9524-F9E967241741}", "InstallLocation"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{5EFC6C07-6B87-43FC-9524-F9E967241741}", "InstallLocation"),
    ]
    for hive, subkey, value_name in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                if value:
                    p = Path(value)
                    if _validate_gta_path(p):
                        log.debug("Registry hit [%s\\%s]: %s", subkey, value_name, p)
                        return p
        except (FileNotFoundError, OSError):
            continue
    return None


def _check_steam_uninstall_registry() -> Path | None:
    """Check Steam's per-game uninstall registry entries."""
    try:
        import winreg
    except ImportError:
        return None

    for appid in _GTA_APPIDS:
        subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {appid}"
        for hive_path in [r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                          r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"]:
            full_key = rf"{hive_path}\Steam App {appid}"
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, full_key) as key:
                    value, _ = winreg.QueryValueEx(key, "InstallLocation")
                    if value:
                        p = Path(value)
                        if _validate_gta_path(p):
                            log.debug("Steam uninstall registry hit (App %s): %s", appid, p)
                            return p
            except (FileNotFoundError, OSError):
                continue
    return None


# ---------------------------------------------------------------------------
# Epic Games detection
# ---------------------------------------------------------------------------

def _find_epic_install_windows() -> Path | None:
    """Find GTA V installed via Epic Games by reading manifest files."""
    manifests_dir = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    )

    if manifests_dir.is_dir():
        try:
            for manifest in manifests_dir.glob("*.item"):
                try:
                    text = manifest.read_text(encoding="utf-8")
                    if "GTA" not in text.upper() and "9d2d0eb64d5c44529cece33fe2a46482" not in text:
                        continue
                    match = re.search(r'"InstallLocation"\s*:\s*"([^"]+)"', text)
                    if match:
                        p = Path(match.group(1).replace("\\\\", "\\"))
                        if _validate_gta_path(p):
                            log.info("Found GTA V via Epic manifest: %s", p)
                            return p
                except OSError:
                    continue
        except OSError:
            pass

    # Fallback: common Epic paths
    for drive in _get_windows_drives():
        for folder in ["GTAV", "Grand Theft Auto V", "Grand Theft Auto V Enhanced"]:
            p = drive / "Epic Games" / folder
            if _validate_gta_path(p):
                log.debug("Found GTA V at Epic fallback path: %s", p)
                return p

    return None


# ---------------------------------------------------------------------------
# Rockstar Games Launcher detection
# ---------------------------------------------------------------------------

def _find_rockstar_launcher_windows() -> Path | None:
    """Find GTA V installed via Rockstar Games Launcher."""
    # Check settings_user.dat for paths
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        settings_file = Path(local_appdata) / "Rockstar Games" / "Launcher" / "settings_user.dat"
        if settings_file.exists():
            try:
                data = settings_file.read_bytes()
                text = data.decode("utf-8", errors="ignore")
                for match in re.finditer(r'([A-Z]:\\[^\x00]+?Grand Theft Auto V[^\x00]*?)(?=\x00|")', text):
                    p = Path(match.group(1).rstrip("\\/"))
                    if _validate_gta_path(p):
                        log.info("Found GTA V via Rockstar Launcher settings: %s", p)
                        return p
            except OSError:
                pass

    # Registry keys handled by _check_registry() already
    return None


# ---------------------------------------------------------------------------
# Hardcoded paths and deep scan
# ---------------------------------------------------------------------------

def _check_hardcoded_paths(drives: list[Path]) -> Path | None:
    """Check common GTA V install locations on all drives."""
    for drive in drives:
        candidates = [
            drive / "Program Files" / "Rockstar Games" / "Grand Theft Auto V",
            drive / "Program Files" / "Rockstar Games" / "Grand Theft Auto V Enhanced",
            drive / "Program Files (x86)" / "Rockstar Games" / "Grand Theft Auto V",
            drive / "Games" / "Grand Theft Auto V",
            drive / "Games" / "GTA V",
            drive / "Games" / "GTAV",
            drive / "Games" / "Rockstar Games" / "Grand Theft Auto V",
            drive / "Rockstar Games" / "Grand Theft Auto V",
            drive / "Grand Theft Auto V",
            drive / "GTAV",
        ]
        for p in candidates:
            if _validate_gta_path(p):
                log.debug("Found GTA V at hardcoded path: %s", p)
                return p
    return None


def _deep_scan_windows(drives: list[Path]) -> Path | None:
    """Brute-force scan drives for GTA V installation.

    Strategy 1: Find any steamapps/ directories and check for appmanifest files.
    Strategy 2: Look for GTA V folder names up to 3 levels deep.
    """
    for drive in drives:
        try:
            for depth1 in drive.iterdir():
                if not depth1.is_dir():
                    continue

                # Check for steamapps at this level
                result = _check_steamapps_dir(depth1 / "steamapps")
                if result:
                    return result
                result = _check_steamapps_dir(depth1 / "SteamApps")
                if result:
                    return result

                # If this IS steamapps, check it
                if depth1.name.lower() == "steamapps":
                    result = _check_steamapps_dir(depth1)
                    if result:
                        return result

                # Check folder name match
                if depth1.name.lower() in _GTA_FOLDER_NAMES and _validate_gta_path(depth1):
                    return depth1

                # Go one level deeper
                try:
                    for depth2 in depth1.iterdir():
                        if not depth2.is_dir():
                            continue

                        result = _check_steamapps_dir(depth2 / "steamapps")
                        if result:
                            return result
                        result = _check_steamapps_dir(depth2 / "SteamApps")
                        if result:
                            return result

                        if depth2.name.lower() == "steamapps":
                            result = _check_steamapps_dir(depth2)
                            if result:
                                return result

                        if depth2.name.lower() in _GTA_FOLDER_NAMES and _validate_gta_path(depth2):
                            return depth2

                        # Third level
                        try:
                            for depth3 in depth2.iterdir():
                                if not depth3.is_dir():
                                    continue
                                if depth3.name.lower() in _GTA_FOLDER_NAMES and _validate_gta_path(depth3):
                                    return depth3
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            continue

    return None


def _check_steamapps_dir(steamapps: Path) -> Path | None:
    """Check a steamapps directory for GTA V appmanifest files."""
    if not steamapps.is_dir():
        return None
    for appid in _GTA_APPIDS:
        manifest = steamapps / f"appmanifest_{appid}.acf"
        if manifest.exists():
            installdir = _parse_appmanifest_installdir(manifest)
            if installdir:
                game_path = steamapps / "common" / installdir
                if _validate_gta_path(game_path):
                    log.debug("Deep scan: found via appmanifest at %s", game_path)
                    return game_path
    return None


# ---------------------------------------------------------------------------
# Linux detection
# ---------------------------------------------------------------------------

def _detect_linux() -> Path | None:
    home = Path.home()

    # Collect all possible Steam root directories
    steam_roots = [
        home / ".steam" / "steam",
        home / ".steam" / "root",
        home / ".local" / "share" / "Steam",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".steam" / "steam",  # Flatpak
    ]

    # Parse library folders from each root
    libraries: list[Path] = []
    seen: set[str] = set()
    for root in steam_roots:
        if not root.is_dir():
            continue
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            libraries.append(root)
        for vdf_name in ["steamapps/libraryfolders.vdf", "SteamApps/libraryfolders.vdf"]:
            vdf = root / vdf_name
            for lib in _parse_steam_vdf(vdf):
                lkey = str(lib).lower()
                if lkey not in seen:
                    seen.add(lkey)
                    libraries.append(lib)

    # Check appmanifests in each library (same method as Windows)
    for lib in libraries:
        for sa_name in ["steamapps", "SteamApps"]:
            steamapps = lib / sa_name
            if not steamapps.is_dir():
                continue
            for appid in _GTA_APPIDS:
                manifest = steamapps / f"appmanifest_{appid}.acf"
                if manifest.exists():
                    installdir = _parse_appmanifest_installdir(manifest)
                    if installdir:
                        game_path = steamapps / "common" / installdir
                        if _validate_gta_path(game_path):
                            log.info("Found GTA V via Linux Steam appmanifest: %s", game_path)
                            return game_path

    # Fallback: check common paths directly
    for lib in libraries:
        for sa_name in ["steamapps", "SteamApps"]:
            for folder in ["Grand Theft Auto V", "Grand Theft Auto V Enhanced"]:
                p = lib / sa_name / "common" / folder
                if _validate_gta_path(p):
                    return p

    return None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _get_windows_drives() -> list[Path]:
    """Get all available drive letters on Windows."""
    drives: list[Path] = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if drive.exists():
            drives.append(drive)
    return drives


def validate_gta_path(path: str | Path) -> Path:
    """Validate a user-provided or detected GTA V path. Raises ValueError if invalid."""
    p = Path(path)
    if not _validate_gta_path(p):
        log.error("Invalid GTA V path: %s", p)
        raise ValueError(
            f"'{p}' does not appear to be a valid GTA V installation. "
            "Expected to find GTA5.exe or update/update.rpf."
        )
    log.info("Validated GTA V path: %s", p)
    save_cached_path(p)
    return p


def _validate_gta_path(path: Path) -> bool:
    """Check if a path looks like a GTA V installation."""
    if not path.is_dir():
        return False
    has_exe = (
        (path / "GTA5.exe").exists()
        or (path / "GTA5_Enhanced.exe").exists()
        or (path / "PlayGTAV.exe").exists()
    )
    has_update = (path / "update" / "update.rpf").exists()
    return has_exe or has_update

"""First-class native Story axle controller builds for the SDK.

The ASI is generic per GTA edition.  Vehicle packs customize its schema-2
``runtime.json`` and provide one or more model sidecars; they do not need a
vehicle-specific native binary.  Local outputs are deliberately candidate
builds and never impersonate an in-game acceptance receipt.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    retarget_axle_configuration,
)
from allin1_sdk.axle_runtime_bundler import (
    STORY_RUNTIME_REQUIRED_EXPORTS,
    TARGET_STORY_ENHANCED,
    TARGET_STORY_LEGACY,
    VehicleAxleBuildInput,
    inspect_story_runtime_binary,
    story_native_runtime_configuration,
)
from allin1_sdk.paths import gta_root_containing, project_root
from allin1_sdk.processes import run_hidden


STORY_TARGETS = (TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED)
_TARGET_LABELS = {
    TARGET_STORY_LEGACY: "Legacy",
    TARGET_STORY_ENHANCED: "Enhanced",
}
_BUILD_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:+-]{0,127}$")
_VERSION = re.compile(r"project\(VehicleWorkbenchAxles\s+VERSION\s+(\d+\.\d+\.\d+)")
_RESERVED_WINDOWS_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
})
_FORBIDDEN_DYNAMIC_CRT = frozenset({
    "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "ucrtbase.dll",
})
_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_CONFIGURATIONS = 1024
_COMMAND_TIMEOUT_SECONDS = 15 * 60
_DEFAULT_CONFIGURATION_DIRECTORY = "VehicleWorkbenchAxles/configs"
_DEFAULT_LOG_FILE = "VehicleWorkbenchAxles/logs/VehicleWorkbenchAxles.log"
_TRANSIT_EXPANSION_MODEL = "metrobusxl2"
_TRANSIT_EXPANSION_CONFIGURATION_DIRECTORY = (
    "scripts/TransitExpansionPack/VehicleSettings"
)
_TRANSIT_EXPANSION_LOG_FILE = "scripts/TransitExpansionPack/Axles.log"


class StoryAxleRuntimeBuildError(RuntimeError):
    """Raised when a native candidate cannot be built safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_runtime_relative_path(value: str, label: str) -> str:
    """Mirror the native schema-2 Windows path contract."""

    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if value != value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} contains unsafe whitespace or control characters")
    text = value.replace("\\", "/")
    if not text or text.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", text):
        raise ValueError(f"{label} must be a non-empty path relative to the GTA root")
    parts = text.split("/")
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or any(character in part for character in '<>:"|?*')
        ):
            raise ValueError(f"{label} contains an unsafe Windows path component")
        if part.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
            raise ValueError(f"{label} contains a reserved Windows device name")
    return "/".join(parts)


def portable_runtime_path(
    selection: str | Path,
    gta_roots: Sequence[Path],
    label: str,
) -> str:
    """Convert one selected path below a declared GTA root to schema-2 form.

    File dialogs return machine-specific absolute paths.  Runtime settings are
    portable package data, so an absolute path is accepted only long enough to
    prove that it belongs to one of the explicitly supplied GTA installations.
    The absolute prefix is never returned or serialized.
    """

    raw_selection = os.fspath(selection)
    raw_roots = tuple(os.fspath(root) for root in gta_roots)
    if not raw_roots:
        raise ValueError(
            f"Choose a GTA installation before browsing for {label.casefold()}"
        )

    # Keep Windows selections portable even when backend tests execute on a
    # non-Windows host. ``Path`` treats ``C:\\...`` as relative on POSIX.
    if re.match(r"^[A-Za-z]:[\\/]", raw_selection) or raw_selection.startswith(
        ("\\\\", "//")
    ):
        selected = ntpath.normpath(raw_selection)
        for raw_root in raw_roots:
            root = ntpath.normpath(raw_root)
            try:
                relative = ntpath.relpath(selected, root)
            except ValueError:
                continue
            if relative == ".":
                raise ValueError(f"{label} must identify a path below the GTA root")
            if relative != ".." and not relative.startswith(f"..{ntpath.sep}"):
                return validate_runtime_relative_path(
                    relative.replace("\\", "/"), label,
                )
    else:
        selected = Path(raw_selection).expanduser().resolve(strict=False)
        for raw_root in raw_roots:
            root = Path(raw_root).expanduser().resolve(strict=False)
            try:
                relative = selected.relative_to(root)
            except ValueError:
                continue
            if relative == Path("."):
                raise ValueError(f"{label} must identify a path below the GTA root")
            return validate_runtime_relative_path(relative.as_posix(), label)
    raise ValueError(
        f"{label} must be inside one of the configured GTA installations"
    )


def _selected_runtime_path(
    value: str, gta_roots: Sequence[Path], label: str,
) -> str:
    """Validate a portable value or relativize a selected absolute value."""

    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    is_absolute = (
        Path(value).is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", value) is not None
        or value.startswith(("\\\\", "//"))
    )
    if is_absolute:
        return portable_runtime_path(value, gta_roots, label)
    return validate_runtime_relative_path(value, label)


@dataclass(frozen=True)
class StoryAxleRuntimeSettings:
    enabled: bool = True
    discovery_interval_ms: int = 250
    recovery_interval_ms: int = 2000
    restore_on_unload: bool = True
    configuration_directory: str = _DEFAULT_CONFIGURATION_DIRECTORY
    log_file: str = _DEFAULT_LOG_FILE

    def validate(self) -> "StoryAxleRuntimeSettings":
        if not isinstance(self.enabled, bool):
            raise ValueError("Enabled must be true or false")
        if not isinstance(self.restore_on_unload, bool):
            raise ValueError("Restore on unload must be true or false")
        if isinstance(self.discovery_interval_ms, bool) or not isinstance(
            self.discovery_interval_ms, int
        ) or not 100 <= self.discovery_interval_ms <= 10000:
            raise ValueError("Discovery interval must be between 100 and 10000 ms")
        if isinstance(self.recovery_interval_ms, bool) or not isinstance(
            self.recovery_interval_ms, int
        ) or not self.discovery_interval_ms <= self.recovery_interval_ms <= 60000:
            raise ValueError(
                "Recovery interval must be at least the discovery interval and no more than 60000 ms"
            )
        configuration = validate_runtime_relative_path(
            self.configuration_directory, "Configuration directory",
        )
        log_file = validate_runtime_relative_path(self.log_file, "Log file")
        if configuration.casefold() == log_file.casefold():
            raise ValueError("Configuration directory and log file cannot be the same path")
        return replace(
            self,
            configuration_directory=configuration,
            log_file=log_file,
        )

    def normalize_selected_paths(
        self, gta_roots: Sequence[Path],
    ) -> "StoryAxleRuntimeSettings":
        """Return settings containing GTA-root-relative portable paths only."""

        return replace(
            self,
            configuration_directory=_selected_runtime_path(
                self.configuration_directory, gta_roots,
                "Configuration directory",
            ),
            log_file=_selected_runtime_path(
                self.log_file, gta_roots, "Log file",
            ),
        ).validate()

    def to_runtime_json(self) -> dict[str, Any]:
        settings = self.validate()
        return {
            "schemaVersion": 2,
            "enabled": settings.enabled,
            "discoveryIntervalMs": settings.discovery_interval_ms,
            "recoveryIntervalMs": settings.recovery_interval_ms,
            "restoreOnUnload": settings.restore_on_unload,
            "configurationDirectory": settings.configuration_directory,
            "logFile": settings.log_file,
        }


def default_story_axle_runtime_settings(
    vehicle_model: str | None = None,
) -> StoryAxleRuntimeSettings:
    """Return portable project defaults for one selected vehicle model."""

    model = str(vehicle_model or "").strip().casefold()
    if model == _TRANSIT_EXPANSION_MODEL:
        return StoryAxleRuntimeSettings(
            configuration_directory=(
                _TRANSIT_EXPANSION_CONFIGURATION_DIRECTORY
            ),
            log_file=_TRANSIT_EXPANSION_LOG_FILE,
        )
    return StoryAxleRuntimeSettings()


@dataclass(frozen=True)
class NativeToolchainCheck:
    """One visible, actionable native compiler readiness result."""

    key: str
    label: str
    ready: bool
    detected: str
    requirement: str
    guidance: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NativeAxleToolchainReport:
    ready: bool
    platform: str
    source_root: Path
    cmake_path: Path | None
    cmake_version: str | None
    ctest_path: Path | None
    visual_studio_path: Path | None
    cmake_generator: str | None
    problems: tuple[str, ...]
    ctest_version: str | None = None
    visual_studio_version: str | None = None
    visual_studio_name: str | None = None
    vc_workload_ready: bool = False
    cl_path: Path | None = None
    cl_version: str | None = None
    msvc_toolset_version: str | None = None
    windows_sdk_version: str | None = None
    host_architecture: str | None = None
    target_architecture: str | None = None
    probe_succeeded: bool = False
    probe_detail: str = "Not run"
    checks: tuple[NativeToolchainCheck, ...] = ()
    guidance: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "source_root", "cmake_path", "ctest_path", "visual_studio_path",
            "cl_path",
        ):
            value = payload[key]
            payload[key] = str(value) if value is not None else None
        return payload


def _runtime_source_root() -> Path:
    relative = Path("runtime") / "VehicleWorkbenchAxles"
    checkout_or_app = project_root() / relative
    candidates = (
        checkout_or_app,
        Path(sys.prefix).resolve() / "share" / "allin1-sdk" / relative,
    )
    for candidate in candidates:
        if candidate.is_dir() and not candidate.is_symlink():
            return candidate.resolve()
    # Preserve the checkout/app path in diagnostics when neither distribution
    # layout is complete; inspect_native_axle_toolchain will report its missing
    # source files rather than concealing the packaging fault.
    return checkout_or_app


_VC_WORKLOAD = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
_MINIMUM_CMAKE = (3, 20, 0)
_MINIMUM_MSVC_TOOLSET = (14, 20, 0)
_MAXIMUM_MSVC_TOOLSET = (15, 0, 0)


def _version_tuple(value: str | None, *, width: int = 3) -> tuple[int, ...]:
    if not value:
        return ()
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", value.strip())
    if match is None:
        return ()
    values = [int(match.group(index) or 0) for index in range(1, 4)]
    return tuple(values[:width])


def _executable_version(
    executable: Path,
    product: str,
) -> tuple[str | None, str | None]:
    try:
        completed = run_hidden(
            [executable, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{product} version probe failed: {exc}"
    combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    match = re.search(
        rf"{re.escape(product)}\s+version\s+(\d+\.\d+(?:\.\d+)?)",
        combined, re.IGNORECASE,
    )
    if completed.returncode != 0 or match is None:
        return None, f"{product} version could not be verified"
    return match.group(1), None


def _vswhere_executable() -> tuple[Path | None, str | None]:
    if os.name != "nt":
        return None, "Native Story ASI builds require Windows"
    program_files = os.environ.get("ProgramFiles(x86)", "").strip()
    if not program_files:
        return None, "ProgramFiles(x86) is unavailable"
    path = (
        Path(program_files) / "Microsoft Visual Studio" / "Installer" /
        "vswhere.exe"
    )
    if not path.is_file():
        return None, "Visual Studio Installer discovery (vswhere.exe) was not found"
    return path.resolve(), None


def _vswhere_property(
    executable: Path,
    property_name: str,
    *,
    require_vc: bool,
) -> tuple[str | None, str | None]:
    command: list[str | Path] = [
        executable, "-latest", "-products", "*", "-property", property_name,
    ]
    if require_vc:
        command[4:4] = ["-requires", _VC_WORKLOAD]
    try:
        completed = run_hidden(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Visual Studio discovery failed: {exc}"
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if completed.returncode != 0 or len(lines) != 1:
        return None, None
    return lines[0], None


def _visual_studio_details(
) -> tuple[Path | None, str | None, str | None, bool, str | None]:
    vswhere, problem = _vswhere_executable()
    if vswhere is None:
        return None, None, None, False, problem

    workload_path, query_problem = _vswhere_property(
        vswhere, "installationPath", require_vc=True,
    )
    if query_problem:
        return None, None, None, False, query_problem
    generic_path, query_problem = _vswhere_property(
        vswhere, "installationPath", require_vc=False,
    )
    if query_problem:
        return None, None, None, False, query_problem
    raw_path = workload_path or generic_path
    if raw_path is None:
        return None, None, None, False, "A supported Visual Studio installation is not installed"
    path = Path(raw_path)
    if not path.is_dir():
        return None, None, None, False, "The detected Visual Studio installation is unavailable"

    has_workload = workload_path is not None
    version, _ = _vswhere_property(
        vswhere, "installationVersion", require_vc=has_workload,
    )
    name, _ = _vswhere_property(
        vswhere, "displayName", require_vc=has_workload,
    )
    return path.resolve(), version, name, has_workload, None


def _visual_studio_installation() -> tuple[Path | None, str | None]:
    """Compatibility wrapper used by callers and focused discovery tests."""

    path, _version, _name, workload, problem = _visual_studio_details()
    if problem:
        return None, problem
    if path is None:
        return None, "A supported Visual Studio installation is not installed"
    if not workload:
        return path, "The Visual Studio VC x86/x64 build-tools workload is missing"
    return path, None


def _visual_studio_cmake_generator(installation: Path) -> tuple[str | None, str | None]:
    """Map supported Visual Studio product years or major folders to CMake."""

    match = re.search(r"[\\/](16|17|18|2019|2022|2026)[\\/]", str(installation))
    if match is None:
        return None, "Visual Studio product version could not be identified"
    generators = {
        "16": "Visual Studio 16 2019",
        "17": "Visual Studio 17 2022",
        "18": "Visual Studio 18 2026",
        "2019": "Visual Studio 16 2019",
        "2022": "Visual Studio 17 2022",
        "2026": "Visual Studio 18 2026",
    }
    return generators[match.group(1)], None


def _msvc_toolset(
    installation: Path,
) -> tuple[Path | None, str | None, Path | None, str | None]:
    root = installation / "VC" / "Tools" / "MSVC"
    version_file = (
        installation / "VC" / "Auxiliary" / "Build" /
        "Microsoft.VCToolsVersion.default.txt"
    )
    versions: list[str] = []
    if version_file.is_file() and not version_file.is_symlink():
        try:
            value = version_file.read_text("utf-8-sig").strip()
        except OSError:
            value = ""
        if _version_tuple(value):
            versions.append(value)
    if root.is_dir() and not root.is_symlink():
        versions.extend(
            path.name for path in root.iterdir()
            if path.is_dir() and not path.is_symlink() and _version_tuple(path.name)
        )
    versions = sorted(set(versions), key=_version_tuple, reverse=True)
    for version in versions:
        cl = root / version / "bin" / "Hostx64" / "x64" / "cl.exe"
        if cl.is_file() and not cl.is_symlink():
            return root / version, version, cl.resolve(), None
    if not root.is_dir():
        return None, None, None, "The MSVC toolset directory is missing"
    return None, None, None, "Host x64 / Target x64 cl.exe was not found"


def _compiler_banner(cl_path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        completed = run_hidden(
            [cl_path, "/Bv"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, None, f"cl.exe /Bv failed: {exc}"
    combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    match = re.search(
        r"Compiler Version\s+(\d+\.\d+(?:\.\d+)*)\s+for\s+([A-Za-z0-9_]+)",
        combined, re.IGNORECASE,
    )
    if match is None:
        return None, None, "cl.exe /Bv did not report a compiler version and target"
    return match.group(1), match.group(2).casefold(), None


def _windows_sdk() -> tuple[str | None, Path | None, str | None]:
    roots: list[Path] = []
    configured = os.environ.get("WindowsSdkDir", "").strip()
    if configured:
        roots.append(Path(configured))
    program_files = os.environ.get("ProgramFiles(x86)", "").strip()
    if program_files:
        roots.append(Path(program_files) / "Windows Kits" / "10")
    for root in dict.fromkeys(path.resolve(strict=False) for path in roots):
        include = root / "Include"
        if not include.is_dir() or include.is_symlink():
            continue
        versions = sorted(
            (
                child.name for child in include.iterdir()
                if child.is_dir() and not child.is_symlink()
                and _version_tuple(child.name, width=4)
                and (child / "um" / "Windows.h").is_file()
                and (child / "ucrt" / "stdlib.h").is_file()
            ),
            key=lambda value: _version_tuple(value, width=4), reverse=True,
        )
        if versions:
            return versions[0], root, None
    return None, None, "A complete Windows 10/11 SDK with UM and UCRT headers was not found"


def _run_cpp17_static_probe(
    *,
    cmake: Path,
    generator: str,
) -> tuple[bool, str]:
    """Compile and inspect a disposable x64 C++17 /MT executable."""

    try:
        with tempfile.TemporaryDirectory(prefix="allin1-axle-preflight-") as raw:
            root = Path(raw)
            source = root / "source"
            build = root / "build"
            source.mkdir()
            (source / "CMakeLists.txt").write_text(
                "\n".join((
                    "cmake_minimum_required(VERSION 3.20)",
                    "project(ALLIN1AxlePreflight LANGUAGES CXX)",
                    "if(NOT MSVC)",
                    '  message(FATAL_ERROR "MSVC is required")',
                    "endif()",
                    'add_executable(allin1_axle_probe "main.cpp")',
                    "target_compile_features(allin1_axle_probe PRIVATE cxx_std_17)",
                    "set_property(TARGET allin1_axle_probe PROPERTY ",
                    '  MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")',
                    "",
                )),
                encoding="utf-8",
            )
            (source / "main.cpp").write_text(
                "#include <type_traits>\n"
                "#if defined(_MSVC_LANG)\n"
                "static_assert(_MSVC_LANG >= 201703L);\n"
                "#else\n"
                "static_assert(__cplusplus >= 201703L);\n"
                "#endif\n"
                "static_assert(sizeof(void*) == 8);\n"
                "int main() { return std::is_same_v<int, int> ? 0 : 1; }\n",
                encoding="utf-8",
            )
            commands = (
                [
                    cmake, "-S", source, "-B", build, "-G", generator,
                    "-A", "x64", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
                ],
                [
                    cmake, "--build", build, "--config", "Release", "--target",
                    "allin1_axle_probe",
                ],
            )
            for index, command in enumerate(commands):
                completed = run_hidden(
                    command, cwd=source, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=120,
                    check=False,
                )
                if completed.returncode != 0:
                    phase = "configure" if index == 0 else "compile/link"
                    detail = (completed.stderr or completed.stdout or "").strip()
                    return False, f"C++17 probe {phase} failed: {detail[-800:]}"
            executable = build / "Release" / "allin1_axle_probe.exe"
            if not executable.is_file():
                return False, "C++17 probe did not produce an x64 executable"
            imports, _signed = _pe_imports_and_signature(executable)
            forbidden = sorted(set(imports) & _FORBIDDEN_DYNAMIC_CRT)
            if forbidden:
                return False, (
                    "Static-runtime probe imported dynamic CRT files: "
                    + ", ".join(forbidden)
                )
            return True, "C++17 x64 compile/link passed with the static MSVC runtime"
    except (OSError, subprocess.TimeoutExpired, StoryAxleRuntimeBuildError) as exc:
        return False, f"C++17/static-runtime probe failed: {exc}"


def inspect_native_axle_toolchain(
    *, source_root: Path | None = None,
) -> NativeAxleToolchainReport:
    source = (source_root or _runtime_source_root()).expanduser().resolve(strict=False)
    checks: list[NativeToolchainCheck] = []

    def record(
        key: str,
        label: str,
        ready: bool,
        detected: str,
        requirement: str,
        guidance: str = "",
        detail: str = "",
    ) -> None:
        checks.append(NativeToolchainCheck(
            key=key,
            label=label,
            ready=bool(ready),
            detected=detected,
            requirement=requirement,
            guidance=guidance if not ready else "",
            detail=detail,
        ))

    required = (
        source / "CMakeLists.txt",
        source / "src" / "runtime.cpp",
        source / "src" / "asi_entry.cpp",
        source / "tools" / "config_validator.cpp",
    )
    missing = [path.name for path in required if not path.is_file() or path.is_symlink()]
    record(
        "runtime_source",
        "Controller source",
        not missing,
        str(source) if not missing else "Missing: " + ", ".join(missing),
        "Complete packaged VehicleWorkbenchAxles source tree",
        "Repair or reinstall the ALLIN1 SDK so its native runtime sources are restored.",
    )

    cmake_text = shutil.which("cmake")
    cmake = Path(cmake_text).resolve() if cmake_text else None
    ctest_text = shutil.which("ctest")
    if ctest_text is None and cmake is not None:
        adjacent = cmake.with_name("ctest.exe")
        ctest_text = str(adjacent) if adjacent.is_file() else None
    ctest = Path(ctest_text).resolve() if ctest_text else None
    cmake_version: str | None = None
    cmake_problem: str | None = None
    if cmake is not None:
        cmake_version, cmake_problem = _executable_version(cmake, "cmake")
    cmake_ready = (
        cmake is not None
        and cmake_problem is None
        and _version_tuple(cmake_version) >= _MINIMUM_CMAKE
    )
    record(
        "cmake",
        "CMake",
        cmake_ready,
        (
            f"{cmake_version} — {cmake}" if cmake is not None and cmake_version
            else cmake_problem or "Not found on PATH"
        ),
        "CMake 3.20 or newer",
        "Install current CMake for Windows and enable its Add to PATH option.",
    )

    ctest_version: str | None = None
    ctest_problem: str | None = None
    if ctest is not None:
        ctest_version, ctest_problem = _executable_version(ctest, "ctest")
    ctest_ready = (
        ctest is not None
        and ctest_problem is None
        and ctest_version is not None
        and cmake_version is not None
        and _version_tuple(ctest_version) == _version_tuple(cmake_version)
    )
    ctest_detected = ctest_problem or "Not found beside CMake or on PATH"
    if ctest is not None and ctest_version:
        ctest_detected = f"{ctest_version} — {ctest}"
        if cmake_version and not ctest_ready:
            ctest_detected += f" (does not match CMake {cmake_version})"
    record(
        "ctest",
        "CTest",
        ctest_ready,
        ctest_detected,
        "CTest from the same versioned CMake toolchain",
        "Repair CMake or put its bin directory first on PATH so cmake and ctest match.",
    )

    (
        visual_studio,
        visual_studio_version,
        visual_studio_name,
        vc_workload_ready,
        visual_studio_problem,
    ) = _visual_studio_details()
    cmake_generator: str | None = None
    generator_problem: str | None = None
    if visual_studio is not None:
        cmake_generator, generator_problem = _visual_studio_cmake_generator(
            visual_studio,
        )
        if cmake_generator is None and visual_studio_version:
            major = _version_tuple(visual_studio_version)[0]
            cmake_generator = {
                16: "Visual Studio 16 2019",
                17: "Visual Studio 17 2022",
                18: "Visual Studio 18 2026",
            }.get(major)
            generator_problem = (
                None if cmake_generator else
                f"Visual Studio {visual_studio_version} is not supported"
            )
    vs_major = (
        _version_tuple(visual_studio_version)[0]
        if _version_tuple(visual_studio_version) else None
    )
    visual_studio_ready = (
        visual_studio is not None
        and visual_studio_problem is None
        and cmake_generator is not None
        and vs_major in {16, 17, 18}
    )
    record(
        "visual_studio",
        "Visual Studio",
        visual_studio_ready,
        (
            f"{visual_studio_name or 'Visual Studio'} {visual_studio_version or ''} — "
            f"{visual_studio}" if visual_studio is not None
            else visual_studio_problem or "Not found"
        ),
        "Supported Visual Studio 2019 or newer installation",
        "Install Visual Studio Build Tools 2022 or newer through the Visual Studio Installer.",
        generator_problem or "",
    )
    record(
        "vc_workload",
        "VC x86/x64 workload",
        visual_studio_ready and vc_workload_ready,
        "Installed" if vc_workload_ready else "Not detected",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        (
            "Open Visual Studio Installer, choose Modify, select Desktop development "
            "with C++, and include MSVC x86/x64 build tools."
        ),
    )

    toolset_root: Path | None = None
    msvc_toolset_version: str | None = None
    cl_path: Path | None = None
    toolset_problem: str | None = None
    if visual_studio is not None and vc_workload_ready:
        (
            toolset_root, msvc_toolset_version, cl_path, toolset_problem,
        ) = _msvc_toolset(visual_studio)
    cl_version: str | None = None
    cl_target: str | None = None
    cl_problem: str | None = None
    if cl_path is not None:
        cl_version, cl_target, cl_problem = _compiler_banner(cl_path)
    cl_ready = cl_path is not None and cl_version is not None and cl_problem is None
    record(
        "compiler",
        "MSVC compiler",
        cl_ready,
        (
            f"cl.exe {cl_version} for {cl_target} — {cl_path}"
            if cl_ready else cl_problem or toolset_problem or "Not found"
        ),
        "An actual cl.exe /Bv result from the selected Visual Studio installation",
        "Repair the Visual C++ workload and its latest x64 compiler tools.",
    )

    toolset_tuple = _version_tuple(msvc_toolset_version)
    toolset_ready = bool(
        toolset_root is not None
        and toolset_tuple >= _MINIMUM_MSVC_TOOLSET
        and toolset_tuple < _MAXIMUM_MSVC_TOOLSET
    )
    record(
        "msvc_toolset",
        "MSVC toolset",
        toolset_ready,
        msvc_toolset_version or toolset_problem or "Not detected",
        "Supported MSVC v142/v143-compatible toolset (14.20 through 14.x)",
        "Install or update the MSVC x64/x86 build tools in Visual Studio Installer.",
    )

    windows_sdk_version, windows_sdk_root, windows_sdk_problem = _windows_sdk()
    windows_sdk_ready = windows_sdk_version is not None and windows_sdk_root is not None
    record(
        "windows_sdk",
        "Windows SDK",
        windows_sdk_ready,
        (
            f"{windows_sdk_version} — {windows_sdk_root}"
            if windows_sdk_ready else windows_sdk_problem or "Not found"
        ),
        "Complete Windows 10/11 SDK with UM and UCRT headers",
        "Add the Windows 10 or Windows 11 SDK from Visual Studio Installer Individual components.",
    )

    host_architecture = (
        "x64" if cl_path is not None
        and "hostx64" in cl_path.as_posix().casefold() else None
    )
    target_architecture = "x64" if cl_target == "x64" else cl_target
    architecture_ready = host_architecture == "x64" and target_architecture == "x64"
    record(
        "architecture",
        "Compiler architecture",
        architecture_ready,
        f"Host {host_architecture or 'unknown'} / Target {target_architecture or 'unknown'}",
        "Host x64 and Target x64",
        "Install the x64 MSVC tools; 32-bit host or target compilers are not supported.",
    )

    prerequisites_ready = all(check.ready for check in checks)
    probe_succeeded = False
    probe_detail = "Not run because prerequisite checks failed"
    if prerequisites_ready and cmake is not None and cmake_generator is not None:
        probe_succeeded, probe_detail = _run_cpp17_static_probe(
            cmake=cmake,
            generator=cmake_generator,
        )
    record(
        "compile_probe",
        "Isolated compile probe",
        probe_succeeded,
        probe_detail,
        "Successful isolated C++17 x64 compile/link using the static MSVC runtime",
        (
            "Review the probe detail, repair the reported compiler/SDK component, "
            "then press Recheck."
        ),
    )

    problems = tuple(
        f"{check.label}: {check.detail or check.detected}"
        for check in checks if not check.ready
    )
    guidance = tuple(dict.fromkeys(
        check.guidance for check in checks if not check.ready and check.guidance
    ))
    return NativeAxleToolchainReport(
        ready=all(check.ready for check in checks),
        platform=os.name,
        source_root=source,
        cmake_path=cmake,
        cmake_version=cmake_version,
        ctest_path=ctest,
        visual_studio_path=visual_studio,
        cmake_generator=cmake_generator,
        problems=problems,
        ctest_version=ctest_version,
        visual_studio_version=visual_studio_version,
        visual_studio_name=visual_studio_name,
        vc_workload_ready=vc_workload_ready,
        cl_path=cl_path,
        cl_version=cl_version,
        msvc_toolset_version=msvc_toolset_version,
        windows_sdk_version=windows_sdk_version,
        host_architecture=host_architecture,
        target_architecture=target_architecture,
        probe_succeeded=probe_succeeded,
        probe_detail=probe_detail,
        checks=tuple(checks),
        guidance=guidance,
    )


@dataclass(frozen=True)
class StoryAxleRuntimeBuildRequest:
    output_directory: Path
    targets: tuple[str, ...]
    configurations: tuple[VehicleAxleBuildInput, ...] = ()
    settings: StoryAxleRuntimeSettings = StoryAxleRuntimeSettings()
    build_id: str = "allin1-sdk-local"
    create_archives: bool = True
    protected_gta_roots: tuple[Path, ...] = ()

    def validate(self) -> "StoryAxleRuntimeBuildRequest":
        targets = tuple(dict.fromkeys(str(value).strip().casefold() for value in self.targets))
        if not targets or any(target not in STORY_TARGETS for target in targets):
            raise ValueError("Select Story Legacy, Story Enhanced, or both")
        if not _BUILD_ID.fullmatch(str(self.build_id).strip()):
            raise ValueError("Build id must use 1-128 safe letters, numbers, dots, colons, pluses, or dashes")
        output = Path(self.output_directory).expanduser().resolve(strict=False)
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Output already exists: {output}")
        game_root = gta_root_containing(
            output, explicit_roots=tuple(self.protected_gta_roots),
        )
        if game_root is not None:
            raise ValueError(f"Native axle builds must be staged outside GTA V: {game_root}")
        if not isinstance(self.settings, StoryAxleRuntimeSettings):
            raise ValueError("Runtime settings must use StoryAxleRuntimeSettings")
        settings = self.settings.normalize_selected_paths(
            self.protected_gta_roots,
        )
        if len(self.configurations) > _MAX_CONFIGURATIONS:
            raise ValueError(
                f"At most {_MAX_CONFIGURATIONS} axle configurations may be built at once"
            )
        ids: set[str] = set()
        hashes: set[str] = set()
        for item in self.configurations:
            if not isinstance(item, VehicleAxleBuildInput):
                raise ValueError("Configurations must use VehicleAxleBuildInput records")
            identifier = item.normalized_configuration_id
            model_hash = item.normalized_model_hash
            if identifier in ids:
                raise ValueError(f"Duplicate axle configuration id: {identifier}")
            if model_hash in hashes:
                raise ValueError(f"Duplicate axle model hash: {model_hash}")
            ids.add(identifier)
            hashes.add(model_hash)
            if item.configuration.export_mode != EXPORT_FIVEM_RUNTIME:
                raise ValueError(
                    f"{item.configuration.vehicle_model} must use Selective runtime behavior"
                )
        # Transit Expansion's reviewed metrobusxl2 layout deliberately keeps
        # its sidecar and log beside the owning script package. Apply that
        # established project convention only while the caller still has both
        # generic defaults; an explicit author choice always wins.
        models = {
            item.configuration.vehicle_model.strip().casefold()
            for item in self.configurations
        }
        if (
            _TRANSIT_EXPANSION_MODEL in models
            and settings.configuration_directory.casefold()
            == _DEFAULT_CONFIGURATION_DIRECTORY.casefold()
            and settings.log_file.casefold() == _DEFAULT_LOG_FILE.casefold()
        ):
            project_defaults = default_story_axle_runtime_settings(
                _TRANSIT_EXPANSION_MODEL,
            )
            settings = replace(
                settings,
                configuration_directory=(
                    project_defaults.configuration_directory
                ),
                log_file=project_defaults.log_file,
            ).validate()
        return replace(
            self,
            output_directory=output,
            targets=targets,
            settings=settings,
            build_id=str(self.build_id).strip(),
        )


@dataclass(frozen=True)
class NativeBuildCommandRecord:
    name: str
    command: tuple[str, ...]
    returncode: int
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StoryAxleRuntimeBuildResult:
    root: Path
    runtime_version: str
    built_targets: tuple[str, ...]
    archives: tuple[Path, ...]
    checksums: Mapping[str, str]
    files: tuple[str, ...]
    manifest: Path
    commands: tuple[NativeBuildCommandRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "build_story_axle_runtime",
            "output": str(self.root),
            "runtime_version": self.runtime_version,
            "built_targets": list(self.built_targets),
            "archives": [str(path) for path in self.archives],
            "checksums": dict(self.checksums),
            "files": list(self.files),
            "manifest": str(self.manifest),
            "candidate_status": {
                "supported": False,
                "game_acceptance": "not-tested",
            },
            "commands": [record.to_dict() for record in self.commands],
        }


ProgressCallback = Callable[[str], None]


def _command_tail(value: str, maximum: int = 8000) -> str:
    text = value[-maximum:]
    return text.replace("\r\n", "\n")


def _redact_local_path_text(
    value: str,
    replacements: Sequence[tuple[Path, str]],
) -> str:
    """Remove developer-machine paths from publishable build evidence."""

    text = str(value)
    for path, token in sorted(
        replacements, key=lambda item: len(str(item[0])), reverse=True,
    ):
        raw = str(path)
        variants = {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                text = re.sub(re.escape(variant), token, text, flags=re.IGNORECASE)
    # A tool may mention a user profile unrelated to one of the declared build
    # roots. Keep diagnostics useful without publishing a user name or drive.
    text = re.sub(
        r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]Users[\\/][^\\/\s]+",
        "<user-root>",
        text,
    )
    text = re.sub(
        r"(?i)(?<![A-Za-z0-9])[A-Z]:(?=[\\/])",
        "<drive>",
        text,
    )
    return text


def _portable_command_records(
    commands: Sequence[NativeBuildCommandRecord],
    *,
    source: Path,
    temporary_root: Path,
    toolchain: NativeAxleToolchainReport,
) -> tuple[NativeBuildCommandRecord, ...]:
    """Create a machine-neutral command audit for manifests and API output."""

    replacements: list[tuple[Path, str]] = [
        (temporary_root, "<temporary-build>"),
        (source, "<runtime-source>"),
        (Path.home().resolve(strict=False), "<user-root>"),
    ]
    for tool_path in (
        toolchain.cmake_path, toolchain.ctest_path,
        toolchain.visual_studio_path, toolchain.cl_path,
    ):
        if tool_path is not None:
            replacements.append((Path(tool_path), "<toolchain>"))

    portable: list[NativeBuildCommandRecord] = []
    for record in commands:
        arguments = tuple(
            Path(value).name if index == 0 else _redact_local_path_text(
                value, replacements,
            )
            for index, value in enumerate(record.command)
        )
        portable.append(replace(
            record,
            command=arguments,
            stdout_tail=_redact_local_path_text(record.stdout_tail, replacements),
            stderr_tail=_redact_local_path_text(record.stderr_tail, replacements),
        ))
    return tuple(portable)


def _assert_no_local_path_leaks(
    publish_root: Path,
    forbidden_roots: Sequence[Path],
) -> None:
    """Fail closed when a staged payload contains a developer-local path."""

    needles: set[bytes] = set()
    for root in forbidden_roots:
        raw = str(Path(root).resolve(strict=False))
        for variant in {
            raw,
            raw.replace("\\", "/"),
            raw.replace("/", "\\"),
            raw.replace("\\", "\\\\"),
        }:
            if not variant:
                continue
            needles.add(variant.casefold().encode("utf-8"))
            needles.add(variant.casefold().encode("utf-16-le"))

    def inspect(payload: bytes, label: str) -> None:
        folded = payload.lower()
        if any(needle in folded for needle in needles):
            raise StoryAxleRuntimeBuildError(
                f"Distributable payload contains a local staging path: {label}"
            )
        # Catch a user profile printed by a subprocess outside the known roots.
        text = payload.decode("utf-8", errors="ignore")
        if re.search(
            r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]Users[\\/][^\\/\s]+",
            text,
        ):
            raise StoryAxleRuntimeBuildError(
                f"Distributable payload contains a local user path: {label}"
            )

    for path in sorted(
        (item for item in publish_root.rglob("*") if item.is_file()),
        key=lambda item: item.as_posix().casefold(),
    ):
        relative = path.relative_to(publish_root).as_posix()
        if path.suffix.casefold() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    inspect(name.encode("utf-8"), f"{relative}:{name}:name")
                    inspect(archive.read(name), f"{relative}:{name}")
        else:
            inspect(path.read_bytes(), relative)


def _run_command(
    name: str,
    command: Sequence[str | Path],
    *,
    cwd: Path,
    timeout: int,
) -> NativeBuildCommandRecord:
    started = time.monotonic()
    try:
        completed = run_hidden(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise StoryAxleRuntimeBuildError(f"{name} exceeded its {timeout}-second timeout") from exc
    except OSError as exc:
        raise StoryAxleRuntimeBuildError(f"{name} could not start: {exc}") from exc
    record = NativeBuildCommandRecord(
        name=name,
        command=tuple(str(value) for value in command),
        returncode=int(completed.returncode),
        duration_seconds=round(time.monotonic() - started, 3),
        stdout_tail=_command_tail(completed.stdout or ""),
        stderr_tail=_command_tail(completed.stderr or ""),
    )
    if completed.returncode != 0:
        detail = record.stderr_tail.strip() or record.stdout_tail.strip()
        raise StoryAxleRuntimeBuildError(
            f"{name} failed with exit code {completed.returncode}"
            + (f": {detail[-1200:]}" if detail else "")
        )
    return record


def _runtime_version(source: Path) -> str:
    text = (source / "CMakeLists.txt").read_text("utf-8")
    match = _VERSION.search(text)
    if match is None:
        raise StoryAxleRuntimeBuildError("Native axle runtime version could not be derived")
    return match.group(1)


def _pe_layout(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise StoryAxleRuntimeBuildError("Built axle runtime is not a PE image")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset > len(data) - 24:
        raise StoryAxleRuntimeBuildError("Built axle runtime PE header is out of bounds")
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise StoryAxleRuntimeBuildError("Built axle runtime has an invalid PE signature")
    coff = pe_offset + 4
    machine, section_count, _timestamp, _symbols, _count, optional_size, _flags = struct.unpack_from(
        "<HHIIIHH", data, coff,
    )
    if machine != 0x8664:
        raise StoryAxleRuntimeBuildError("Built axle runtime does not target x64")
    if section_count < 1 or section_count > 96:
        raise StoryAxleRuntimeBuildError("Built axle runtime has an invalid section count")
    optional = coff + 20
    if optional_size < 152 or optional + optional_size > len(data):
        raise StoryAxleRuntimeBuildError("Built axle runtime optional header is out of bounds")
    sections: list[tuple[int, int, int, int]] = []
    table = optional + optional_size
    if table + section_count * 40 > len(data):
        raise StoryAxleRuntimeBuildError("Built axle runtime section table is out of bounds")
    for index in range(section_count):
        offset = table + index * 40
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", data, offset + 8,
        )
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset, raw_size))
    return optional, optional_size, sections


def _rva_offset(
    rva: int, size: int, sections: Iterable[tuple[int, int, int, int]], data_size: int,
) -> int:
    for virtual_address, span, raw_offset, raw_size in sections:
        if virtual_address <= rva and rva + size <= virtual_address + span:
            delta = rva - virtual_address
            if delta + size <= raw_size and raw_offset + delta + size <= data_size:
                return raw_offset + delta
    raise StoryAxleRuntimeBuildError("Built axle runtime PE directory is outside backed sections")


def _pe_imports_and_signature(path: Path) -> tuple[tuple[str, ...], bool]:
    data = path.read_bytes()
    optional, optional_size, sections = _pe_layout(data)
    if struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise StoryAxleRuntimeBuildError("Built axle runtime is not PE32+ x64")
    import_rva, import_size = struct.unpack_from("<II", data, optional + 120)
    certificate_offset, certificate_size = struct.unpack_from("<II", data, optional + 144)
    certificate_present = (
        certificate_offset > 0
        and certificate_size >= 8
        and certificate_offset + certificate_size <= len(data)
    )
    imports: list[str] = []
    if import_rva and import_size:
        descriptor = _rva_offset(import_rva, min(import_size, 20), sections, len(data))
        descriptor_count = min(4096, max(1, import_size // 20))
        for _index in range(descriptor_count):
            if descriptor + 20 > len(data):
                raise StoryAxleRuntimeBuildError(
                    "Built axle runtime import descriptor is out of bounds"
                )
            row = struct.unpack_from("<IIIII", data, descriptor)
            if row == (0, 0, 0, 0, 0):
                break
            name_rva = row[3]
            name_offset = _rva_offset(name_rva, 1, sections, len(data))
            terminator = data.find(b"\0", name_offset, min(len(data), name_offset + 260))
            if terminator < 0:
                raise StoryAxleRuntimeBuildError("Built axle runtime has an invalid import name")
            imports.append(data[name_offset:terminator].decode("ascii").casefold())
            descriptor += 20
        else:
            raise StoryAxleRuntimeBuildError("Built axle runtime import table is unbounded")
    return tuple(sorted(set(imports))), certificate_present


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _safe_config_name(model_name: str) -> str:
    normalized = str(model_name).strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", normalized):
        raise ValueError(f"Vehicle model cannot form a safe config filename: {model_name}")
    if normalized.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError(f"Vehicle model uses a reserved Windows device name: {model_name}")
    return f"{normalized}.axles.json"


def _zip_directory(source: Path, archive_path: Path, *, prefix: str = "") -> str:
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True,
    ) as archive:
        for path in sorted(source.rglob("*"), key=lambda value: value.as_posix().casefold()):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            name = f"{prefix.rstrip('/')}/{relative}" if prefix else relative
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return _sha256(archive_path)


def _write_checksum(path: Path, digest: str) -> None:
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii",
    )


def build_story_axle_runtime_candidate(
    request: StoryAxleRuntimeBuildRequest,
    *,
    source_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> StoryAxleRuntimeBuildResult:
    """Compile, validate, and atomically stage native Story controller candidates."""

    planned = request.validate()
    source = (source_root or _runtime_source_root()).expanduser().resolve(strict=False)
    toolchain = inspect_native_axle_toolchain(source_root=source)
    if (
        not toolchain.ready
        or toolchain.cmake_path is None
        or toolchain.ctest_path is None
        or toolchain.cmake_generator is None
    ):
        raise StoryAxleRuntimeBuildError("; ".join(toolchain.problems))
    runtime_version = _runtime_version(source)
    output = planned.output_directory
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(
        prefix=f".{output.name}.native-axle-", dir=output.parent,
    )).resolve()
    build_directory = temporary_root / "build"
    publish = temporary_root / "publish"
    commands: list[NativeBuildCommandRecord] = []
    callback = progress or (lambda _message: None)
    try:
        callback("Configuring native axle runtime")
        commands.append(_run_command(
            "CMake configure",
            [
                toolchain.cmake_path, "-S", source, "-B", build_directory,
                "-G", toolchain.cmake_generator, "-A", "x64",
                "-DVWA_BUILD_STORY_HOSTS=ON", "-DVWA_BUILD_TESTS=ON",
                "-DVWA_BUILD_CONFIG_VALIDATOR=ON",
                "-DVWA_BUILD_SETTINGS_EDITOR=ON",
            ],
            cwd=source, timeout=_COMMAND_TIMEOUT_SECONDS,
        ))
        callback("Compiling Legacy and Enhanced controllers")
        commands.append(_run_command(
            "Native build",
            [
                toolchain.cmake_path, "--build", build_directory,
                "--config", "Release", "--parallel",
            ],
            cwd=source, timeout=_COMMAND_TIMEOUT_SECONDS,
        ))
        callback("Running native controller tests")
        commands.append(_run_command(
            "Native CTest",
            [
                toolchain.ctest_path, "--test-dir", build_directory,
                "-C", "Release", "--output-on-failure",
            ],
            cwd=source, timeout=_COMMAND_TIMEOUT_SECONDS,
        ))

        validator = build_directory / "Release" / "VehicleWorkbenchAxlesConfigValidator.exe"
        if not validator.is_file():
            raise StoryAxleRuntimeBuildError("Native configuration validator was not produced")
        settings_editor = (
            build_directory / "Release" / "VehicleWorkbenchAxles.Settings.exe"
        )
        if not settings_editor.is_file():
            settings_editor = build_directory / "VehicleWorkbenchAxles.Settings.exe"
        if not settings_editor.is_file():
            raise StoryAxleRuntimeBuildError(
                "Native recipient settings editor was not produced"
            )
        editor_imports, editor_authenticode_present = _pe_imports_and_signature(
            settings_editor
        )
        editor_forbidden = sorted(set(editor_imports) & _FORBIDDEN_DYNAMIC_CRT)
        if editor_forbidden:
            raise StoryAxleRuntimeBuildError(
                "Settings editor imports dynamic CRT files: "
                + ", ".join(editor_forbidden)
            )
        settings_editor_sha256 = _sha256(settings_editor)
        staged_configurations: dict[str, list[Path]] = {}
        binary_hashes: dict[str, str] = {}
        for target in planned.targets:
            edition = _TARGET_LABELS[target]
            callback(f"Staging {edition} controller")
            binary = (
                build_directory / f"story-{edition}" / "Release" /
                "VehicleWorkbenchAxles.asi"
            )
            evidence = inspect_story_runtime_binary(binary)
            data = binary.read_bytes()
            expected_marker = (
                f"VehicleWorkbenchAxles.BuildTarget={target}".encode("ascii")
                + b"\0"
            )
            if expected_marker not in data:
                raise StoryAxleRuntimeBuildError(
                    f"{edition} controller descriptor does not match its edition"
                )
            if runtime_version.encode("ascii") + b"\0" not in data:
                raise StoryAxleRuntimeBuildError(
                    f"{edition} controller does not contain runtime version {runtime_version}"
                )
            imports, authenticode_present = _pe_imports_and_signature(binary)
            forbidden = sorted(set(imports) & _FORBIDDEN_DYNAMIC_CRT)
            if forbidden:
                raise StoryAxleRuntimeBuildError(
                    f"{edition} controller imports dynamic CRT files: {', '.join(forbidden)}"
                )

            edition_root = publish / edition
            runtime_root = edition_root / "VehicleWorkbenchAxles"
            profile_root = runtime_root / "profiles"
            schema_root = runtime_root / "schemas"
            profile_root.mkdir(parents=True)
            schema_root.mkdir(parents=True)
            shutil.copyfile(binary, edition_root / "VehicleWorkbenchAxles.asi")
            shutil.copyfile(
                settings_editor,
                edition_root / "VehicleWorkbenchAxles.Settings.exe",
            )
            for name in ("compatibility.json", "runtime-package.json"):
                shutil.copyfile(source / "profiles" / name, profile_root / name)
            for name in (
                "axle-config.schema.json", "story-runtime-profile.schema.json",
                "story-runtime-receipt.schema.json",
            ):
                shutil.copyfile(source / "schemas" / name, schema_root / name)

            runtime_package_path = profile_root / "runtime-package.json"
            runtime_package = json.loads(runtime_package_path.read_text("utf-8"))
            runtime_package["runtime"]["version"] = runtime_version
            runtime_package["runtime"]["configurationDestination"] = (
                planned.settings.configuration_directory
            )
            runtime_package["runtime"]["logDestination"] = planned.settings.log_file
            _write_json(runtime_package_path, runtime_package)
            _write_json(runtime_root / "runtime.json", planned.settings.to_runtime_json())

            config_root = edition_root.joinpath(
                *planned.settings.configuration_directory.split("/")
            )
            config_root.mkdir(parents=True, exist_ok=True)
            config_paths: list[Path] = []
            for item in planned.configurations:
                targeted = retarget_axle_configuration(item.configuration, target)
                payload = story_native_runtime_configuration(
                    targeted, bones=item.steering_evidence_bones,
                )
                name = _safe_config_name(targeted.vehicle_model)
                destination = config_root / name
                serialized = json.dumps(payload, indent=2) + "\n"
                if len(serialized.encode("utf-8")) > _MAX_CONFIG_BYTES:
                    raise ValueError(f"Runtime configuration exceeds 1 MiB: {name}")
                destination.write_text(serialized, encoding="utf-8")
                config_paths.append(destination)
            staged_configurations[target] = config_paths

            receipt = {
                "schema_version": 1,
                "artifact": "VehicleWorkbenchAxles.asi",
                "edition": edition.casefold(),
                "descriptor_target": target,
                "architecture": evidence.architecture,
                "toolchain": "msvc",
                "runtime_library": "static",
                "build_id": planned.build_id,
                "runtime_version": runtime_version,
                "sha256": evidence.sha256,
                "ctest_passed": True,
                "pe_validated": True,
                "exports_validated": list(STORY_RUNTIME_REQUIRED_EXPORTS),
                "dynamic_crt_imports_rejected": True,
                "authenticode_certificate_present": authenticode_present,
                "settings_editor": {
                    "artifact": "VehicleWorkbenchAxles.Settings.exe",
                    "sha256": settings_editor_sha256,
                    "pe_x64_validated": True,
                    "dynamic_crt_imports_rejected": True,
                    "authenticode_certificate_present": (
                        editor_authenticode_present
                    ),
                },
                "game_acceptance": "not-tested",
                "supported": False,
                "unsigned": (
                    not authenticode_present
                    or not editor_authenticode_present
                ),
                "notice": (
                    "Build validation is not an in-game acceptance receipt and "
                    "does not mark this edition supported."
                ),
            }
            _write_json(edition_root / "build-validation-receipt.json", receipt)
            _write_json(runtime_root / "runtime-metadata.json", {
                "schema_version": 1,
                "runtime_name": "VehicleWorkbenchAxles",
                "runtime_version": runtime_version,
                "target": target,
                "build_id": planned.build_id,
                "binary_sha256": evidence.sha256,
                "settings_editor_sha256": settings_editor_sha256,
                "game_acceptance": "not-tested",
                "supported": False,
                "configuration_directory": planned.settings.configuration_directory,
                "log_file": planned.settings.log_file,
                "authenticode_certificate_present": authenticode_present,
                "settings_editor_authenticode_certificate_present": (
                    editor_authenticode_present
                ),
            })
            binary_hashes[target] = evidence.sha256

        if len(set(binary_hashes.values())) != len(binary_hashes):
            raise StoryAxleRuntimeBuildError(
                "Edition-specific controller binaries are byte-identical"
            )
        all_configs = [
            path for target in planned.targets
            for path in staged_configurations[target]
        ]
        if all_configs:
            callback("Validating generated configs with the native parser")
            commands.append(_run_command(
                "Native configuration validation",
                [validator, *all_configs],
                cwd=source, timeout=120,
            ))

        archives: list[Path] = []
        archive_hashes: dict[str, str] = {}
        if planned.create_archives:
            callback("Creating controller archives with deterministic ZIP metadata")
            for target in planned.targets:
                edition = _TARGET_LABELS[target]
                archive = publish / f"VehicleWorkbenchAxles-{edition}-{runtime_version}.zip"
                digest = _zip_directory(publish / edition, archive)
                _write_checksum(archive, digest)
                archives.append(archive)
                archive_hashes[archive.name] = digest
            if len(planned.targets) > 1:
                combined = publish / (
                    f"VehicleWorkbenchAxles-{runtime_version}-Legacy-and-Enhanced.zip"
                )
                with zipfile.ZipFile(
                    combined, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True,
                ) as archive:
                    for target in planned.targets:
                        edition = _TARGET_LABELS[target]
                        root = publish / edition
                        for path in sorted(
                            root.rglob("*"), key=lambda value: value.as_posix().casefold(),
                        ):
                            if not path.is_file():
                                continue
                            relative = path.relative_to(root).as_posix()
                            info = zipfile.ZipInfo(
                                f"{edition}/{relative}", (2020, 1, 1, 0, 0, 0),
                            )
                            info.compress_type = zipfile.ZIP_DEFLATED
                            info.external_attr = 0o100644 << 16
                            archive.writestr(info, path.read_bytes())
                digest = _sha256(combined)
                _write_checksum(combined, digest)
                archives.append(combined)
                archive_hashes[combined.name] = digest

        portable_commands = _portable_command_records(
            commands,
            source=source,
            temporary_root=temporary_root,
            toolchain=toolchain,
        )
        command_payload = [record.to_dict() for record in portable_commands]
        manifest = publish / "build-manifest.json"
        _write_json(manifest, {
            "schema_version": 1,
            "operation": "build_story_axle_runtime",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "build_id": planned.build_id,
            "runtime_version": runtime_version,
            "targets": list(planned.targets),
            "settings": planned.settings.to_runtime_json(),
            "configurations": [
                {
                    "configuration_id": item.normalized_configuration_id,
                    "model": item.configuration.vehicle_model,
                    "model_hash": item.normalized_model_hash,
                }
                for item in planned.configurations
            ],
            "binary_sha256": binary_hashes,
            "settings_editor_sha256": settings_editor_sha256,
            "archives": archive_hashes,
            "validation": {
                "cmake_build": "passed",
                "ctest": "passed",
                "native_config_parser": "passed" if all_configs else "not-applicable",
                "pe_x64_exports": "passed",
                "edition_separation": "passed",
                "dynamic_crt_rejected": "passed",
                "settings_editor_pe_x64": "passed",
                "settings_editor_dynamic_crt_rejected": "passed",
                "game_acceptance": "not-tested",
                "supported": False,
            },
            "commands": command_payload,
        })
        _assert_no_local_path_leaks(
            publish,
            (temporary_root, source, Path.home()),
        )
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Output appeared while the build was running: {output}")
        # Windows rename is a no-clobber operation. The preflight above also
        # protects non-Windows backend tests from replacing an existing path.
        publish.rename(output)
        files = tuple(sorted(
            path.relative_to(output).as_posix()
            for path in output.rglob("*") if path.is_file()
        ))
        relocated_archives = tuple(output / path.name for path in archives)
        return StoryAxleRuntimeBuildResult(
            root=output,
            runtime_version=runtime_version,
            built_targets=planned.targets,
            archives=relocated_archives,
            checksums=archive_hashes,
            files=files,
            manifest=output / "build-manifest.json",
            commands=portable_commands,
        )
    except Exception:
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


__all__ = [
    "NativeAxleToolchainReport", "NativeToolchainCheck",
    "StoryAxleRuntimeBuildError",
    "StoryAxleRuntimeBuildRequest", "StoryAxleRuntimeBuildResult",
    "StoryAxleRuntimeSettings", "build_story_axle_runtime_candidate",
    "default_story_axle_runtime_settings",
    "inspect_native_axle_toolchain", "portable_runtime_path",
    "validate_runtime_relative_path",
]

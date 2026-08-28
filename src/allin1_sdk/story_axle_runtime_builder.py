"""First-class native Story axle controller builds for the SDK.

The ASI is generic per GTA edition.  Vehicle packs customize its schema-2
``runtime.json`` and provide one or more model sidecars; they do not need a
vehicle-specific native binary.  Local outputs are deliberately candidate
builds and never impersonate an in-game acceptance receipt.
"""

from __future__ import annotations

import hashlib
import json
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


@dataclass(frozen=True)
class StoryAxleRuntimeSettings:
    enabled: bool = True
    discovery_interval_ms: int = 250
    recovery_interval_ms: int = 2000
    restore_on_unload: bool = True
    configuration_directory: str = "VehicleWorkbenchAxles/configs"
    log_file: str = "VehicleWorkbenchAxles/logs/VehicleWorkbenchAxles.log"

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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("source_root", "cmake_path", "ctest_path", "visual_studio_path"):
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


def _visual_studio_installation() -> tuple[Path | None, str | None]:
    if os.name != "nt":
        return None, "Native Story ASI builds require Windows"
    program_files = os.environ.get("ProgramFiles(x86)", "").strip()
    if not program_files:
        return None, "ProgramFiles(x86) is unavailable"
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None, "Visual Studio Build Tools discovery (vswhere.exe) was not found"
    try:
        completed = run_hidden(
            [
                vswhere, "-latest", "-products", "*", "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Visual Studio Build Tools discovery failed: {exc}"
    raw_path = completed.stdout.strip() if completed.returncode == 0 else ""
    if not raw_path or "\n" in raw_path or "\r" in raw_path:
        return None, "A supported Visual Studio C++ x64 toolchain is not installed"
    path = Path(raw_path)
    if not path.is_dir():
        return None, "A supported Visual Studio C++ x64 toolchain is not installed"
    return path.resolve(), None


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


def inspect_native_axle_toolchain(
    *, source_root: Path | None = None,
) -> NativeAxleToolchainReport:
    source = (source_root or _runtime_source_root()).expanduser().resolve(strict=False)
    problems: list[str] = []
    required = (
        source / "CMakeLists.txt",
        source / "src" / "runtime.cpp",
        source / "src" / "asi_entry.cpp",
        source / "tools" / "config_validator.cpp",
    )
    missing = [path.name for path in required if not path.is_file() or path.is_symlink()]
    if missing:
        problems.append("Native axle source is incomplete: " + ", ".join(missing))

    cmake_text = shutil.which("cmake")
    cmake = Path(cmake_text).resolve() if cmake_text else None
    ctest_text = shutil.which("ctest")
    if ctest_text is None and cmake is not None:
        adjacent = cmake.with_name("ctest.exe")
        ctest_text = str(adjacent) if adjacent.is_file() else None
    ctest = Path(ctest_text).resolve() if ctest_text else None
    cmake_version: str | None = None
    if cmake is None:
        problems.append("CMake 3.20 or newer is not available on PATH")
    else:
        try:
            completed = run_hidden(
                [cmake, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10, check=False,
            )
            match = re.search(r"cmake version (\d+\.\d+(?:\.\d+)?)", completed.stdout)
            cmake_version = match.group(1) if match else None
            if completed.returncode != 0 or cmake_version is None:
                problems.append("CMake version could not be verified")
            else:
                parts = tuple(int(value) for value in cmake_version.split(".")[:2])
                if parts < (3, 20):
                    problems.append("CMake 3.20 or newer is required")
        except (OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"CMake probe failed: {exc}")
    if ctest is None:
        problems.append("CTest is not available beside CMake or on PATH")
    visual_studio, visual_studio_problem = _visual_studio_installation()
    cmake_generator: str | None = None
    if visual_studio_problem:
        problems.append(visual_studio_problem)
    elif visual_studio is not None:
        cmake_generator, generator_problem = _visual_studio_cmake_generator(
            visual_studio,
        )
        if generator_problem:
            problems.append(generator_problem)
    return NativeAxleToolchainReport(
        ready=not problems,
        platform=os.name,
        source_root=source,
        cmake_path=cmake,
        cmake_version=cmake_version,
        ctest_path=ctest,
        visual_studio_path=visual_studio,
        cmake_generator=cmake_generator,
        problems=tuple(problems),
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
        settings = self.settings.validate()
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
                "game_acceptance": "not-tested",
                "supported": False,
                "unsigned": not authenticode_present,
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
                "game_acceptance": "not-tested",
                "supported": False,
                "configuration_directory": planned.settings.configuration_directory,
                "log_file": planned.settings.log_file,
                "authenticode_certificate_present": authenticode_present,
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

        command_payload = [record.to_dict() for record in commands]
        for record in command_payload:
            record["command"] = [Path(value).name if index == 0 else value for index, value in enumerate(record["command"])]
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
            "archives": archive_hashes,
            "validation": {
                "cmake_build": "passed",
                "ctest": "passed",
                "native_config_parser": "passed" if all_configs else "not-applicable",
                "pe_x64_exports": "passed",
                "edition_separation": "passed",
                "dynamic_crt_rejected": "passed",
                "game_acceptance": "not-tested",
                "supported": False,
            },
            "commands": command_payload,
        })
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
            commands=tuple(commands),
        )
    except Exception:
        raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


__all__ = [
    "NativeAxleToolchainReport", "StoryAxleRuntimeBuildError",
    "StoryAxleRuntimeBuildRequest", "StoryAxleRuntimeBuildResult",
    "StoryAxleRuntimeSettings", "build_story_axle_runtime_candidate",
    "inspect_native_axle_toolchain", "validate_runtime_relative_path",
]

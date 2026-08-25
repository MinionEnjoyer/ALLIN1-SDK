"""Read-only ingestion for bounded ALLIN1 product workspaces.

The product-workspace descriptor is an evidence map, not an install manifest.
This module inventories only declared source areas, never imports workspace code,
and keeps evidence nodes structurally separate from installable package nodes.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from allin1_sdk.processes import run_hidden
from allin1_sdk.product_api_contract import (
    RuntimeContractReport,
    audit_runtime_contracts,
)

try:  # Python 3.10 remains supported by the SDK.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


WORKSPACE_SCHEMA_VERSION = 1
WORKSPACE_KIND = "product_workspace"
SUPPORTED_EDITIONS = frozenset({"legacy", "enhanced"})
SUPPORTED_INVENTORIES = frozenset({"git_tracked_allowlist"})
ROLE_CATEGORIES = {
    "launcher_host": "host",
    "story_runtime": "runtime",
    "official_content_pack": "package",
    "optional_package": "package",
    "sdk_example": "package",
    "build_tool": "tool",
    "test_evidence": "evidence",
    "documentation_evidence": "evidence",
}
RELATION_SIGNATURES = {
    "deploys": ({"host"}, {"runtime"}),
    "registers": ({"host"}, {"package"}),
    "uses_shared_runtime": ({"package"}, {"runtime"}),
    "builds_install_time_assets": ({"tool"}, {"package"}),
    "documents_system": ({"package", "evidence"}, {"package"}),
    "integrates_with_api": ({"package"}, {"runtime"}),
    "verifies": ({"evidence"}, {"host", "runtime", "package", "tool"}),
    "documents": ({"evidence"}, {"host", "runtime", "package", "tool"}),
}
MAX_DESCRIPTOR_BYTES = 2 * 1024 * 1024
MAX_INVENTORY_FILES = 50_000
MAX_GIT_RECORDS = 100_000
MAX_INVENTORY_BYTES = 8 * 1024 * 1024 * 1024
MAX_EVIDENCE_SAMPLES = 12
MAX_EVIDENCE_SAMPLE_OWNERS = 16
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
_DEFAULT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?$")
_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')
_WINDOWS_DEVICE_NAMES = frozenset({
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})
_WORKSPACE_FIELDS = frozenset({
    "schema_version", "id", "name", "version", "kind", "editions",
    "description", "source_policy", "components", "relationships",
})
_POLICY_FIELDS = frozenset({
    "inventory", "follow_symlinks", "execute_sources", "allowlisted_roots",
    "allowlisted_files", "excluded_roots",
})
_COMPONENT_FIELDS = frozenset({
    "id", "name", "role", "paths", "package_id", "manifest",
    "content_manifest", "runtime_artifact", "artifact_name", "experimental",
    "defaults", "package_discovery", "api_contract",
})
_RELATION_FIELDS = frozenset({"source", "target", "type"})


def _reject_unknown(
    data: Mapping[str, Any], allowed: Iterable[str], label: str,
) -> None:
    unknown = set(data) - set(allowed)
    if unknown:
        raise ValueError(
            f"Unsupported {label} field(s): " + ", ".join(sorted(unknown))
        )


def _required_text(data: Mapping[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be non-empty text")
    if value != value.strip():
        raise ValueError(f"{label}.{key} must not have outer whitespace")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"{label} must be a safe lowercase identifier")
    normalized = value.casefold()
    if value != normalized or not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must be a safe lowercase identifier")
    return normalized


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    if normalized != normalized.strip():
        raise ValueError(f"{label} must not have outer whitespace")
    parts = normalized.split("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} must be relative and contain no traversal")
    for part in parts:
        if part.endswith((".", " ")) or any(
            character in _WINDOWS_INVALID_CHARS or ord(character) < 32
            for character in part
        ):
            raise ValueError(f"{label} contains a Windows-invalid path component")
        if part.split(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES:
            raise ValueError(f"{label} contains a reserved Windows device name")
    return path


def _path_tuple(value: object, label: str) -> tuple[PurePosixPath, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty array of relative paths")
    paths = tuple(
        _safe_relative(item, f"{label}[{index}]")
        for index, item in enumerate(value, start=1)
    )
    folded = [item.as_posix().casefold() for item in paths]
    if len(folded) != len(set(folded)):
        raise ValueError(f"{label} contains duplicate paths")
    return paths


def _optional_paths(value: object, label: str) -> tuple[PurePosixPath, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array of relative paths")
    if not value:
        return ()
    return _path_tuple(value, label)


def _is_under(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return path == parent or parent in path.parents


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & flag
    )


def _candidate_without_links(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if _is_reparse(current):
            raise ValueError(f"Workspace paths may not traverse links: {relative}")
        if not current.exists():
            break
    return candidate


def _json_safe(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    for key in value:
        if not isinstance(key, str) or not _DEFAULT_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"{label} contains an invalid setting key")
    try:
        detached = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain only finite JSON values") from exc
    return detached


@dataclass(frozen=True)
class SourcePolicy:
    inventory: str
    allowlisted_roots: tuple[PurePosixPath, ...]
    allowlisted_files: tuple[PurePosixPath, ...]
    excluded_roots: tuple[PurePosixPath, ...]
    follow_symlinks: bool = False
    execute_sources: bool = False

    def permits(self, path: PurePosixPath) -> bool:
        if any(_is_under(path, excluded) for excluded in self.excluded_roots):
            return False
        return path in self.allowlisted_files or any(
            _is_under(path, root) for root in self.allowlisted_roots
        )


@dataclass(frozen=True)
class WorkspaceComponent:
    component_id: str
    name: str
    role: str
    category: str
    paths: tuple[PurePosixPath, ...]
    package_id: str | None = None
    manifest: PurePosixPath | None = None
    content_manifest: PurePosixPath | None = None
    runtime_artifact: PurePosixPath | None = None
    api_contract: PurePosixPath | None = None
    artifact_name: str | None = None
    experimental: bool = False
    defaults: Mapping[str, Any] | None = None
    package_discovery: bool = False

    @property
    def install_candidate(self) -> bool:
        return self.role == "optional_package" and self.package_discovery

    @property
    def managed_builtin(self) -> bool:
        return self.role == "official_content_pack"


@dataclass(frozen=True)
class WorkspaceRelationship:
    source: str
    target: str
    relation: str


@dataclass(frozen=True)
class WorkspaceFinding:
    severity: str
    code: str
    message: str
    component_id: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    size: int
    source: str
    kind: str = "file"


@dataclass(frozen=True)
class WorkspaceInventory:
    method: str
    entries: tuple[InventoryEntry, ...]
    total_bytes: int

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(item.path for item in self.entries)


@dataclass(frozen=True)
class WorkspaceGraphNode:
    node_id: str
    label: str
    role: str
    category: str
    package_id: str | None
    experimental: bool
    install_candidate: bool
    managed_builtin: bool
    runtime_artifact: str | None


@dataclass(frozen=True)
class WorkspaceGraphEdge:
    source: str
    target: str
    relation: str


@dataclass(frozen=True)
class WorkspaceEvidenceSample:
    path: str
    size: int


@dataclass(frozen=True)
class WorkspaceSharedEvidenceSample:
    path: str
    size: int
    owner_count: int
    owners: tuple[str, ...]
    owners_truncated: bool


@dataclass(frozen=True)
class WorkspaceComponentEvidence:
    component_id: str
    declared_paths: tuple[str, ...]
    matched_files: int
    matched_bytes: int
    unique_files: int
    unique_bytes: int
    shared_files: int
    shared_bytes: int


@dataclass(frozen=True)
class WorkspaceEvidenceRollup:
    files: int
    bytes: int
    samples: tuple[WorkspaceEvidenceSample, ...]


@dataclass(frozen=True)
class WorkspaceSharedEvidenceRollup:
    files: int
    bytes: int
    samples: tuple[WorkspaceSharedEvidenceSample, ...]


@dataclass(frozen=True)
class WorkspaceEvidenceAudit:
    components: tuple[WorkspaceComponentEvidence, ...]
    unassigned: WorkspaceEvidenceRollup
    shared: WorkspaceSharedEvidenceRollup


@dataclass(frozen=True)
class ProductWorkspace:
    descriptor: Path
    workspace_id: str
    name: str
    version: str
    editions: tuple[str, ...]
    description: str
    source_policy: SourcePolicy
    components: tuple[WorkspaceComponent, ...]
    relationships: tuple[WorkspaceRelationship, ...]


@dataclass(frozen=True)
class ProductWorkspaceReport:
    workspace: ProductWorkspace
    inventory: WorkspaceInventory
    nodes: tuple[WorkspaceGraphNode, ...]
    edges: tuple[WorkspaceGraphEdge, ...]
    findings: tuple[WorkspaceFinding, ...]
    evidence: WorkspaceEvidenceAudit
    runtime_contracts: RuntimeContractReport

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    @property
    def structurally_valid(self) -> bool:
        """Whether the workspace envelope is safe to adapt for inspection.

        Runtime/API mismatches are semantic diagnostics.  They must remain
        visible in the Linker instead of preventing the workspace from opening.
        """
        return not any(
            item.severity == "error"
            and not item.code.startswith(("api_contract_", "runtime_contract_"))
            for item in self.findings
        )

    @property
    def install_candidates(self) -> tuple[WorkspaceGraphNode, ...]:
        return tuple(item for item in self.nodes if item.install_candidate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": "inspect_product_workspace",
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "workspace": {
                "id": self.workspace.workspace_id,
                "name": self.workspace.name,
                "version": self.workspace.version,
                "editions": list(self.workspace.editions),
                "descriptor": str(self.workspace.descriptor),
            },
            "inventory": {
                "method": self.inventory.method,
                "files": len(self.inventory.entries),
                "total_bytes": self.inventory.total_bytes,
                "entries": [asdict(item) for item in self.inventory.entries],
            },
            "graph": {
                "nodes": [asdict(item) for item in self.nodes],
                "edges": [asdict(item) for item in self.edges],
            },
            "evidence": asdict(self.evidence),
            "api_contracts": self.runtime_contracts.to_dict(),
            "findings": [asdict(item) for item in self.findings],
            "valid": self.valid,
            "structurally_valid": self.structurally_valid,
        }


def _source_policy(value: object) -> SourcePolicy:
    if not isinstance(value, dict):
        raise ValueError("source_policy must be an object")
    _reject_unknown(value, _POLICY_FIELDS, "source_policy")
    inventory = _required_text(value, "inventory", "source_policy")
    if inventory not in SUPPORTED_INVENTORIES:
        raise ValueError("source_policy.inventory is not supported")
    if value.get("follow_symlinks") is not False:
        raise ValueError("source_policy.follow_symlinks must be false")
    if value.get("execute_sources") is not False:
        raise ValueError("source_policy.execute_sources must be false")
    roots = _optional_paths(value.get("allowlisted_roots"), "allowlisted_roots")
    files = _optional_paths(value.get("allowlisted_files"), "allowlisted_files")
    excluded = _optional_paths(value.get("excluded_roots"), "excluded_roots")
    if not roots and not files:
        raise ValueError("source_policy must declare at least one allowlist")
    for path in (*roots, *files):
        if any(_is_under(path, blocked) for blocked in excluded):
            raise ValueError(f"Allowlisted source is excluded by policy: {path}")
    return SourcePolicy(inventory, roots, files, excluded)


def _component(value: object, index: int, policy: SourcePolicy) -> WorkspaceComponent:
    label = f"components[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    _reject_unknown(value, _COMPONENT_FIELDS, label)
    component_id = _identifier(value.get("id"), f"{label}.id")
    role = _required_text(value, "role", label)
    if role not in ROLE_CATEGORIES:
        raise ValueError(f"{label}.role is not supported")
    paths = _path_tuple(value.get("paths"), f"{label}.paths")
    for path in paths:
        if not policy.permits(path):
            raise ValueError(f"{label}.paths is outside source_policy: {path}")

    package_value = value.get("package_id")
    package_id = (
        _identifier(package_value, f"{label}.package_id")
        if package_value is not None else None
    )
    manifest = (
        _safe_relative(value["manifest"], f"{label}.manifest")
        if "manifest" in value else None
    )
    content_manifest = (
        _safe_relative(value["content_manifest"], f"{label}.content_manifest")
        if "content_manifest" in value else None
    )
    for field_name, path in (("manifest", manifest), ("content_manifest", content_manifest)):
        if path is not None and (not policy.permits(path) or path not in paths):
            raise ValueError(f"{label}.{field_name} must be an exact declared path")

    category = ROLE_CATEGORIES[role]
    package_role = role in {"official_content_pack", "optional_package", "sdk_example"}
    if package_role and (package_id is None or manifest is None):
        raise ValueError(f"{label} package roles require package_id and manifest")
    if not package_role and (package_id is not None or manifest is not None):
        raise ValueError(f"{label} non-package roles cannot declare package metadata")
    if content_manifest is not None and role != "optional_package":
        raise ValueError(f"{label}.content_manifest is valid only for optional packages")

    runtime_artifact = (
        _safe_relative(value["runtime_artifact"], f"{label}.runtime_artifact")
        if "runtime_artifact" in value else None
    )
    if role == "story_runtime":
        if runtime_artifact is None or (
            not runtime_artifact.parts
            or runtime_artifact.parts[0].casefold() != "scripts"
            or runtime_artifact.suffix.casefold() != ".dll"
        ):
            raise ValueError(f"{label} runtime requires a scripts/*.dll artifact")
    elif runtime_artifact is not None:
        raise ValueError(f"{label}.runtime_artifact is valid only for story_runtime")

    api_contract = (
        _safe_relative(value["api_contract"], f"{label}.api_contract")
        if "api_contract" in value else None
    )
    if role == "story_runtime":
        if api_contract is not None and not policy.permits(api_contract):
            raise ValueError(f"{label}.api_contract is outside source_policy")
    elif api_contract is not None:
        raise ValueError(f"{label}.api_contract is valid only for story_runtime")

    artifact_name = value.get("artifact_name")
    if artifact_name is not None:
        artifact_name = _required_text(value, "artifact_name", label)
        if "/" in artifact_name or "\\" in artifact_name:
            raise ValueError(f"{label}.artifact_name must be a filename")
        _safe_relative(artifact_name, f"{label}.artifact_name")
    if role == "build_tool" and artifact_name is None:
        raise ValueError(f"{label} build tools require artifact_name")
    if role != "build_tool" and artifact_name is not None:
        raise ValueError(f"{label}.artifact_name is valid only for build_tool")

    experimental = value.get("experimental", False)
    if not isinstance(experimental, bool):
        raise ValueError(f"{label}.experimental must be true or false")
    defaults = _json_safe(value.get("defaults", {}), f"{label}.defaults")
    discovery_default = role == "optional_package"
    package_discovery = value.get("package_discovery", discovery_default)
    if not isinstance(package_discovery, bool):
        raise ValueError(f"{label}.package_discovery must be true or false")
    if category == "evidence":
        if package_discovery:
            raise ValueError(f"{label} evidence may not be a package-discovery target")
        if any((package_id, manifest, content_manifest, runtime_artifact, artifact_name)):
            raise ValueError(f"{label} evidence may not declare install metadata")
    elif package_discovery and role != "optional_package":
        raise ValueError(
            f"{label} only optional packages may enable package discovery"
        )

    return WorkspaceComponent(
        component_id=component_id,
        name=_required_text(value, "name", label),
        role=role,
        category=category,
        paths=paths,
        package_id=package_id,
        manifest=manifest,
        content_manifest=content_manifest,
        runtime_artifact=runtime_artifact,
        api_contract=api_contract,
        artifact_name=artifact_name,
        experimental=experimental,
        defaults=defaults,
        package_discovery=package_discovery,
    )


def _relationship(
    value: object, index: int, components: Mapping[str, WorkspaceComponent],
) -> WorkspaceRelationship:
    label = f"relationships[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    _reject_unknown(value, _RELATION_FIELDS, label)
    source = _identifier(value.get("source"), f"{label}.source")
    target = _identifier(value.get("target"), f"{label}.target")
    relation = _required_text(value, "type", label)
    if source == target:
        raise ValueError(f"{label} cannot relate a component to itself")
    if source not in components or target not in components:
        raise ValueError(f"{label} references an unknown component endpoint")
    signature = RELATION_SIGNATURES.get(relation)
    if signature is None:
        raise ValueError(f"{label}.type is not supported")
    source_types, target_types = signature
    if (
        components[source].category not in source_types
        or components[target].category not in target_types
    ):
        raise ValueError(f"{label} has incompatible endpoint roles for {relation}")
    return WorkspaceRelationship(source, target, relation)


def load_product_workspace(source: str | Path) -> ProductWorkspace:
    selected = Path(source).expanduser().absolute()
    if _is_reparse(selected):
        raise ValueError("Product workspace source may not be a link or reparse point")
    try:
        selected_metadata = selected.lstat()
    except FileNotFoundError:
        selected_metadata = None
    if selected_metadata is not None and stat.S_ISDIR(selected_metadata.st_mode):
        selected = selected / "allin1.workspace.json"
    if not selected.is_file():
        raise FileNotFoundError(f"Product workspace descriptor not found: {selected}")
    if _is_reparse(selected) or _is_reparse(selected.parent):
        raise ValueError("Product workspace descriptor may not be a link or reparse point")
    if selected.stat().st_size > MAX_DESCRIPTOR_BYTES:
        raise ValueError("Product workspace descriptor exceeds the size limit")
    try:
        data = json.loads(
            selected.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Non-finite JSON value: {value}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid product workspace JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Product workspace descriptor must be an object")
    _reject_unknown(data, _WORKSPACE_FIELDS, "workspace")
    if data.get("schema_version") != WORKSPACE_SCHEMA_VERSION:
        raise ValueError(f"workspace schema_version must be {WORKSPACE_SCHEMA_VERSION}")
    if data.get("kind") != WORKSPACE_KIND:
        raise ValueError(f"workspace kind must be {WORKSPACE_KIND}")
    workspace_id = _identifier(data.get("id"), "workspace.id")
    version = _required_text(data, "version", "workspace")
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError("workspace.version must be a numeric release version")
    editions_value = data.get("editions")
    if not isinstance(editions_value, list) or not editions_value or not all(
        isinstance(item, str) and item in SUPPORTED_EDITIONS
        for item in editions_value
    ):
        raise ValueError("workspace.editions must contain Legacy and/or Enhanced")
    editions = tuple(editions_value)
    if len(editions) != len(set(editions)):
        raise ValueError("workspace.editions contains duplicates")
    policy = _source_policy(data.get("source_policy"))
    raw_components = data.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise ValueError("workspace.components must be a non-empty array")
    components = tuple(
        _component(item, index, policy)
        for index, item in enumerate(raw_components, start=1)
    )
    component_map = {item.component_id: item for item in components}
    if len(component_map) != len(components):
        raise ValueError("workspace.components contains duplicate component ids")
    raw_relationships = data.get("relationships", [])
    if not isinstance(raw_relationships, list):
        raise ValueError("workspace.relationships must be an array")
    relationships = tuple(
        _relationship(item, index, component_map)
        for index, item in enumerate(raw_relationships, start=1)
    )
    keys = [(item.source, item.target, item.relation) for item in relationships]
    if len(keys) != len(set(keys)):
        raise ValueError("workspace.relationships contains duplicate edges")
    return ProductWorkspace(
        descriptor=selected.resolve(),
        workspace_id=workspace_id,
        name=_required_text(data, "name", "workspace"),
        version=version,
        editions=editions,
        description=str(data.get("description", "")).strip(),
        source_policy=policy,
        components=components,
        relationships=relationships,
    )


def _excluded(policy: SourcePolicy, path: PurePosixPath) -> bool:
    return any(_is_under(path, item) for item in policy.excluded_roots)


def _bounded_entry(
    entries: dict[str, InventoryEntry], entry: InventoryEntry, total: list[int],
) -> None:
    key = entry.path.casefold()
    if key in entries:
        return
    if len(entries) >= MAX_INVENTORY_FILES:
        raise ValueError("Product workspace inventory exceeds its file-count limit")
    if total[0] + entry.size > MAX_INVENTORY_BYTES:
        raise ValueError("Product workspace inventory exceeds its byte limit")
    entries[key] = entry
    total[0] += entry.size


def _git_inventory(
    root: Path, policy: SourcePolicy, findings: list[WorkspaceFinding],
) -> WorkspaceInventory | None:
    try:
        top = run_hidden(
            ["git", "-C", root, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if top.returncode != 0:
        return None
    try:
        git_root = Path(top.stdout.strip()).resolve(strict=True)
    except (OSError, ValueError):
        return None
    if git_root != root:
        return None
    try:
        listed = run_hidden(
            ["git", "-C", root, "ls-files", "-z", "--stage"],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    raw = listed.stdout
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="surrogateescape")
    records = raw.split("\0")
    if len(records) - 1 > MAX_GIT_RECORDS:
        raise ValueError("Git workspace inventory exceeds its record limit")
    entries: dict[str, InventoryEntry] = {}
    total = [0]
    for record in records:
        if not record:
            continue
        header, separator, name = record.partition("\t")
        if not separator:
            continue
        mode = header.split(" ", 1)[0]
        try:
            relative = _safe_relative(name, "git tracked path")
        except ValueError:
            findings.append(WorkspaceFinding(
                "warning", "unsafe_tracked_path_skipped",
                "A tracked path is not safe on the supported Windows workspace.",
                path=name,
            ))
            continue
        if not policy.permits(relative) or _excluded(policy, relative):
            continue
        if mode == "120000":
            findings.append(WorkspaceFinding(
                "warning", "tracked_link_skipped",
                "A tracked link was excluded without following it.", path=name,
            ))
            continue
        candidate = _candidate_without_links(root, relative)
        if mode == "160000":
            _bounded_entry(
                entries, InventoryEntry(name, 0, "git", "gitlink"), total,
            )
            continue
        if not candidate.is_file():
            findings.append(WorkspaceFinding(
                "warning", "tracked_file_missing",
                "A tracked allowlisted file is absent from this checkout.", path=name,
            ))
            continue
        size = candidate.stat().st_size
        _bounded_entry(entries, InventoryEntry(name, size, "git"), total)
    values = tuple(sorted(entries.values(), key=lambda item: item.path.casefold()))
    return WorkspaceInventory("git_tracked", values, total[0])


def _fallback_inventory(
    root: Path, policy: SourcePolicy, findings: list[WorkspaceFinding],
) -> WorkspaceInventory:
    entries: dict[str, InventoryEntry] = {}
    total = [0]

    def add_file(relative: PurePosixPath) -> None:
        if _excluded(policy, relative):
            return
        candidate = _candidate_without_links(root, relative)
        if not candidate.is_file():
            return
        _bounded_entry(
            entries,
            InventoryEntry(
                relative.as_posix(), candidate.stat().st_size,
                "declared_allowlist",
            ),
            total,
        )

    for relative in policy.allowlisted_files:
        try:
            add_file(relative)
        except ValueError:
            findings.append(WorkspaceFinding(
                "warning", "allowlisted_link_skipped",
                "An allowlisted file traverses a link and was skipped.",
                path=relative.as_posix(),
            ))
    for relative_root in policy.allowlisted_roots:
        if _excluded(policy, relative_root):
            continue
        try:
            source = _candidate_without_links(root, relative_root)
        except ValueError:
            findings.append(WorkspaceFinding(
                "warning", "allowlisted_link_skipped",
                "An allowlisted root is a link and was skipped.",
                path=relative_root.as_posix(),
            ))
            continue
        if source.is_file():
            add_file(relative_root)
            continue
        if not source.is_dir():
            continue
        pending = [source]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name.casefold())
            for child in children:
                child_path = Path(child.path)
                relative = PurePosixPath(child_path.relative_to(root).as_posix())
                if _excluded(policy, relative):
                    continue
                if child.is_symlink() or _is_reparse(child_path):
                    findings.append(WorkspaceFinding(
                        "warning", "allowlisted_link_skipped",
                        "An allowlisted link was skipped without following it.",
                        path=relative.as_posix(),
                    ))
                    continue
                if child.is_dir(follow_symlinks=False):
                    pending.append(child_path)
                elif child.is_file(follow_symlinks=False):
                    add_file(relative)
    values = tuple(sorted(entries.values(), key=lambda item: item.path.casefold()))
    return WorkspaceInventory("declared_allowlists", values, total[0])


def _path_in_inventory(path: PurePosixPath, inventory: WorkspaceInventory) -> bool:
    value = path.as_posix().casefold()
    for entry in inventory.entries:
        candidate = entry.path.casefold()
        if candidate == value or candidate.startswith(value + "/"):
            return True
    return False


def _read_manifest(root: Path, path: PurePosixPath) -> Mapping[str, Any]:
    source = _candidate_without_links(root, path)
    if not source.is_file():
        raise FileNotFoundError(f"Declared manifest is missing: {path}")
    if source.stat().st_size > MAX_DESCRIPTOR_BYTES:
        raise ValueError(f"Declared manifest exceeds the size limit: {path}")
    if path.suffix.casefold() == ".toml":
        with source.open("rb") as stream:
            data = tomllib.load(stream)
    else:
        data = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Non-finite JSON value: {value}")
            ),
        )
    if not isinstance(data, dict):
        raise ValueError(f"Declared manifest must be an object/table: {path}")
    return data


def _manifest_findings(
    root: Path, component: WorkspaceComponent,
) -> list[WorkspaceFinding]:
    findings: list[WorkspaceFinding] = []
    if component.manifest is None or component.package_id is None:
        return findings
    try:
        manifest = _read_manifest(root, component.manifest)
        manifest_id = manifest.get("id")
        if manifest_id != component.package_id:
            raise ValueError(
                f"manifest id {manifest_id!r} does not match {component.package_id!r}"
            )
        schema = manifest.get("schema_version")
        if component.role == "optional_package":
            if schema not in {1, 2}:
                raise ValueError("mod.toml schema_version must be 1 or 2")
        elif schema != 1:
            raise ValueError("JSON package descriptor schema_version must be 1")
        version = manifest.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("manifest version must be non-empty text")
        if component.content_manifest is not None:
            content = _read_manifest(root, component.content_manifest)
            if content.get("id") != component.package_id:
                raise ValueError("content manifest id does not match package_id")
            if content.get("version") != version:
                raise ValueError("content manifest version does not match mod.toml")
            if content.get("schema_version") != 1 or content.get("api_version") != 1:
                raise ValueError("content manifest must use schema/API version 1")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        findings.append(WorkspaceFinding(
            "error", "component_manifest_invalid", str(exc),
            component.component_id, component.manifest.as_posix(),
        ))
    return findings


def _component_evidence(
    components: tuple[WorkspaceComponent, ...],
    inventory: WorkspaceInventory,
    findings: list[WorkspaceFinding],
) -> WorkspaceEvidenceAudit:
    """Map each bounded inventory entry to its declared component owners.

    Matching is deliberately path based and data-only: an entry belongs to a
    component when it is the component's declared path or is beneath one of its
    declared directories. Overlapping declarations are retained as shared
    ownership evidence instead of being assigned by descriptor order.
    """

    component_paths = {
        component.component_id: tuple(
            path.as_posix().casefold() for path in component.paths
        )
        for component in components
    }
    matches: dict[str, list[tuple[InventoryEntry, int]]] = {
        component.component_id: [] for component in components
    }
    unassigned_entries: list[InventoryEntry] = []
    shared_entries: list[tuple[InventoryEntry, tuple[str, ...]]] = []

    for entry in sorted(
        inventory.entries, key=lambda item: (item.path.casefold(), item.path)
    ):
        candidate = entry.path.casefold()
        owners = tuple(sorted(
            (
                component.component_id
                for component in components
                if any(
                    candidate == declared
                    or candidate.startswith(declared + "/")
                    for declared in component_paths[component.component_id]
                )
            ),
            key=str.casefold,
        ))
        owner_count = len(owners)
        if owner_count == 0:
            unassigned_entries.append(entry)
            continue
        for owner in owners:
            matches[owner].append((entry, owner_count))
        if owner_count > 1:
            shared_entries.append((entry, owners))

    component_rollups = tuple(
        WorkspaceComponentEvidence(
            component_id=component.component_id,
            declared_paths=tuple(path.as_posix() for path in component.paths),
            matched_files=len(matches[component.component_id]),
            matched_bytes=sum(
                entry.size for entry, _owner_count
                in matches[component.component_id]
            ),
            unique_files=sum(
                1 for _entry, owner_count in matches[component.component_id]
                if owner_count == 1
            ),
            unique_bytes=sum(
                entry.size for entry, owner_count
                in matches[component.component_id] if owner_count == 1
            ),
            shared_files=sum(
                1 for _entry, owner_count in matches[component.component_id]
                if owner_count > 1
            ),
            shared_bytes=sum(
                entry.size for entry, owner_count
                in matches[component.component_id] if owner_count > 1
            ),
        )
        for component in components
    )
    unassigned = WorkspaceEvidenceRollup(
        files=len(unassigned_entries),
        bytes=sum(entry.size for entry in unassigned_entries),
        samples=tuple(
            WorkspaceEvidenceSample(entry.path, entry.size)
            for entry in unassigned_entries[:MAX_EVIDENCE_SAMPLES]
        ),
    )
    shared = WorkspaceSharedEvidenceRollup(
        files=len(shared_entries),
        bytes=sum(entry.size for entry, _owners in shared_entries),
        samples=tuple(
            WorkspaceSharedEvidenceSample(
                path=entry.path,
                size=entry.size,
                owner_count=len(owners),
                owners=owners[:MAX_EVIDENCE_SAMPLE_OWNERS],
                owners_truncated=len(owners) > MAX_EVIDENCE_SAMPLE_OWNERS,
            )
            for entry, owners in shared_entries[:MAX_EVIDENCE_SAMPLES]
        ),
    )
    if unassigned.files:
        findings.append(WorkspaceFinding(
            "info", "inventory_files_unassigned",
            f"{unassigned.files} bounded inventory file(s) "
            f"({unassigned.bytes} bytes) are not assigned to a component.",
        ))
    if shared.files:
        findings.append(WorkspaceFinding(
            "info", "inventory_files_shared",
            f"{shared.files} bounded inventory file(s) "
            f"({shared.bytes} bytes) are declared by multiple components.",
        ))
    return WorkspaceEvidenceAudit(component_rollups, unassigned, shared)


class ProductWorkspaceInspector:
    """Load and inventory a declared product workspace without executing it."""

    def inspect(self, source: str | Path) -> ProductWorkspaceReport:
        workspace = load_product_workspace(source)
        root = workspace.descriptor.parent
        findings: list[WorkspaceFinding] = []
        inventory = _git_inventory(root, workspace.source_policy, findings)
        if inventory is None:
            findings.append(WorkspaceFinding(
                "info", "git_inventory_unavailable",
                "Git tracked-file evidence was unavailable; only declared allowlists were walked.",
            ))
            inventory = _fallback_inventory(root, workspace.source_policy, findings)
        for component in workspace.components:
            for path in component.paths:
                if not _path_in_inventory(path, inventory):
                    findings.append(WorkspaceFinding(
                        "error", "component_source_missing",
                        "Declared component source is absent from the bounded inventory.",
                        component.component_id, path.as_posix(),
                    ))
            findings.extend(_manifest_findings(root, component))
        nodes = tuple(WorkspaceGraphNode(
            node_id=item.component_id,
            label=item.name,
            role=item.role,
            category=item.category,
            package_id=item.package_id,
            experimental=item.experimental,
            install_candidate=item.install_candidate,
            managed_builtin=item.managed_builtin,
            runtime_artifact=(
                item.runtime_artifact.as_posix()
                if item.runtime_artifact is not None else None
            ),
        ) for item in workspace.components)
        edges = tuple(WorkspaceGraphEdge(
            item.source, item.target, item.relation,
        ) for item in workspace.relationships)
        evidence = _component_evidence(
            workspace.components, inventory, findings,
        )
        runtime_contracts = audit_runtime_contracts(workspace, inventory)
        findings.extend(WorkspaceFinding(
            item.severity, item.code, item.message,
            item.component_id, item.path,
        ) for item in runtime_contracts.findings)
        return ProductWorkspaceReport(
            workspace, inventory, nodes, edges, tuple(findings), evidence,
            runtime_contracts,
        )


__all__ = [
    "InventoryEntry",
    "ProductWorkspace",
    "ProductWorkspaceInspector",
    "ProductWorkspaceReport",
    "SourcePolicy",
    "WorkspaceComponent",
    "WorkspaceComponentEvidence",
    "WorkspaceEvidenceAudit",
    "WorkspaceEvidenceRollup",
    "WorkspaceEvidenceSample",
    "WorkspaceFinding",
    "WorkspaceGraphEdge",
    "WorkspaceGraphNode",
    "WorkspaceInventory",
    "WorkspaceRelationship",
    "WorkspaceSharedEvidenceRollup",
    "WorkspaceSharedEvidenceSample",
    "load_product_workspace",
]

"""Deterministic, read-only runtime API audits for product workspaces.

The auditor connects a checked-in host API contract to the declarative content
manifests and bounded C# evidence already owned by a product workspace.  It
never imports workspace code, loads an assembly, or grants execution authority.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from allin1_sdk.extensions import ExtensionManifest
from allin1_sdk.mods import ModManifest
from allin1_sdk.runtime_api_contract import RuntimeApiContract, RuntimeApiSymbol


RUNTIME_CONTRACT_REPORT_SCHEMA = 1
MAX_CONTRACT_SOURCE_BYTES = 2 * 1024 * 1024
MAX_CONSUMER_SOURCE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class ContractEvidence:
    path: str
    line: int
    excerpt: str


@dataclass(frozen=True)
class RuntimeContractFinding:
    severity: str
    code: str
    message: str
    component_id: str | None = None
    path: str | None = None
    evidence: ContractEvidence | None = None


@dataclass(frozen=True)
class RuntimeMemberAudit:
    name: str
    kind: str
    capability: str | None
    requires: tuple[str, ...]
    status: str
    expected_signature: str | None
    actual_signature: str | None
    evidence: ContractEvidence | None


@dataclass(frozen=True)
class RuntimeHostAudit:
    component_id: str
    api_version: int
    assembly: str
    public_type: str
    source: str
    status: str
    members: tuple[RuntimeMemberAudit, ...]


@dataclass(frozen=True)
class RuntimeApiCall:
    member: str
    capability: str | None
    status: str
    evidence: ContractEvidence


@dataclass(frozen=True)
class RuntimePackageAudit:
    component_id: str
    package_id: str
    relation: str
    provider_component_id: str
    manifest: str
    version: str
    api_version: int | None
    status: str
    capabilities: tuple[str, ...]
    runtime_assemblies: tuple[str, ...]
    entry_points: tuple[str, ...]
    entry_point_sources: tuple[str, ...]
    interfaces: tuple[str, ...]
    api_calls: tuple[RuntimeApiCall, ...]
    settings: tuple[str, ...]
    requirements: tuple[str, ...]
    workbench_relationships: tuple[str, ...]
    project_references: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeContractReport:
    schema_version: int
    hosts: tuple[RuntimeHostAudit, ...]
    packages: tuple[RuntimePackageAudit, ...]
    findings: tuple[RuntimeContractFinding, ...]

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "valid": self.valid,
            "summary": {
                "hosts": len(self.hosts),
                "packages": len(self.packages),
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "hosts": [asdict(item) for item in self.hosts],
            "packages": [asdict(item) for item in self.packages],
            "findings": [asdict(item) for item in self.findings],
        }


def _is_under(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return path == parent or parent in path.parents


def _owned(path: str, component: object) -> bool:
    relative = PurePosixPath(path.replace("\\", "/"))
    return any(_is_under(relative, declared) for declared in component.paths)


def _evidence(path: str, text: str, offset: int) -> ContractEvidence:
    line = text.count("\n", 0, offset) + 1
    excerpt = text.splitlines()[line - 1].strip() if text.splitlines() else ""
    return ContractEvidence(path, line, excerpt[:240])


def _declaration_evidence(
    symbol: RuntimeApiSymbol, path: str, text: str,
) -> ContractEvidence | None:
    escaped = re.escape(symbol.name)
    patterns = {
        "constant": rf"\bpublic\s+const\b[^;\n]*\b{escaped}\b",
        "property": rf"\bpublic\s+static\b[^\n]*\b{escaped}\b",
        "method": rf"\bpublic\s+static\b[^\n]*\b{escaped}\s*\(",
        "interface": rf"\bpublic\s+interface\s+{escaped}\b",
        "type": rf"\bpublic\s+(?:(?:sealed|static|readonly)\s+)*(?:class|struct|record)\s+{escaped}\b",
    }
    match = re.search(patterns[symbol.kind], text)
    return _evidence(path, text, match.start()) if match else None


def _normalize_csharp(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _split_parameters(value: str) -> tuple[str, ...]:
    """Split a C# parameter list without breaking generic type arguments."""
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in "<([{":
            depth += 1
        elif character in ">)]}":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            items.append(value[start:index])
            start = index + 1
    items.append(value[start:])
    return tuple(item for item in items if item.strip())


def _expected_signature(symbol: RuntimeApiSymbol) -> str | None:
    if symbol.kind == "constant":
        return f"{symbol.return_type} {symbol.name} = {symbol.value}"
    if symbol.kind == "property":
        return f"{symbol.return_type} {symbol.name}"
    if symbol.kind == "method":
        parameters = ", ".join(
            f"{item.parameter_type} {item.name}"
            + (f" = {item.default}" if item.optional else "")
            for item in symbol.parameters
        )
        return f"{symbol.return_type} {symbol.name}({parameters})"
    return None


def _actual_signature(symbol: RuntimeApiSymbol, text: str) -> str | None:
    name = re.escape(symbol.name)
    if symbol.kind == "constant":
        match = re.search(
            rf"\bpublic\s+const\s+([A-Za-z_][A-Za-z0-9_.<>,?\[\]]*)\s+"
            rf"{name}\s*=\s*(.*?)\s*;",
            text, re.DOTALL,
        )
        if match:
            return f"{match.group(1)} {symbol.name} = {_normalize_csharp(match.group(2))}"
    elif symbol.kind == "property":
        match = re.search(
            rf"\bpublic\s+static\s+([A-Za-z_][A-Za-z0-9_.<>,?\[\]]*)\s+"
            rf"{name}\s*(?:=>|\{{)",
            text,
        )
        if match:
            return f"{match.group(1)} {symbol.name}"
    elif symbol.kind == "method":
        match = re.search(
            rf"\bpublic\s+static\s+([A-Za-z_][A-Za-z0-9_.<>,?\[\]]*)\s+"
            rf"{name}\s*\((.*?)\)\s*(?:\{{|=>)",
            text, re.DOTALL,
        )
        if match:
            parameters = ", ".join(
                _normalize_csharp(item)
                for item in _split_parameters(match.group(2))
            )
            return f"{match.group(1)} {symbol.name}({parameters})"
    return None


def _public_api_member_names(text: str, type_name: str) -> set[str]:
    """Return public static/constant members declared on one C# class body."""
    declaration = re.search(
        rf"\bpublic\s+static\s+class\s+{re.escape(type_name)}\b[^{{]*\{{",
        text,
    )
    if declaration is None:
        return set()
    start = declaration.end() - 1
    depth = 0
    end = len(text)
    for offset in range(start, len(text)):
        if text[offset] == "{":
            depth += 1
        elif text[offset] == "}":
            depth -= 1
            if depth == 0:
                end = offset
                break
    body = text[start + 1:end]
    values = set(re.findall(
        r"\bpublic\s+const\s+[^;]+?\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
        body,
    ))
    values.update(re.findall(
        r"\bpublic\s+static\s+[^{};=]+?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        body,
    ))
    values.update(re.findall(
        r"\bpublic\s+static\s+[^{};=()]+?\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=>|\{)",
        body,
    ))
    return values


def _status(findings: Iterable[RuntimeContractFinding], component_id: str) -> str:
    relevant = [item for item in findings if item.component_id == component_id]
    if any(item.severity == "error" for item in relevant):
        return "error"
    if any(item.severity == "warning" for item in relevant):
        return "warning"
    return "verified"


def _source_files(
    root: Path, component: object, inventory: object,
) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    total = 0
    for entry in inventory.entries:
        if not entry.path.casefold().endswith((".cs", ".csproj")):
            continue
        if not _owned(entry.path, component):
            continue
        source = root.joinpath(*PurePosixPath(entry.path).parts)
        if not source.is_file() or source.stat().st_size > MAX_CONTRACT_SOURCE_BYTES:
            continue
        size = source.stat().st_size
        if total + size > MAX_CONSUMER_SOURCE_BYTES:
            break
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        total += size
        values.append((entry.path, text))
    return tuple(values)


def _entry_point_source(
    entry_point: str, sources: tuple[tuple[str, str], ...],
) -> str | None:
    if "." not in entry_point:
        return None
    namespace, class_name = entry_point.rsplit(".", 1)
    namespace_pattern = re.compile(rf"\bnamespace\s+{re.escape(namespace)}\b")
    class_pattern = re.compile(rf"\bclass\s+{re.escape(class_name)}\b")
    for path, text in sources:
        if namespace_pattern.search(text) and class_pattern.search(text):
            return path
    return None


def _project_references(
    root: Path, sources: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    references: list[str] = []
    pattern = re.compile(
        r"<ProjectReference\s+Include\s*=\s*[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    )
    for path, text in sources:
        if not path.casefold().endswith(".csproj"):
            continue
        parent = root.joinpath(*PurePosixPath(path).parts).parent
        for value in pattern.findall(text):
            try:
                resolved = (parent / value).resolve(strict=False)
                references.append(resolved.relative_to(root.resolve()).as_posix())
            except ValueError:
                references.append(value.replace("\\", "/"))
    return tuple(dict.fromkeys(references))


def _setting_calls(
    sources: tuple[tuple[str, str], ...], contract: RuntimeApiContract,
) -> tuple[tuple[str, str, ContractEvidence], ...]:
    type_name = re.escape(contract.public_type.rsplit(".", 1)[1])
    pattern = re.compile(
        rf"\b{type_name}\s*\.\s*"
        r"(GetBooleanSetting|GetStringSetting|GetIntegerSetting|GetNumberSetting)"
        r"\s*\(\s*[^,]+,\s*\"([^\"]+)\"",
        re.MULTILINE,
    )
    calls: list[tuple[str, str, ContractEvidence]] = []
    for path, text in sources:
        for match in pattern.finditer(text):
            calls.append((
                match.group(1), match.group(2),
                _evidence(path, text, match.start(1)),
            ))
    return tuple(calls)


def _api_calls(
    sources: tuple[tuple[str, str], ...], contract: RuntimeApiContract,
) -> tuple[RuntimeApiCall, ...]:
    type_name = re.escape(contract.public_type.rsplit(".", 1)[1])
    pattern = re.compile(
        rf"\b{type_name}\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)"
    )
    calls: list[RuntimeApiCall] = []
    seen: set[tuple[str, str, int]] = set()
    for path, text in sources:
        for match in pattern.finditer(text):
            name = match.group(1)
            evidence = _evidence(path, text, match.start(1))
            key = (name, path.casefold(), evidence.line)
            if key in seen:
                continue
            seen.add(key)
            symbol = contract.symbol_map.get(name)
            calls.append(RuntimeApiCall(
                name,
                symbol.capability if symbol else None,
                "verified" if symbol else "unknown",
                evidence,
            ))
    return tuple(calls)


def audit_runtime_contracts(workspace: object, inventory: object) -> RuntimeContractReport:
    """Audit declared runtime relationships using only bounded source evidence."""
    root = workspace.descriptor.parent
    components = {item.component_id: item for item in workspace.components}
    findings: list[RuntimeContractFinding] = []
    hosts: list[RuntimeHostAudit] = []
    contracts: dict[str, RuntimeApiContract] = {}

    for component in workspace.components:
        contract_path = getattr(component, "api_contract", None)
        if contract_path is None:
            continue
        relative_contract = contract_path.as_posix()
        try:
            contract = RuntimeApiContract.load(root.joinpath(*contract_path.parts))
            contracts[component.component_id] = contract
        except (OSError, ValueError) as exc:
            findings.append(RuntimeContractFinding(
                "error", "runtime_contract_invalid", str(exc),
                component.component_id, relative_contract,
            ))
            continue
        if component.runtime_artifact != contract.assembly:
            findings.append(RuntimeContractFinding(
                "error", "runtime_contract_assembly_mismatch",
                "The host contract assembly does not match the workspace runtime artifact.",
                component.component_id, relative_contract,
            ))
        source_path = contract.source.as_posix()
        source = root.joinpath(*contract.source.parts)
        text = ""
        if not _owned(source_path, component):
            findings.append(RuntimeContractFinding(
                "error", "runtime_contract_source_not_owned",
                "The host API source is outside the runtime component's declared paths.",
                component.component_id, source_path,
            ))
        if not source.is_file() or source.stat().st_size > MAX_CONTRACT_SOURCE_BYTES:
            findings.append(RuntimeContractFinding(
                "error", "runtime_contract_source_missing",
                "The bounded host API source is missing or exceeds the audit limit.",
                component.component_id, source_path,
            ))
        else:
            try:
                text = source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                findings.append(RuntimeContractFinding(
                    "error", "runtime_contract_source_unreadable", str(exc),
                    component.component_id, source_path,
                ))
        namespace, type_name = contract.public_type.rsplit(".", 1)
        if text and not (
            re.search(rf"\bnamespace\s+{re.escape(namespace)}\b", text)
            and re.search(rf"\bpublic\s+static\s+class\s+{re.escape(type_name)}\b", text)
        ):
            findings.append(RuntimeContractFinding(
                "error", "runtime_contract_public_type_missing",
                f"Declared public type {contract.public_type} was not found in host source.",
                component.component_id, source_path,
            ))
        version_match = re.search(
            r"\bpublic\s+const\s+int\s+ApiVersion\s*=\s*(\d+)\s*;", text,
        ) if text else None
        if version_match is None or int(version_match.group(1)) != contract.api_version:
            findings.append(RuntimeContractFinding(
                "error", "runtime_contract_version_drift",
                "The checked-in API version does not match the host source constant.",
                component.component_id, source_path,
            ))
        members: list[RuntimeMemberAudit] = []
        for symbol in contract.symbols:
            evidence = _declaration_evidence(symbol, source_path, text) if text else None
            expected_signature = _expected_signature(symbol)
            actual_signature = _actual_signature(symbol, text) if text else None
            if evidence is None:
                findings.append(RuntimeContractFinding(
                    "error", "runtime_contract_symbol_missing",
                    f"Declared host symbol {symbol.name} was not found as a public {symbol.kind}.",
                    component.component_id, source_path,
                ))
            elif (
                expected_signature is not None
                and actual_signature != expected_signature
            ):
                findings.append(RuntimeContractFinding(
                    "error", "runtime_contract_signature_mismatch",
                    f"{symbol.name} signature drifted: expected "
                    f"{expected_signature!r}, found {actual_signature!r}.",
                    component.component_id, source_path, evidence,
                ))
            members.append(RuntimeMemberAudit(
                symbol.name, symbol.kind, symbol.capability, symbol.requires,
                (
                    "verified" if evidence and (
                        expected_signature is None
                        or actual_signature == expected_signature
                    ) else "mismatch" if evidence else "missing"
                ),
                expected_signature, actual_signature, evidence,
            ))
        declared_members = {
            symbol.name for symbol in contract.symbols
            if symbol.kind in {"constant", "property", "method"}
        }
        for undeclared in sorted(
            _public_api_member_names(text, type_name) - declared_members,
            key=str.casefold,
        ):
            findings.append(RuntimeContractFinding(
                "error", "runtime_contract_symbol_undeclared",
                f"Public host member {undeclared} is missing from the checked-in API contract.",
                component.component_id, source_path,
            ))
        hosts.append(RuntimeHostAudit(
            component.component_id, contract.api_version,
            contract.assembly.as_posix(), contract.public_type, source_path,
            _status(findings, component.component_id), tuple(members),
        ))

    packages: list[RuntimePackageAudit] = []
    for relationship in workspace.relationships:
        if relationship.relation not in {"uses_shared_runtime", "integrates_with_api"}:
            continue
        component = components[relationship.source]
        provider = components[relationship.target]
        contract = contracts.get(provider.component_id)
        if contract is None:
            # API-contract auditing is opt-in at the runtime component. Older
            # and third-party product descriptors remain valid evidence maps
            # until their host declares a machine-readable contract.
            continue
        manifest_path = component.content_manifest or component.manifest
        if manifest_path is None:
            findings.append(RuntimeContractFinding(
                "error", "api_contract_manifest_missing",
                "The API consumer does not declare a content manifest.",
                component.component_id,
            ))
            continue
        extension: ExtensionManifest | None = None
        requirements: tuple[str, ...] = ()
        try:
            if component.role == "optional_package":
                package = ModManifest.load(
                    root.joinpath(*component.manifest.parts), validate_payload=True,
                )
                extension = package.extension.descriptor if package.extension else None
                requirements = tuple(str(item) for item in package.package_requirements)
                if extension is None:
                    raise ValueError("schema-v2 API integration requires [allin1] content")
            else:
                extension = ExtensionManifest.load(root.joinpath(*manifest_path.parts))
        except (OSError, ValueError) as exc:
            findings.append(RuntimeContractFinding(
                "error", "api_contract_package_invalid", str(exc),
                component.component_id, manifest_path.as_posix(),
            ))
            packages.append(RuntimePackageAudit(
                component_id=component.component_id,
                package_id=component.package_id or "",
                relation=relationship.relation,
                provider_component_id=provider.component_id,
                manifest=manifest_path.as_posix(),
                version="",
                api_version=None,
                status="error",
                capabilities=(),
                runtime_assemblies=(),
                entry_points=(),
                entry_point_sources=(),
                interfaces=(),
                api_calls=(),
                settings=(),
                requirements=requirements,
                workbench_relationships=(),
                project_references=(),
            ))
            continue
        assert extension is not None
        if extension.extension_id != component.package_id:
            findings.append(RuntimeContractFinding(
                "error", "api_contract_package_id_mismatch",
                "The content descriptor ID does not match the workspace package ID.",
                component.component_id, manifest_path.as_posix(),
            ))
        if extension.api_version != contract.api_version:
            findings.append(RuntimeContractFinding(
                "error", "api_contract_version_mismatch",
                f"Package API {extension.api_version} is incompatible with host API {contract.api_version}.",
                component.component_id, manifest_path.as_posix(),
            ))
        if (
            component.role == "official_content_pack"
            and extension.version != workspace.version
        ):
            findings.append(RuntimeContractFinding(
                "error", "api_contract_builtin_version_mismatch",
                f"Built-in package version {extension.version} does not match workspace {workspace.version}.",
                component.component_id, manifest_path.as_posix(),
            ))
        assembly_paths = tuple(item.path.as_posix() for item in extension.runtime_assemblies)
        if relationship.relation == "uses_shared_runtime" and contract.assembly.as_posix() not in assembly_paths:
            findings.append(RuntimeContractFinding(
                "error", "api_contract_shared_runtime_missing",
                f"Package does not declare the shared runtime assembly {contract.assembly}.",
                component.component_id, manifest_path.as_posix(),
            ))
        sources = _source_files(root, component, inventory)
        entry_points = tuple(
            item.entry_point for item in extension.runtime_assemblies if item.entry_point
        )
        entry_sources: list[str] = []
        for entry_point in entry_points:
            source_path = _entry_point_source(entry_point, sources)
            if source_path is None:
                findings.append(RuntimeContractFinding(
                    "error", "api_contract_entry_point_missing",
                    f"Entry point {entry_point} was not found in bounded component source.",
                    component.component_id, manifest_path.as_posix(),
                ))
            else:
                entry_sources.append(source_path)
        calls = _api_calls(sources, contract)
        capability_set = set(extension.capabilities)
        for call in calls:
            symbol = contract.symbol_map.get(call.member)
            if symbol is None:
                findings.append(RuntimeContractFinding(
                    "error", "api_contract_unknown_member",
                    f"Consumer calls undeclared API member {call.member}.",
                    component.component_id, call.evidence.path, call.evidence,
                ))
                continue
            if symbol.capability and symbol.capability not in capability_set:
                findings.append(RuntimeContractFinding(
                    "error", "api_contract_capability_missing",
                    f"Calling {call.member} requires capability {symbol.capability}.",
                    component.component_id, call.evidence.path, call.evidence,
                ))
            for required in symbol.requires:
                if not any(re.search(rf"\b{re.escape(required)}\b", text) for _path, text in sources):
                    findings.append(RuntimeContractFinding(
                        "error", "api_contract_interface_missing",
                        f"Calling {call.member} requires implementation of {required}.",
                        component.component_id, call.evidence.path, call.evidence,
                    ))
        interfaces = tuple(
            symbol.name for symbol in contract.symbols
            if symbol.kind == "interface" and any(
                re.search(rf"\b{re.escape(symbol.name)}\b", text)
                for _path, text in sources
            )
        )
        settings = tuple(setting.key for setting in extension.settings)
        settings_by_key = {setting.key: setting for setting in extension.settings}
        getter_types = {
            "GetBooleanSetting": "boolean",
            "GetStringSetting": "string",
            "GetIntegerSetting": "integer",
            "GetNumberSetting": "number",
        }
        for member, key, evidence in _setting_calls(sources, contract):
            setting = settings_by_key.get(key)
            if setting is None:
                findings.append(RuntimeContractFinding(
                    "error", "api_contract_setting_missing",
                    f"Runtime requests undeclared setting {key}.",
                    component.component_id, evidence.path, evidence,
                ))
            elif setting.setting_type != getter_types[member]:
                findings.append(RuntimeContractFinding(
                    "error", "api_contract_setting_type_mismatch",
                    f"{key} is {setting.setting_type}, but runtime uses {member}.",
                    component.component_id, evidence.path, evidence,
                ))
        package_id_matches = []
        for path, text in sources:
            for match in re.finditer(
                r"\bconst\s+string\s+PackageId\s*=\s*\"([^\"]+)\"", text,
            ):
                package_id_matches.append((match.group(1), _evidence(path, text, match.start())))
        for package_id, evidence in package_id_matches:
            if package_id != extension.extension_id:
                findings.append(RuntimeContractFinding(
                    "error", "api_contract_runtime_package_id_mismatch",
                    f"Runtime PackageId {package_id!r} does not match {extension.extension_id!r}.",
                    component.component_id, evidence.path, evidence,
                ))
        project_references = _project_references(root, sources)
        provider_projects = {
            path.as_posix().casefold() for path in provider.paths
            if path.suffix.casefold() == ".csproj"
        }
        if calls and provider_projects and not any(
            value.casefold() in provider_projects for value in project_references
        ):
            findings.append(RuntimeContractFinding(
                "warning", "api_contract_project_reference_missing",
                "Consumer source calls the host API without a bounded ProjectReference to its runtime project.",
                component.component_id,
            ))
        relationships = tuple(
            item.enhancement_id for item in extension.workbench_weapon_enhancements
        )
        if (
            "weapon.components.lifecycle" in capability_set
            and extension.runtime_assemblies
            and not relationships
        ):
            findings.append(RuntimeContractFinding(
                "warning", "api_contract_weapon_relationship_missing",
                "Weapon lifecycle integration is valid, but its weapon/component/visual Workbench relationship is undeclared.",
                component.component_id, manifest_path.as_posix(),
            ))
        packages.append(RuntimePackageAudit(
            component.component_id, extension.extension_id,
            relationship.relation, provider.component_id,
            manifest_path.as_posix(), extension.version, extension.api_version,
            _status(findings, component.component_id), extension.capabilities,
            assembly_paths, entry_points, tuple(entry_sources), interfaces,
            calls, settings, requirements, relationships, project_references,
        ))

    # Hosts were constructed before package findings were known, so their
    # status reflects only their own source/contract verification.
    return RuntimeContractReport(
        RUNTIME_CONTRACT_REPORT_SCHEMA, tuple(hosts), tuple(packages),
        tuple(findings),
    )


__all__ = [
    "ContractEvidence",
    "RUNTIME_CONTRACT_REPORT_SCHEMA",
    "RuntimeApiCall",
    "RuntimeContractFinding",
    "RuntimeContractReport",
    "RuntimeHostAudit",
    "RuntimeMemberAudit",
    "RuntimePackageAudit",
    "audit_runtime_contracts",
]

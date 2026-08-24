"""Declarative linker and diagnostics for GTA V add-on content integrations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
SUPPORTED_NODE_KINDS = frozenset({
    "weapon", "weapon_component", "ammo", "animation", "text_label", "hud_alias",
    "runtime", "storefront", "archive", "package", "vehicle",
    "handling", "vehicle_variation", "tuning", "ped", "streaming",
    "dlc_registration", "script_plugin", "asi_plugin", "reshade_addon",
    "replacement",
})
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "weapon": ("Name", "Slot", "AmmoInfo", "Model", "HumanNameHash", "StatName"),
    "weapon_component": (
        "Names", "Models", "AttachBones", "ComponentTypes",
    ),
    "ammo": ("Name", "Model", "AmmoMax", "AmmoMax50", "Explosion", "TrailFx", "PrimedFx"),
    "animation": ("WeaponNames", "Template", "Sets"),
    "text_label": ("Labels", "Archive", "Entry"),
    "hud_alias": ("SourceWeaponNames", "FrameTemplate", "Archive", "Entry"),
    "runtime": ("WeaponNames", "Controller", "Persistence"),
    "storefront": ("WeaponNames", "Catalog", "Persistence"),
    "archive": ("Path", "Entry", "MergeStrategy", "Backup"),
    "package": ("Registration", "Edition", "Safety"),
    "vehicle": (
        "ModelName", "TxdName", "HandlingId", "GameName", "MakeName",
        "AudioNameHash", "Layout", "Type", "Class",
    ),
    "handling": ("HandlingNames",),
    "vehicle_variation": ("ModelNames", "Kits", "LightSettings"),
    "tuning": ("VehicleModels", "KitNames", "ModelNames", "KitIds"),
    "ped": (
        "Names", "PedTypes", "ModelTypes", "PropsNames",
        "ClipDictionaries", "ExpressionSets", "MovementClipSets",
    ),
    "streaming": ("ModelNames", "TextureNames", "Assets"),
    "dlc_registration": (
        "VehicleModels", "PackageNames", "MetadataFiles", "Registration",
        "Edition",
    ),
    "script_plugin": (
        "Binaries", "Configuration", "CompanionAssets", "DependencyHints",
        "InstallRoot",
    ),
    "asi_plugin": (
        "Binaries", "Configuration", "CompanionAssets", "DependencyHints",
        "InstallRoot",
    ),
    "reshade_addon": ("Binaries", "Shaders", "Configuration", "InstallRoot"),
    "replacement": (
        "Assets", "TargetArchives", "Editions", "MergeStrategy", "Backup",
    ),
}
WEAPON_RELATIONSHIPS = frozenset({
    "uses_ammo", "uses_animation", "uses_label", "uses_hud_icon",
    "handled_by_runtime", "sold_by",
})
VEHICLE_RELATIONSHIPS = frozenset({
    "uses_handling", "uses_variation", "streams_model", "registered_by",
})
FIELD_HELP: dict[str, str] = {
    "Name": "The exact game-facing identifier. References and hashes are case-sensitive in authored metadata.",
    "Slot": "Weapon-wheel slot identifier. Unique slots create independent wheel entries and ammo selection.",
    "AmmoInfo": "Reference to the ammo definition used by this weapon.",
    "Model": "The streamed prop/model used when the item is held or thrown.",
    "HumanNameHash": "GXT label key used for the native weapon-wheel name.",
    "StatName": "Stats/UI lookup key. It does not choose weapon-wheel artwork.",
    "Names": "Game-facing identifiers covered by this component or ped definition group.",
    "Models": "Streamed model identifiers referenced by these definitions.",
    "AttachBones": "Skeleton attachment points used by weapon components.",
    "ComponentTypes": "Native weapon-component record types represented by the package.",
    "AmmoMax": "Maximum ammo count used by the native inventory.",
    "AmmoMax50": "Alternate maximum used by the 50-percent ammo-cap profile.",
    "Explosion": "Native explosion tag. Leave empty when a runtime controller owns deployment.",
    "TrailFx": "Projectile trail particle. Clear it when it would mix an unwanted native colour.",
    "PrimedFx": "Particle effect shown while the throwable is primed.",
    "WeaponNames": "Weapon identifiers consumed by this shared integration stage.",
    "Template": "Native record cloned to retain compatible animation or UI behavior.",
    "Sets": "Animation sets that require a mapping for the custom weapon hash.",
    "Labels": "Native GXT keys and their displayed text.",
    "Archive": "Containing RPF archive that must be merged rather than blindly replaced.",
    "Entry": "Entry inside the containing archive.",
    "SourceWeaponNames": "Custom weapon hashes that receive a native HUD frame alias.",
    "FrameTemplate": "Native Scaleform frame whose artwork is reused.",
    "Controller": "Runtime script responsible for behavior the metadata cannot express safely.",
    "Persistence": "Save boundary for purchases, ammo, and runtime state.",
    "Catalog": "Storefront or catalog that exposes the item to the player.",
    "Path": "Game-relative path of the archive or metadata file.",
    "MergeStrategy": "How the authored data is combined with the current game build.",
    "Backup": "Rollback material required before a game archive is changed.",
    "Registration": "Loader or dlclist registration required for the package.",
    "Edition": "Supported GTA V edition or editions.",
    "Safety": "Boot, rollback, and validation constraints for the integration.",
    "ExpectedFrames": "Signed JOAAT-derived Scaleform labels: INT<signed weapon hash>.",
    "ModelName": "Spawn model identifier from vehicles.meta; it must resolve to streamed model assets.",
    "TxdName": "Texture dictionary identifier used by the vehicle model.",
    "HandlingId": "Reference from vehicles.meta to a handling.meta handlingName.",
    "GameName": "Game-facing vehicle label/hash key.",
    "MakeName": "Manufacturer label/hash key displayed by native UI where available.",
    "AudioNameHash": "Native or custom engine-audio profile used by the vehicle.",
    "Layout": "Seat, entry, and occupant layout used by the vehicle skeleton.",
    "Type": "Native vehicle type such as VEHICLE_TYPE_CAR, BOAT, HELI, or PLANE.",
    "Class": "Native vehicle class used by spawning and UI categorization.",
    "HandlingNames": "handlingName identifiers authored in handling.meta.",
    "ModelNames": "Model identifiers covered by this metadata or streamed-asset stage.",
    "TextureNames": "Texture dictionary identifiers covered by streamed .ytd assets.",
    "Kits": "Tuning kit identifiers selected by carvariations.meta.",
    "KitNames": "Tuning kit definitions authored in carcols.meta.",
    "KitIds": "Numeric mod-kit IDs; they must not collide with another loaded kit.",
    "LightSettings": "Vehicle light-setting IDs linked through carvariations/carcols metadata.",
    "Assets": "Package-relative streamed model and texture files.",
    "VehicleModels": "Vehicle models covered by this tuning or package-registration stage.",
    "TuningModels": "Vehicle models that declare one or more package-defined tuning kits.",
    "PedTypes": "Native population/behavior category declared by peds.meta.",
    "ModelTypes": "Native ped model classification declared by peds.meta.",
    "PropsNames": "Prop-definition identifier paired with the ped model.",
    "ClipDictionaries": "Animation clip dictionaries referenced by ped definitions.",
    "ExpressionSets": "Facial expression sets referenced by ped definitions.",
    "MovementClipSets": "Movement animation sets referenced by ped definitions.",
    "PackageNames": "DLC device/folder names used by content.xml, setup2.xml, dlclist, or a resource manifest.",
    "MetadataFiles": "Metadata files declared by the package registration layer.",
    "Binaries": "Compiled plug-ins inventoried without loading or executing them.",
    "Configuration": "Configuration files distributed with the plug-in or content pack.",
    "CompanionAssets": "Non-binary assets that must follow the plug-in's installation layout.",
    "DependencyHints": "Dependencies inferred from package documentation; the author must confirm versions and edition compatibility.",
    "InstallRoot": "Game-relative destination root. Imported drafts leave this unresolved until the author confirms it.",
    "Shaders": "ReShade or other shader-host assets included by the package.",
    "TargetArchives": "Current-build RPF archives inferred from package layout or documentation.",
    "Editions": "Legacy/Enhanced payload hints inferred from directory names; they are not compatibility proof.",
    "Architecture": "PE machine type read from the compiled plug-in header without loading the binary; GTA V plug-ins must be x64.",
    "Managed": "A bounded header scan found .NET metadata. This is a hint, not execution or dependency verification.",
}
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")


def joaat(value: str) -> int:
    """Return Rockstar's lowercase Jenkins one-at-a-time hash."""
    result = 0
    for byte in value.lower().encode("utf-8"):
        result = (result + byte) & 0xFFFFFFFF
        result = (result + (result << 10)) & 0xFFFFFFFF
        result ^= result >> 6
    result = (result + (result << 3)) & 0xFFFFFFFF
    result ^= result >> 11
    result = (result + (result << 15)) & 0xFFFFFFFF
    return result


def signed_hash(value: str) -> int:
    hashed = joaat(value)
    return hashed if hashed < 0x80000000 else hashed - 0x100000000


def hud_frame_label(weapon_name: str) -> str:
    return f"INT{signed_hash(weapon_name)}"


def _clean_identifier(value: object, label: str) -> str:
    identifier = str(value or "").strip().lower()
    if not _ID_PATTERN.fullmatch(identifier):
        raise ValueError(f"{label} must be a 2-96 character lowercase identifier")
    return identifier


def _contained_source(root: Path, source: str) -> Path:
    normalized = source.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError(f"Source must be a non-empty relative path: {source!r}")
    candidate = (root / normalized).resolve(strict=False)
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"Source escapes the SDK root: {source}")
    return candidate


@dataclass(frozen=True)
class AddonNode:
    node_id: str
    kind: str
    label: str
    description: str
    source: str | None
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class AddonReference:
    reference_id: str
    source: str
    source_field: str
    target: str
    target_field: str
    relationship: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class AddonInstallStep:
    step_id: str
    order: int
    title: str
    target: str
    strategy: str
    source: str | None
    description: str


@dataclass(frozen=True)
class AddonManifest:
    manifest_path: Path
    source_root: Path
    addon_id: str
    name: str
    version: str
    summary: str
    editions: tuple[str, ...]
    nodes: tuple[AddonNode, ...]
    references: tuple[AddonReference, ...]
    install_steps: tuple[AddonInstallStep, ...]
    catalog_state: str = "Built-in example"
    catalog_origin: str = "built-in"
    package_source: Path | None = None

    @classmethod
    def load(
        cls, manifest_path: str | Path, *, source_root: str | Path | None = None
    ) -> "AddonManifest":
        path = Path(manifest_path).resolve()
        if path.is_dir():
            path = path / "addon.json"
        if not path.is_file():
            raise FileNotFoundError(f"SDK manifest not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid SDK JSON: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"addon.json schema_version must be {SCHEMA_VERSION}")

        addon_id = _clean_identifier(data.get("id"), "Add-on id")
        name = str(data.get("name", "")).strip()
        version = str(data.get("version", "")).strip()
        if not name or not version:
            raise ValueError("Add-on name and version are required")
        raw_editions = data.get("editions", [])
        if (not isinstance(raw_editions, list) or not raw_editions
                or not all(value in {"legacy", "enhanced"} for value in raw_editions)):
            raise ValueError("editions must contain 'legacy' and/or 'enhanced'")

        root = Path(source_root).resolve() if source_root else path.parent.resolve()
        nodes: list[AddonNode] = []
        node_ids: set[str] = set()
        for index, raw in enumerate(data.get("nodes", []), start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"nodes[{index}] must be an object")
            node_id = _clean_identifier(raw.get("id"), f"nodes[{index}].id")
            if node_id in node_ids:
                raise ValueError(f"Duplicate node id: {node_id}")
            node_ids.add(node_id)
            kind = str(raw.get("kind", "")).strip().lower()
            if kind not in SUPPORTED_NODE_KINDS:
                raise ValueError(f"Unsupported node kind '{kind}'")
            fields = raw.get("fields")
            if not isinstance(fields, dict):
                raise ValueError(f"Node '{node_id}' fields must be an object")
            source = raw.get("source")
            if source is not None:
                if not isinstance(source, str):
                    raise ValueError(f"Node '{node_id}' source must be a relative path")
                _contained_source(root, source)
            nodes.append(AddonNode(
                node_id, kind, str(raw.get("label", node_id)).strip() or node_id,
                str(raw.get("description", "")).strip(), source, fields,
            ))
        if not nodes:
            raise ValueError("SDK manifest must contain at least one node")

        references: list[AddonReference] = []
        reference_ids: set[str] = set()
        for index, raw in enumerate(data.get("references", []), start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"references[{index}] must be an object")
            reference_id = _clean_identifier(raw.get("id"), f"references[{index}].id")
            if reference_id in reference_ids:
                raise ValueError(f"Duplicate reference id: {reference_id}")
            reference_ids.add(reference_id)
            references.append(AddonReference(
                reference_id,
                str(raw.get("source", "")).strip().lower(),
                str(raw.get("source_field", "")).strip(),
                str(raw.get("target", "")).strip().lower(),
                str(raw.get("target_field", "")).strip(),
                str(raw.get("relationship", "")).strip().lower(),
                str(raw.get("description", "")).strip(),
                bool(raw.get("required", True)),
            ))

        steps: list[AddonInstallStep] = []
        step_ids: set[str] = set()
        for index, raw in enumerate(data.get("install_steps", []), start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"install_steps[{index}] must be an object")
            step_id = _clean_identifier(raw.get("id"), f"install_steps[{index}].id")
            if step_id in step_ids:
                raise ValueError(f"Duplicate install step id: {step_id}")
            step_ids.add(step_id)
            source = raw.get("source")
            if source is not None:
                if not isinstance(source, str):
                    raise ValueError(f"Install step '{step_id}' source must be a path")
                _contained_source(root, source)
            steps.append(AddonInstallStep(
                step_id, int(raw.get("order", index)),
                str(raw.get("title", step_id)).strip(),
                str(raw.get("target", "")).strip(),
                str(raw.get("strategy", "")).strip(), source,
                str(raw.get("description", "")).strip(),
            ))
        steps.sort(key=lambda item: (item.order, item.step_id))
        return cls(
            path, root, addon_id, name, version,
            str(data.get("summary", "")).strip(),
            tuple(dict.fromkeys(raw_editions)), tuple(nodes),
            tuple(references), tuple(steps),
        )

    @property
    def node_map(self) -> dict[str, AddonNode]:
        return {node.node_id: node for node in self.nodes}


@dataclass(frozen=True)
class AddonIssue:
    severity: str
    code: str
    message: str
    subject: str | None = None


@dataclass(frozen=True)
class LinkedReference:
    reference: AddonReference
    valid: bool
    source_value: Any = None
    target_value: Any = None
    message: str = ""


@dataclass(frozen=True)
class AddonLinkReport:
    manifest: AddonManifest
    issues: tuple[AddonIssue, ...]
    references: tuple[LinkedReference, ...]

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)

    def to_markdown(self) -> str:
        state = "PASS" if self.valid else "FAIL"
        lines = [
            f"# {self.manifest.name} — linked integration report",
            "",
            f"- Result: **{state}**",
            f"- Manifest: `{self.manifest.manifest_path}`",
            f"- Nodes: {len(self.manifest.nodes)}",
            f"- References: {sum(item.valid for item in self.references)}/{len(self.references)} resolved",
            f"- Install steps: {len(self.manifest.install_steps)}",
            "",
            "## Diagnostics",
            "",
        ]
        if self.issues:
            lines.extend(
                f"- **{issue.severity.upper()} {issue.code}**"
                f"{f' (`{issue.subject}`)' if issue.subject else ''}: {issue.message}"
                for issue in self.issues
            )
        else:
            lines.append("- No issues.")
        lines.extend(["", "## Install plan", ""])
        for step in self.manifest.install_steps:
            lines.append(
                f"{step.order}. **{step.title}** — `{step.target}` ({step.strategy})"
            )
            if step.description:
                lines.append(f"   {step.description}")
        lines.extend(["", "## Linked references", ""])
        for linked in self.references:
            mark = "✓" if linked.valid else "✗"
            ref = linked.reference
            lines.append(
                f"- {mark} `{ref.source}.{ref.source_field}` → "
                f"`{ref.target}.{ref.target_field}` ({ref.relationship})"
            )
        return "\n".join(lines) + "\n"


class AddonLinker:
    """Resolve an SDK manifest without making any game or archive changes."""

    def link(self, manifest: AddonManifest) -> AddonLinkReport:
        issues: list[AddonIssue] = []
        linked: list[LinkedReference] = []
        nodes = manifest.node_map

        for node in manifest.nodes:
            for field in REQUIRED_FIELDS[node.kind]:
                if field not in node.fields:
                    issues.append(AddonIssue(
                        "error", "missing_field",
                        f"{node.kind} nodes require '{field}'.", node.node_id,
                    ))
            if node.source:
                source = _contained_source(manifest.source_root, node.source)
                if not source.is_file():
                    issues.append(AddonIssue(
                        "error", "missing_source",
                        f"Source file does not exist: {node.source}", node.node_id,
                    ))
            if node.kind == "hud_alias":
                names = node.fields.get("SourceWeaponNames", [])
                expected = node.fields.get("ExpectedFrames", {})
                if not isinstance(names, list) or not isinstance(expected, dict):
                    issues.append(AddonIssue(
                        "error", "invalid_hud_alias",
                        "HUD aliases require SourceWeaponNames[] and ExpectedFrames{}.",
                        node.node_id,
                    ))
                else:
                    for name in names:
                        if expected.get(name) != hud_frame_label(str(name)):
                            issues.append(AddonIssue(
                                "error", "hud_hash_mismatch",
                                f"ExpectedFrames[{name!r}] must be {hud_frame_label(str(name))}.",
                                node.node_id,
                            ))
            if node.kind == "package" and str(
                node.fields.get("Safety", "")
            ).casefold().startswith("draft only"):
                issues.append(AddonIssue(
                    "error", "imported_draft_requires_review",
                    "Imported drafts remain review-only until package targets, "
                    "dependencies, verification, and rollback are explicitly authored.",
                    node.node_id,
                ))

        for reference in manifest.references:
            source = nodes.get(reference.source)
            target = nodes.get(reference.target)
            if source is None or target is None:
                missing = reference.source if source is None else reference.target
                message = f"Referenced node does not exist: {missing}"
                linked.append(LinkedReference(reference, False, message=message))
                if reference.required:
                    issues.append(AddonIssue(
                        "error", "missing_node", message, reference.reference_id,
                    ))
                continue
            if reference.source_field not in source.fields:
                message = f"Source field does not exist: {reference.source_field}"
                linked.append(LinkedReference(reference, False, message=message))
                if reference.required:
                    issues.append(AddonIssue(
                        "error", "missing_source_field", message,
                        reference.reference_id,
                    ))
                continue
            if reference.target_field not in target.fields:
                message = f"Target field does not exist: {reference.target_field}"
                linked.append(LinkedReference(reference, False, message=message))
                if reference.required:
                    issues.append(AddonIssue(
                        "error", "missing_target_field", message,
                        reference.reference_id,
                    ))
                continue
            source_value = source.fields[reference.source_field]
            target_value = target.fields[reference.target_field]
            if isinstance(target_value, Mapping):
                matches = (
                    all(value in target_value for value in source_value)
                    if isinstance(source_value, (list, tuple, set))
                    else source_value in target_value
                )
            elif isinstance(target_value, (list, tuple, set)):
                matches = (
                    set(source_value).issubset(set(target_value))
                    if isinstance(source_value, (list, tuple, set))
                    else source_value in target_value
                )
            else:
                matches = source_value == target_value
            message = "Reference resolved." if matches else (
                f"Value mismatch: {source_value!r} is not linked to {target_value!r}."
            )
            linked.append(LinkedReference(
                reference, matches, source_value, target_value, message,
            ))
            if not matches and reference.required:
                issues.append(AddonIssue(
                    "error", "reference_mismatch", message,
                    reference.reference_id,
                ))

        relationships: dict[str, set[str]] = {}
        for item in linked:
            if item.valid:
                relationships.setdefault(item.reference.source, set()).add(
                    item.reference.relationship
                )
        for node in manifest.nodes:
            if node.kind == "weapon":
                missing = WEAPON_RELATIONSHIPS - relationships.get(
                    node.node_id, set()
                )
                code = "incomplete_weapon_integration"
            elif node.kind == "vehicle":
                required = set(VEHICLE_RELATIONSHIPS)
                if node.fields.get("TuningKits"):
                    required.add("uses_tuning")
                missing = required - relationships.get(node.node_id, set())
                code = "incomplete_vehicle_integration"
            else:
                continue
            if missing:
                issues.append(AddonIssue(
                    "error", code,
                    "Missing required links: " + ", ".join(sorted(missing)),
                    node.node_id,
                ))

        seen_orders: set[int] = set()
        for step in manifest.install_steps:
            if step.order in seen_orders:
                issues.append(AddonIssue(
                    "warning", "duplicate_step_order",
                    f"Multiple install steps use order {step.order}.", step.step_id,
                ))
            seen_orders.add(step.order)
            if not step.target or not step.strategy:
                issues.append(AddonIssue(
                    "error", "incomplete_install_step",
                    "Install steps require target and strategy.", step.step_id,
                ))
            if step.source:
                source = _contained_source(manifest.source_root, step.source)
                if not source.is_file():
                    issues.append(AddonIssue(
                        "error", "missing_step_source",
                        f"Install-step source does not exist: {step.source}",
                        step.step_id,
                    ))
        return AddonLinkReport(manifest, tuple(issues), tuple(linked))


class AddonSdkCatalog:
    """Discover examples, imported manifests, packages, and install receipts."""

    def __init__(
        self, project_root: str | Path, state_root: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.examples_root = self.project_root / "sdk" / "examples"
        registry_root = (
            Path(state_root).expanduser().resolve()
            if state_root is not None else self.project_root / "sdk"
        )
        self.registry_path = registry_root / "imported_packages.json"

    def remember(
        self, manifest_path: str | Path, *, source_root: str | Path | None = None,
        package_source: str | Path | None = None,
    ) -> AddonManifest:
        """Persist a reference to an imported SDK manifest without copying payloads."""
        path = Path(manifest_path).expanduser().resolve()
        root = Path(source_root).expanduser().resolve() if source_root else path.parent
        package = (
            Path(package_source).expanduser().resolve()
            if package_source else root
        )
        manifest = AddonManifest.load(path, source_root=root)
        records = self._registry_records()
        key = str(path).casefold()
        records = [
            record for record in records
            if str(record.get("manifest", "")).casefold() != key
        ]
        records.append({
            "manifest": str(path), "source_root": str(root),
            "package_source": str(package),
        })
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({
            "schema_version": 1,
            "manifests": records,
        }, indent=2), encoding="utf-8")
        temporary.replace(self.registry_path)
        return replace(
            manifest, catalog_state="Imported draft", catalog_origin="imported",
            package_source=package,
        )

    def _registry_records(self) -> list[dict[str, str]]:
        if not self.registry_path.is_file():
            return []
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            return []
        records = data.get("manifests", [])
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    @staticmethod
    def _manifest_from_mod(
        manifest: object,
        *,
        state: str,
        origin: str,
        installed_root: Path | None = None,
    ) -> AddonManifest:
        files = tuple(getattr(manifest, "files", ()))
        rpf_entries = tuple(getattr(manifest, "rpf_entries", ()))
        editions = tuple(getattr(manifest, "editions", ("legacy", "enhanced")))
        dependencies = tuple(getattr(manifest, "dependencies", ()))
        dlc_packs = tuple(getattr(manifest, "dlc_packs", ()))
        package_root = Path(getattr(manifest, "package_root")).resolve()
        manifest_path = Path(getattr(manifest, "manifest_path")).resolve()
        mod_id = str(getattr(manifest, "mod_id"))
        name = str(getattr(manifest, "name"))
        version = str(getattr(manifest, "version"))
        description = str(getattr(manifest, "description", ""))

        registration = (
            "DLC packs: " + ", ".join(dlc_packs) if dlc_packs
            else "Loaders: " + ", ".join(dependencies) if dependencies
            else "No external registration declared"
        )
        nodes: list[AddonNode] = [AddonNode(
            "package.managed", "package", "Managed package contract",
            "Edition routing, dependencies, install ownership, and rollback boundary.",
            None,
            {
                "Registration": registration,
                "Edition": [value.title() for value in editions],
                "Safety": (
                    "Installed files are receipt-owned and restored from exact backups."
                    if installed_root else
                    "Available manifest; payload is validated before installation."
                ),
            },
        )]

        destinations = [item.destination.as_posix() for item in files]
        script_files = [value for value in destinations if value.casefold().startswith("scripts/")]
        root_plugins = [value for value in destinations if "/" not in value]
        if script_files:
            nodes.append(AddonNode(
                "plugin.script", "script_plugin", "Script package payload", "",
                None, {
                    "Binaries": [value for value in script_files if Path(value).suffix.lower() == ".dll"],
                    "Configuration": [value for value in script_files if Path(value).suffix.lower() in {".ini", ".toml", ".json", ".cfg"}],
                    "CompanionAssets": [value for value in script_files if Path(value).suffix.lower() not in {".dll", ".ini", ".toml", ".json", ".cfg"}],
                    "DependencyHints": list(dependencies),
                    "InstallRoot": "scripts/",
                },
            ))
        if root_plugins:
            nodes.append(AddonNode(
                "plugin.asi", "asi_plugin", "Root plug-in payload", "", None,
                {
                    "Binaries": [value for value in root_plugins if Path(value).suffix.lower() in {".asi", ".dll"}],
                    "Configuration": [value for value in root_plugins if Path(value).suffix.lower() in {".ini", ".toml", ".json", ".cfg"}],
                    "CompanionAssets": [value for value in root_plugins if Path(value).suffix.lower() not in {".asi", ".dll", ".ini", ".toml", ".json", ".cfg"}],
                    "DependencyHints": list(dependencies),
                    "InstallRoot": "GTA V root",
                },
            ))
        archive_targets = [
            value for value in destinations if value.casefold().endswith(".rpf")
        ] + [item.archive.as_posix() for item in rpf_entries]
        if archive_targets:
            nodes.append(AddonNode(
                "content.archives", "replacement", "Archive content", "", None,
                {
                    "Assets": [item.source.as_posix() for item in rpf_entries] + [
                        item.source.as_posix() for item in files
                        if item.destination.suffix.lower() == ".rpf"
                    ],
                    "TargetArchives": list(dict.fromkeys(archive_targets)),
                    "Editions": list(editions),
                    "MergeStrategy": (
                        "Exact manifest-owned entry replacement"
                        if rpf_entries else "Whole manifest-owned add-on archive"
                    ),
                    "Backup": "Exact entry/file backup recorded in the install receipt",
                },
            ))
        if dlc_packs:
            nodes.append(AddonNode(
                "content.registration", "dlc_registration", "DLC registration", "",
                None, {
                    "VehicleModels": [],
                    "PackageNames": list(dlc_packs),
                    "MetadataFiles": archive_targets,
                    "Registration": "Managed dlclist.xml registration",
                    "Edition": list(editions),
                },
            ))

        steps: list[AddonInstallStep] = []
        order = 10
        for item in files:
            steps.append(AddonInstallStep(
                f"file.{order}", order, f"Deploy {item.destination.name}",
                item.destination.as_posix(), "verified manifest-owned file copy",
                None, "Back up any previous destination and record ownership.",
            ))
            order += 10
        for item in rpf_entries:
            steps.append(AddonInstallStep(
                f"rpf.{order}", order, f"Patch {item.entry.name}",
                f"{item.archive.as_posix()}/{item.entry.as_posix()}",
                "verified exact-entry RPF transaction", None,
                "Restore only this entry during disable, rollback, or uninstall.",
            ))
            order += 10
        return AddonManifest(
            manifest_path, package_root, mod_id, name, version, description,
            editions, tuple(nodes), (), tuple(steps), state, origin, package_root,
        )

    @staticmethod
    def _manifest_from_receipt(receipt_path: Path, game_root: Path) -> AddonManifest:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
        mod_id = _clean_identifier(data.get("id"), "Installed package id")
        edition = "enhanced" if (game_root / "GTA5_Enhanced.exe").is_file() else "legacy"
        files = data.get("files", []) if isinstance(data.get("files"), list) else []
        entries = (
            data.get("rpf_entries", [])
            if isinstance(data.get("rpf_entries"), list) else []
        )
        nodes = (AddonNode(
            "package.receipt", "package", "Installed receipt", "", None,
            {
                "Registration": data.get("dlc_packs", []) or data.get("dependencies", []) or "none",
                "Edition": edition.title(),
                "Safety": "Receipt-owned installation with recorded rollback metadata.",
            },
        ),)
        steps: list[AddonInstallStep] = []
        for index, item in enumerate(files + entries, start=1):
            target = str(item.get("destination") or (
                f"{item.get('archive', '')}/{item.get('entry', '')}"
            ))
            steps.append(AddonInstallStep(
                f"receipt.{index}", index * 10, f"Managed target {index}", target,
                "installed receipt ownership", None, "",
            ))
        return AddonManifest(
            receipt_path, receipt_path.parent, mod_id,
            str(data.get("name", mod_id)), str(data.get("version", "unknown")),
            "Installed package reconstructed from its ALLIN1 receipt.",
            (edition,), nodes, (), tuple(steps),
            f"Installed · {edition.title()}", "installed-receipt", receipt_path.parent,
        )

    def _installed_manifests(self, roots: Iterable[str | Path]) -> list[AddonManifest]:
        manifests: list[AddonManifest] = []
        for value in roots:
            game_root = Path(value).expanduser().resolve()
            receipt_root = game_root / "scripts" / ".allin1" / "mods"
            if not receipt_root.is_dir():
                continue
            edition = (
                "Enhanced" if (game_root / "GTA5_Enhanced.exe").is_file()
                else "Legacy"
            )
            for receipt_path in sorted(receipt_root.glob("*.json")):
                try:
                    data = json.loads(receipt_path.read_text(encoding="utf-8"))
                    source = Path(str(data.get("source_manifest", ""))).expanduser()
                    if source.is_file() and source.name.casefold() == "mod.toml":
                        from allin1_sdk.mods import ModManifest
                        mod = ModManifest.load(source, validate_payload=False)
                        manifests.append(self._manifest_from_mod(
                            mod, state=f"Installed · {edition}",
                            origin="installed-receipt", installed_root=game_root,
                        ))
                    else:
                        manifests.append(self._manifest_from_receipt(
                            receipt_path, game_root,
                        ))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        return manifests

    def discover(
        self,
        installation_roots: Iterable[str | Path] = (),
        *,
        include_external: bool = False,
    ) -> list[AddonManifest]:
        manifests: list[AddonManifest] = []
        if self.examples_root.is_dir():
            for path in sorted(self.examples_root.glob("*/addon.json")):
                manifests.append(AddonManifest.load(
                    path, source_root=self.project_root,
                ))
        if not include_external:
            return manifests

        for record in self._registry_records():
            try:
                path = Path(str(record.get("manifest", ""))).expanduser().resolve()
                root = Path(str(record.get("source_root", path.parent))).expanduser().resolve()
                package = Path(
                    str(record.get("package_source", root))
                ).expanduser().resolve()
                manifests.append(replace(
                    AddonManifest.load(path, source_root=root),
                    catalog_state="Imported draft", catalog_origin="imported",
                    package_source=package,
                ))
            except (OSError, ValueError):
                continue

        catalog_root = self.project_root / "mods" / "catalog"
        if catalog_root.is_dir():
            from allin1_sdk.mods import ModManifest
            for path in sorted(catalog_root.glob("*/mod.toml")):
                try:
                    manifests.append(self._manifest_from_mod(
                        ModManifest.load(path, validate_payload=False),
                        state="Available package", origin="mod-catalog",
                    ))
                except (OSError, ValueError):
                    continue
        manifests.extend(self._installed_manifests(installation_roots))

        # Prefer installed state over imported/available/example records with
        # the same ID while keeping deterministic display order.
        priority = {
            "built-in": 0, "imported": 1, "mod-catalog": 2,
            "installed-receipt": 3,
        }
        selected: dict[str, AddonManifest] = {}
        for manifest in manifests:
            current = selected.get(manifest.addon_id)
            if current is None or priority.get(manifest.catalog_origin, 0) >= priority.get(
                current.catalog_origin, 0
            ):
                selected[manifest.addon_id] = manifest
        return sorted(selected.values(), key=lambda item: item.name.casefold())


def field_description(field: str) -> str:
    return FIELD_HELP.get(field, "Manifest-defined integration field.")


def summarize_values(values: Iterable[Any]) -> str:
    return ", ".join(str(value) for value in values)

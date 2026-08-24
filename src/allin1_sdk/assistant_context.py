"""Focused, authoritative context retrieval for the optional SDK assistant.

The local model never discovers paths, repository roles, or command contracts by
guessing.  This module builds a small read-only evidence bundle from explicit
inputs, local repository metadata, validated manifests, and the SDK's live Click
command catalog.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from allin1_sdk import __version__
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


OPERATION_MODES = ("advisory", "planning")
WORKSPACE_NAMES = ("ALLIN1", "ALLIN1-SDK", "GTAV-ALLIN1-VR")
CORE_PACKAGE_COMMANDS = (
    "validate-package",
    "install-package",
    "inspect-package-receipt",
    "verify-package-ownership",
    "uninstall-package",
    "list-installed-packages",
)
PACKAGE_DOMAIN_TERMS = frozenset({
    "package", "manifest", "receipt", "ownership", "deploy", "deployment",
    "install", "installation", "uninstall", "mod.toml",
})
OPERATION_DOMAINS = {
    "inspect-source": frozenset({
        "source", "code", "cpp", "c++", "symbol", "function", "renderer",
        "class", "implementation", "bloom", "admission",
    }),
    "inspect-log": frozenset({
        "log", "logs", "telemetry", "runtime", "trace", "diagnostic", "counter",
    }),
    "compare-telemetry": frozenset({
        "compare", "comparison", "telemetry", "baseline", "regression", "delta",
    }),
    "inspect-native-asset": frozenset({
        "rpf", "native", "asset", "yft", "ytd", "ydr", "ymap", "awc", "gxt2",
    }),
    "render-native-model": frozenset({
        "yft", "ydd", "ydr", "blender", "eevee", "cycles", "lighting", "studio",
    }),
    "create-weapon-authoring": frozenset({
        "weapon", "weapons", "ammo", "attachment", "attachments",
    }),
    "inspect-weapon-authoring": frozenset({
        "weapon", "weapons", "ammo", "attachment", "attachments",
    }),
    "inspect-weapon-animation": frozenset({
        "weapon", "weapons", "animation", "animations", "clip", "clips",
    }),
    "clone-weapon-animation": frozenset({
        "weapon", "weapons", "animation", "animations", "clip", "clips",
        "template", "mapping", "mappings",
    }),
    "plan-weapon-clone": frozenset({
        "weapon", "weapons", "clone", "bundle", "donor", "template",
        "ammo", "animation", "shop", "storefront", "component",
        "attachment", "plan", "schema",
    }),
    "clone-weapon-bundle": frozenset({
        "weapon", "weapons", "clone", "bundle", "donor", "template",
        "ammo", "animation", "shop", "storefront", "component",
        "attachment", "author", "schema",
    }),
    "inspect-weapon-shop": frozenset({
        "weapon", "weapons", "shop", "store", "storefront", "price", "cost",
    }),
    "set-weapon-shop-fields": frozenset({
        "weapon", "weapons", "shop", "store", "storefront", "price", "cost",
        "label", "labels",
    }),
    "set-weapon-fields": frozenset({
        "weapon", "weapons", "ammo", "attachment", "attachments",
    }),
    "set-weapon-component": frozenset({
        "weapon", "weapons", "ammo", "attachment", "attachments",
    }),
    "set-weapon-attachment": frozenset({
        "weapon", "weapons", "ammo", "attachment", "attachments",
    }),
    "undo-weapon-edit": frozenset({
        "weapon", "weapons", "ammo", "attachment", "attachments",
    }),
}
MANAGED_PACKAGE_POLICY = (
    "Managed package files must be installed only through the manifest-driven "
    "ALLIN1 package lifecycle. Manual copies into GTA V bypass validation, "
    "ownership, receipts, backups, and rollback and are never an acceptable plan."
)
MAX_RAW_MANIFEST_BYTES = 1024 * 1024
MAX_RAW_MANIFEST_CHARS = 24_000


@dataclass(frozen=True)
class RepositoryEvidence:
    root: str
    role: str
    remote: str
    branch: str
    dirty: bool | None
    version: str


@dataclass(frozen=True)
class AssistantContextBundle:
    schema: int
    sdk_version: str
    operation_mode: str
    current_repository: RepositoryEvidence
    workspace_repositories: tuple[RepositoryEvidence, ...]
    package: Mapping[str, object]
    gta_installation: Mapping[str, object]
    mandatory_install_policy: str
    relevant_operations: tuple[Mapping[str, object], ...]
    completed_operations: tuple[str, ...]
    selected_grounding: tuple[Mapping[str, object], ...]
    omitted_context_summary: tuple[str, ...]
    validation_commands: tuple[str, ...]
    missing_context: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _read_remote(root: Path) -> str:
    origin = _git_value(root, "remote", "get-url", "origin")
    if origin:
        return origin
    config = root / ".git" / "config"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    section = re.search(
        r'(?ims)^\s*\[remote\s+"origin"\]\s*(.*?)(?=^\s*\[|\Z)', text,
    )
    if not section:
        return ""
    match = re.search(r"(?im)^\s*url\s*=\s*(.+?)\s*$", section.group(1))
    return match.group(1).strip() if match else ""


def _repository_role(root: Path, remote: str) -> str:
    identity = f"{root.name} {remote}".casefold()
    if "allin1-sdk" in identity:
        return "sdk"
    if "gtav-allin1-vr" in identity or "ez-gta-v-r" in identity:
        return "vr_package"
    if "gtav-allin1" in identity or root.name.casefold() == "allin1":
        return "launcher"
    if (root / "mod.toml").is_file():
        return "mod_package"
    return "unknown"


def _project_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']\s*$', text)
    return match.group(1) if match else ""


def _git_value(root: Path, *arguments: str) -> str:
    if not (root / ".git").exists() or shutil.which("git") is None:
        return ""
    try:
        completed = run_hidden(
            ["git", "-C", root, *arguments], text=True, capture_output=True,
            timeout=5, check=False,
        )
    except (OSError, TimeoutError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _repository_evidence(root: Path) -> RepositoryEvidence:
    resolved = root.expanduser().resolve()
    remote = _read_remote(resolved)
    status = _git_value(resolved, "status", "--porcelain=v1", "--untracked-files=normal")
    tracked = (resolved / ".git").exists()
    return RepositoryEvidence(
        root=str(resolved),
        role=_repository_role(resolved, remote),
        remote=remote,
        branch=_git_value(resolved, "branch", "--show-current"),
        dirty=(bool(status) if tracked else None),
        version=_project_version(resolved),
    )


def _workspace_roots(
    current: Path, explicit: Iterable[Path], manifest: Path | None,
) -> tuple[Path, ...]:
    sdk_root = project_root()
    candidates = [current, *explicit]
    # A source checkout is useful evidence. A frozen managed SDK directory is
    # runtime payload, not a fourth workspace repository.
    if (sdk_root / ".git").exists():
        candidates.append(sdk_root)
    if manifest is not None:
        candidates.append(manifest.parent)
    for parent in {current.parent, project_root().parent}:
        for name in WORKSPACE_NAMES:
            candidate = parent / name
            if candidate.is_dir():
                candidates.append(candidate)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        unique.append(resolved)
    return tuple(unique)


def _find_manifest(current: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        resolved = explicit.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"Package manifest was not found: {resolved}")
        return resolved
    for name in ("mod.toml", "addon.json"):
        candidate = current / name
        if candidate.is_file():
            return candidate.resolve()
    return None


def _raw_manifest_evidence(manifest: Path) -> dict[str, object]:
    """Read bounded manifest provenance before semantic validation.

    A validator error is itself useful evidence, but it must not erase the
    manifest that produced it. The raw text lets an advisory model distinguish
    an unsupported contract version from an actually absent package id, while
    validated lifecycle arguments continue to come only from ``ModManifest``.
    """
    size = manifest.stat().st_size
    if size > MAX_RAW_MANIFEST_BYTES:
        return {
            "path": str(manifest), "bytes": size, "text": "",
            "truncated": True,
            "evidence_error": (
                f"Manifest exceeds the {MAX_RAW_MANIFEST_BYTES // 1024:,} KiB "
                "read-only evidence limit."
            ),
        }
    raw = manifest.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    truncated = len(text) > MAX_RAW_MANIFEST_CHARS
    grounded_text = text
    if truncated:
        marker = "\n...[raw manifest middle omitted by evidence limit]...\n"
        available = MAX_RAW_MANIFEST_CHARS - len(marker)
        head = max(0, (available * 3) // 4)
        grounded_text = text[:head] + marker + text[-(available - head):]
    evidence: dict[str, object] = {
        "path": str(manifest), "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "text": grounded_text, "truncated": truncated,
    }
    try:
        if manifest.name.casefold() == "mod.toml":
            parsed = tomllib.loads(text)
        elif manifest.name.casefold() == "addon.json":
            parsed = json.loads(text)
        else:
            parsed = None
        if isinstance(parsed, Mapping):
            evidence["declared_fields"] = {
                key: parsed[key] for key in (
                    "schema_version", "id", "name", "version", "type", "editions",
                ) if key in parsed
            }
    except (TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        evidence["parse_error"] = str(exc)
    return evidence


def _package_metadata(manifest: Path | None) -> dict[str, object]:
    if manifest is None:
        return {"manifest": "", "kind": "unknown", "validated": False}
    payload: dict[str, object] = {
        "manifest": str(manifest), "kind": manifest.name.casefold(),
        "validated": False,
    }
    try:
        payload["raw_manifest"] = _raw_manifest_evidence(manifest)
    except OSError as exc:
        payload["raw_manifest"] = {
            "path": str(manifest), "text": "", "truncated": False,
            "evidence_error": str(exc),
        }
    try:
        if manifest.name.casefold() == "mod.toml":
            from allin1_sdk.mods import ModManifest

            package = ModManifest.load(manifest, validate_payload=False)
            payload.update({
                "validated": True, "id": package.mod_id, "name": package.name,
                "version": package.version, "type": package.mod_type,
                "editions": list(package.editions),
                "dependencies": list(package.dependencies),
                "files": len(package.files), "rpf_entries": len(package.rpf_entries),
            })
        elif manifest.name.casefold() == "addon.json":
            from allin1_sdk.addon_sdk import AddonManifest

            package = AddonManifest.load(manifest, source_root=manifest.parent)
            payload.update({
                "validated": True, "id": package.addon_id, "name": package.name,
                "version": package.version, "nodes": len(package.nodes),
                "install_steps": len(package.install_steps),
            })
        else:
            payload["validation_error"] = (
                "Only mod.toml and addon.json are authoritative package manifests."
            )
    except (OSError, TypeError, ValueError) as exc:
        payload["validation_error"] = str(exc)
    return payload


def _gta_metadata(path: Path | None) -> dict[str, object]:
    source = "explicit" if path is not None else "not_provided"
    selected = path
    if selected is None:
        try:
            from allin1_sdk.detector import load_cached_path

            selected = load_cached_path()
            source = "verified_cache" if selected is not None else source
        except OSError:
            selected = None
    if selected is None:
        return {"path": "", "source": source, "verified": False, "edition": "unknown"}
    resolved = selected.expanduser().resolve()
    enhanced = (resolved / "GTA5_Enhanced.exe").is_file()
    legacy = (resolved / "GTA5.exe").is_file()
    return {
        "path": str(resolved), "source": source,
        "verified": enhanced or legacy,
        "edition": "enhanced" if enhanced else "legacy" if legacy else "unknown",
    }


def _terms(value: str) -> set[str]:
    return {
        item for item in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", value.casefold())
        if item not in {"the", "and", "for", "with", "this", "that", "from"}
    }


def _compact_contract(item: Mapping[str, object]) -> dict[str, object]:
    parameters: list[dict[str, object]] = []
    for raw in item.get("parameters", []):
        if not isinstance(raw, Mapping):
            continue
        parameter = {
            key: raw[key] for key in (
                "name", "required", "type", "kind", "flags", "multiple", "is_flag",
                "choices",
            ) if key in raw
        }
        help_text = str(raw.get("help", "")).strip()
        if help_text:
            parameter["help"] = help_text[:240]
        parameters.append(parameter)
    return {
        "name": str(item.get("name", "")),
        "description": str(item.get("description", ""))[:240],
        "risk": str(item.get("risk", "read_only")),
        "parameters": parameters,
    }


def retrieve_operations(
    question: str, catalog: Sequence[Mapping[str, object]], *, limit: int = 10,
) -> tuple[Mapping[str, object], ...]:
    """Retrieve exact live command contracts relevant to one question."""
    query = _terms(question)
    by_name = {str(item.get("name", "")): item for item in catalog}
    specialized_domain = any(query.intersection(domain) for domain in OPERATION_DOMAINS.values())
    include_package_domain = bool(query.intersection(PACKAGE_DOMAIN_TERMS)) or not specialized_domain
    selected: list[Mapping[str, object]] = (
        [_compact_contract(by_name[name]) for name in CORE_PACKAGE_COMMANDS if name in by_name]
        if include_package_domain else []
    )
    ranked: list[tuple[int, str, Mapping[str, object]]] = []
    for item in catalog:
        name = str(item.get("name", ""))
        if name in CORE_PACKAGE_COMMANDS or name in {"assistant", "agent-api"}:
            continue
        domain = OPERATION_DOMAINS.get(name)
        if specialized_domain and not include_package_domain and domain is None:
            continue
        if domain is not None and not query.intersection(domain):
            continue
        evidence = json.dumps(item, sort_keys=True, default=str)
        name_terms = _terms(name)
        score = 4 * len(query.intersection(name_terms))
        score += len(query.intersection(_terms(evidence)))
        if domain is not None:
            score += 100
        if score:
            ranked.append((score, name, _compact_contract(item)))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected.extend(item for _score, _name, item in ranked[:max(0, limit - len(selected))])
    return tuple(selected[:limit])


def _is_within(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _selected_grounding(
    roots: tuple[Path, ...], sources: Iterable[Path], symbols: Iterable[str],
    telemetry_files: Iterable[Path], telemetry_patterns: Iterable[str], *,
    telemetry_roots: Iterable[Path] = (),
) -> tuple[tuple[Mapping[str, object], ...], tuple[str, ...]]:
    from allin1_sdk.assistant_evidence import cached_inspect_log, cached_inspect_source

    evidence: list[Mapping[str, object]] = []
    omitted: list[str] = []
    selected_sources = tuple(sources)
    selected_symbols = tuple(dict.fromkeys(
        item.strip() for item in symbols if item.strip()
    ))
    selected_patterns = tuple(telemetry_patterns)
    if selected_symbols and not selected_sources:
        raise ValueError(
            "--symbol requires at least one explicit --source so the requested "
            "grounding cannot silently be empty."
        )
    grounded_symbols: set[str] = set()
    for source in selected_sources:
        resolved = source.expanduser().resolve(strict=True)
        if not _is_within(resolved, roots):
            raise ValueError(
                f"Selected source is outside the declared workspace roots: {resolved}"
            )
        record = cached_inspect_source(resolved, symbols=selected_symbols)
        evidence.append(record)
        grounded_symbols.update(
            str(item.get("symbol", "")).casefold()
            for item in record.get("excerpts", [])
            if isinstance(item, Mapping) and item.get("symbol") and item.get("text")
        )
        missing = record.get("missing_symbols", [])
        if missing:
            omitted.append(
                f"{resolved.name}: symbols not found: {', '.join(str(item) for item in missing)}"
            )
        if record.get("omitted_windows"):
            omitted.append(f"{resolved.name}: additional matching source windows were omitted")
        if record.get("dependencies_omitted"):
            omitted.append(f"{resolved.name}: additional counter dependencies were omitted")
    globally_missing = [
        symbol for symbol in selected_symbols
        if symbol.casefold() not in grounded_symbols
    ]
    if globally_missing:
        raise ValueError(
            "Requested --symbol grounding was not found in any selected source: "
            + ", ".join(globally_missing)
        )
    allowed_telemetry_roots = tuple(dict.fromkeys((*roots, *telemetry_roots)))
    for telemetry in telemetry_files:
        resolved = telemetry.expanduser().resolve(strict=True)
        if not _is_within(resolved, allowed_telemetry_roots):
            raise ValueError(
                "Selected telemetry is outside the declared workspace roots and the "
                f"explicit verified --gta-path: {resolved}"
            )
        record = cached_inspect_log(resolved, patterns=selected_patterns)
        record["access_scope"] = (
            "explicit_verified_gta_path_read_only"
            if not _is_within(resolved, roots)
            and _is_within(resolved, telemetry_roots)
            else "workspace_read_only"
        )
        evidence.append(record)
        if record.get("omitted_lines"):
            omitted.append(f"{resolved.name}: additional telemetry lines were omitted")
    return tuple(evidence), tuple(omitted)


def build_assistant_context(
    question: str, *, repository_root: Path | None = None,
    workspace_roots: Iterable[Path] = (), manifest: Path | None = None,
    gta_path: Path | None = None, operation_mode: str = "advisory",
    sources: Iterable[Path] = (), symbols: Iterable[str] = (),
    telemetry_files: Iterable[Path] = (), telemetry_patterns: Iterable[str] = (),
    catalog_provider: Callable[[], Sequence[Mapping[str, object]]] | None = None,
) -> AssistantContextBundle:
    mode = operation_mode.casefold()
    if mode not in OPERATION_MODES:
        raise ValueError(
            "Assistant prompting supports advisory or planning mode only; execution "
            "must use a separately acknowledged typed SDK operation."
        )
    current = (repository_root or Path.cwd()).expanduser().resolve()
    if not current.is_dir():
        raise ValueError(f"Assistant repository root was not found: {current}")
    selected_manifest = _find_manifest(current, manifest)
    roots = _workspace_roots(current, workspace_roots, selected_manifest)
    repositories = tuple(_repository_evidence(root) for root in roots)
    current_evidence = next(
        (item for item in repositories if Path(item.root) == current),
        _repository_evidence(current),
    )
    if catalog_provider is None:
        from allin1_sdk.agent_api import command_catalog

        catalog_provider = command_catalog
    operations = retrieve_operations(question, tuple(catalog_provider()))
    package = _package_metadata(selected_manifest)
    game = _gta_metadata(gta_path)
    telemetry_roots: tuple[Path, ...] = ()
    # An explicit, executable-verified game folder grants read-only telemetry
    # access beneath that folder. It does not become a source workspace and it
    # never broadens package or game-write authority.
    if gta_path is not None and game.get("verified") and game.get("path"):
        telemetry_roots = (Path(str(game["path"])),)
    selected_grounding, omitted = _selected_grounding(
        roots, sources, symbols, telemetry_files, telemetry_patterns,
        telemetry_roots=telemetry_roots,
    )
    question_terms = _terms(question)
    focused_engineering_evidence = bool(selected_grounding) and (
        any(question_terms.intersection(domain) for domain in OPERATION_DOMAINS.values())
        and not question_terms.intersection(PACKAGE_DOMAIN_TERMS)
    )
    completed_operations: list[str] = []
    if any(item.get("kind") == "source" for item in selected_grounding):
        completed_operations.append("inspect-source")
    if any(item.get("kind") == "telemetry" for item in selected_grounding):
        completed_operations.append("inspect-log")
    operations = tuple(
        item for item in operations
        if str(item.get("name", "")) not in completed_operations
    )
    omitted_context = list(omitted)
    if any(item.dirty for item in repositories):
        omitted_context.append(
            "Dirty-worktree contents, generated assets, binaries, and unrelated files were "
            "not scanned; only repository metadata and explicitly selected evidence were included."
        )
    missing: list[str] = []
    if not focused_engineering_evidence:
        roles = {item.role for item in repositories}
        for role in ("launcher", "sdk", "vr_package"):
            if role not in roles:
                missing.append(f"{role} workspace root is unavailable")
        if not package.get("manifest"):
            missing.append("package manifest was not provided or found")
        if not game.get("verified"):
            missing.append("verified GTA V installation path is unavailable")
    validation: list[str] = []
    if not focused_engineering_evidence:
        validation.append("allin1-sdk validate-package <mod.toml>")
    if current_evidence.role in {"launcher", "sdk"} and (current / "tests").is_dir():
        validation.append("python -m pytest -q")
    return AssistantContextBundle(
        schema=1, sdk_version=__version__, operation_mode=mode,
        current_repository=current_evidence,
        workspace_repositories=repositories,
        package=package, gta_installation=game,
        mandatory_install_policy=MANAGED_PACKAGE_POLICY,
        relevant_operations=operations,
        completed_operations=tuple(completed_operations),
        selected_grounding=selected_grounding,
        omitted_context_summary=tuple(omitted_context),
        validation_commands=tuple(validation), missing_context=tuple(missing),
    )

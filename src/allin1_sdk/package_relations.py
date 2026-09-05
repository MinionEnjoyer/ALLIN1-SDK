"""Semantic vehicle relationships for persistent mod-package graphs."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.vehicle_project import VehicleProject, VehicleProjectResolver


SEMANTIC_SCHEMA_VERSION = 1
RELATION_GROUPS = {
    "primary_model": "assets",
    "high_detail_model": "assets",
    "model_dependency": "assets",
    "texture_dictionary": "assets",
    "collision_dictionary": "assets",
    "text_labels": "assets",
    "vehicle_metadata": "metadata",
    "handling_metadata": "metadata",
    "variation_metadata": "metadata",
    "tuning_metadata": "tuning",
    "tuning_asset": "tuning",
    "registration": "registration",
    "install_target": "registration",
}
ROLE_LABELS = {
    "primary_model": "Primary model",
    "high_detail_model": "High-detail model",
    "model_dependency": "Model dependency",
    "texture_dictionary": "Texture dictionary",
    "collision_dictionary": "Collision dictionary",
    "text_labels": "Text labels",
    "vehicle_metadata": "Vehicle definition",
    "handling_metadata": "Handling ID",
    "variation_metadata": "Vehicle variations",
    "tuning_metadata": "Tuning kit",
    "tuning_asset": "Tuning model",
    "registration": "Registration",
    "install_target": "Install target",
}
_ORPHAN_SUFFIXES = frozenset({".ybn", ".yft", ".ytd", ".gxt2"})
_CORE_META_NAMES = frozenset({
    "vehicles.meta", "handling.meta", "carvariations.meta", "carcols.meta",
})


def _entity_id(root: Path, workspace: Path, model: str) -> str:
    try:
        root_key = root.relative_to(workspace).as_posix()
    except ValueError:
        root_key = str(root)
    digest = hashlib.sha256(
        f"{root_key.casefold()}\0{model.casefold()}".encode("utf-8")
    ).hexdigest()[:20]
    return f"vehicle_{digest}"


def _resolved_key(path: Path) -> str:
    return str(path.resolve()).casefold()


def _analysis_roots(workspace: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    package_source = workspace / "package-source"
    if package_source.is_dir() and not package_source.is_symlink():
        roots.append(package_source.resolve())
    expanded = workspace / "expanded-rpfs"
    if expanded.is_dir() and not expanded.is_symlink():
        roots.extend(
            item.resolve() for item in sorted(
                expanded.iterdir(), key=lambda value: value.name.casefold(),
            )
            if item.is_dir() and not item.is_symlink()
        )
    return tuple(roots)


def _root_edition(
    root: Path, project: VehicleProject, model_assets: tuple[str, ...],
    nodes: dict[str, dict[str, Any]],
) -> str:
    folded_parts = {
        PurePosixPath(path).parts[0].casefold()
        for path in model_assets if PurePosixPath(path).parts
    }
    if folded_parts == {"legacy"}:
        return "Legacy"
    if folded_parts == {"enhanced"}:
        return "Enhanced"
    suffix = ".rpf.source"
    expanded_node_id = (
        root.name[:-len(suffix)] if root.name.casefold().endswith(suffix) else ""
    )
    expanded_node = nodes.get(expanded_node_id)
    expanded = (
        expanded_node.get("expanded_from")
        if isinstance(expanded_node, dict) else None
    )
    node_edition = expanded.get("edition") if isinstance(expanded, dict) else None
    if isinstance(node_edition, str) and node_edition.strip():
        return node_edition.title()
    return project.edition


def _lowest_common_ancestor(
    node_ids: list[str], parents: dict[str, str], root_id: str,
) -> str:
    if not node_ids:
        return root_id

    def ancestry(node_id: str) -> list[str]:
        values = [node_id]
        while values[-1] in parents:
            values.append(parents[values[-1]])
        values.reverse()
        return values

    chains = [ancestry(node_id) for node_id in node_ids]
    common = root_id
    for candidates in zip(*chains):
        if len(set(candidates)) != 1:
            break
        common = candidates[0]
    return common


class PackageRelationshipAnalyzer:
    """Resolve typed vehicle-system links against a retained graph workspace."""

    @classmethod
    def analyze(cls, graph: str | Path, *, persist: bool = True) -> dict[str, Any]:
        graph_path = Path(graph).expanduser().resolve()
        state = RpfPackageGraph.validate(graph_path, verify_sources=True)
        origin = state["payload"].get("origin")
        if not isinstance(origin, dict) or origin.get("type") != "mod_package_import":
            raise ValueError(
                "Semantic package analysis requires a persistent mod-package graph"
            )
        workspace = graph_path.parent
        roots = _analysis_roots(workspace)
        if not roots:
            raise ValueError("The package graph has no retained analysis sources")

        source_nodes = {
            _resolved_key(Path(node["source"])): node_id
            for node_id, node in state["nodes"].items()
            if node.get("type") == "file" and isinstance(node.get("source"), str)
        }
        existing_semantic = state.get("semantic") or {}
        existing_positions = {
            item["id"]: (item["x"], item["y"])
            for item in existing_semantic.get("entities", [])
        }
        entities: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        linked_nodes: set[str] = set()
        entity_models: dict[str, list[dict[str, Any]]] = {}
        resolver = VehicleProjectResolver()
        max_x = max(node["x"] for node in state["nodes"].values())
        entity_row = 0

        for root in roots:
            try:
                project = resolver.inspect(root)
            except (OSError, ValueError) as exc:
                findings.append({
                    "severity": "warning", "code": "analysis_root_unreadable",
                    "message": f"Could not analyze retained source {root.name}: {exc}",
                    "root": str(root),
                })
                continue
            vehicle_model_names = {
                item.model.casefold() for item in project.models
            }
            for model in project.models:
                entity_id = _entity_id(root, workspace, model.model)
                edition = _root_edition(
                    root, project, tuple(item.path for item in model.assets),
                    state["nodes"],
                )
                entity_x, entity_y = existing_positions.get(
                    entity_id, (max_x + 360.0, 80.0 + entity_row * 132.0),
                )
                entity = {
                    "id": entity_id,
                    "type": "vehicle",
                    "name": model.model,
                    "x": entity_x,
                    "y": entity_y,
                    "source_root": str(root),
                    "edition": edition,
                    "metadata": {
                        "display_name": model.display_name,
                        "make_name": model.make_name,
                        "vehicle_class": model.vehicle_class,
                        "vehicle_type": model.vehicle_type,
                        "handling_id": model.handling_id,
                        "layout": model.layout,
                        "audio_name_hash": model.audio_name_hash,
                        "texture_dictionary": model.texture_dictionary,
                        "tuning_kits": list(model.tuning_kits),
                    },
                    "finding_codes": [item.code for item in model.findings],
                }
                entity_row += 1
                entities.append(entity)
                entity_models.setdefault(model.model.casefold(), []).append(entity)
                target_nodes: list[str] = []
                seen_roles: Counter[str] = Counter()
                texture_stems: list[str] = []
                for binding in model.assets:
                    target = (root / Path(*PurePosixPath(binding.path).parts)).resolve()
                    target_id = source_nodes.get(_resolved_key(target))
                    if target_id is None:
                        findings.append({
                            "severity": "error" if binding.required else "warning",
                            "code": "unmapped_graph_asset", "entity_id": entity_id,
                            "model": model.model, "path": binding.path,
                            "message": (
                                f"{ROLE_LABELS.get(binding.role, binding.role)} exists in "
                                "the retained source but has no graph node."
                            ),
                        })
                        continue
                    seen_roles[binding.role] += 1
                    linked_nodes.add(target_id)
                    target_nodes.append(target_id)
                    if binding.role == "texture_dictionary":
                        texture_stems.append(PurePosixPath(binding.path).stem.casefold())
                    relations.append({
                        "source": entity_id, "target": target_id,
                        "type": binding.role,
                        "group": RELATION_GROUPS.get(binding.role, "metadata"),
                        "label": ROLE_LABELS.get(binding.role, binding.role),
                        "required": binding.required,
                    })

                # Fragment-based tuning parts are valid dependencies even though they
                # are not named in vehicles.meta. Keep them attached to the model
                # instead of reporting spoilers, trim, badges, and similar parts as
                # orphans.
                model_prefix = f"{model.model.casefold()}_"
                for source_key, target_id in source_nodes.items():
                    source = Path(source_key)
                    try:
                        source.relative_to(root)
                    except ValueError:
                        continue
                    if target_id in linked_nodes:
                        continue
                    source_stem = source.stem.casefold()
                    belongs_to_other_vehicle = (
                        source_stem in vehicle_model_names
                        or (
                            source_stem.endswith("_hi")
                            and source_stem[:-3] in vehicle_model_names
                        )
                    )
                    if (
                        source.suffix.casefold() not in {".yft", ".ytd"}
                        or not source_stem.startswith(model_prefix)
                        or belongs_to_other_vehicle
                    ):
                        continue
                    linked_nodes.add(target_id)
                    target_nodes.append(target_id)
                    relations.append({
                        "source": entity_id, "target": target_id,
                        "type": "tuning_asset", "group": "tuning",
                        "label": "Tuning model", "required": False,
                    })

                required_roles = {
                    "primary_model", "texture_dictionary", "vehicle_metadata",
                    "handling_metadata", "variation_metadata",
                }
                if model.tuning_kits:
                    required_roles.add("tuning_metadata")
                for missing in sorted(required_roles - set(seen_roles)):
                    findings.append({
                        "severity": "error", "code": f"missing_{missing}",
                        "entity_id": entity_id, "model": model.model,
                        "message": (
                            f"{ROLE_LABELS.get(missing, missing)} did not resolve to a "
                            "retained package asset."
                        ),
                    })
                expected_texture = model.texture_dictionary.casefold()
                if (
                    expected_texture and texture_stems
                    and expected_texture not in texture_stems
                ):
                    findings.append({
                        "severity": "warning", "code": "texture_name_mismatch",
                        "entity_id": entity_id, "model": model.model,
                        "message": (
                            f"vehicles.meta requests {model.texture_dictionary!r}, but "
                            f"the linked YTD names are {', '.join(texture_stems)}."
                        ),
                    })
                for item in model.findings:
                    findings.append({
                        "severity": item.severity, "code": item.code,
                        "entity_id": entity_id, "model": model.model,
                        "message": item.message,
                    })
                install_target = _lowest_common_ancestor(
                    target_nodes, state["parents"], state["root_id"],
                )
                relations.append({
                    "source": entity_id, "target": install_target,
                    "type": "install_target", "group": "registration",
                    "label": f"{edition} install target", "required": True,
                })

        for model_name, matches in entity_models.items():
            by_edition: dict[str, list[dict[str, Any]]] = {}
            for entity in matches:
                by_edition.setdefault(entity["edition"].casefold(), []).append(entity)
            for edition, duplicates in by_edition.items():
                if len(duplicates) > 1:
                    findings.append({
                        "severity": "warning", "code": "duplicate_vehicle_definition",
                        "model": model_name,
                        "message": (
                            f"{len(duplicates)} {edition.title()} definitions resolve to "
                            f"the same model name {model_name!r}."
                        ),
                    })
            signatures = {
                (
                    item["metadata"]["handling_id"].casefold(),
                    item["metadata"]["texture_dictionary"].casefold(),
                    tuple(value.casefold() for value in item["metadata"]["tuning_kits"]),
                )
                for item in matches
            }
            if len(matches) > 1 and len(signatures) > 1:
                findings.append({
                    "severity": "warning", "code": "edition_metadata_mismatch",
                    "model": model_name,
                    "message": (
                        "Vehicle identity links differ between retained edition targets."
                    ),
                })

        for node_id, node in state["nodes"].items():
            if node.get("type") != "file" or node_id in linked_nodes:
                continue
            suffix = Path(node["name"]).suffix.casefold()
            if (
                suffix not in _ORPHAN_SUFFIXES
                and node["name"].casefold() not in _CORE_META_NAMES
            ):
                continue
            findings.append({
                "severity": "warning", "code": "orphaned_vehicle_asset",
                "node_id": node_id, "path": node.get("source", node["name"]),
                "message": (
                    f"{node['name']} looks like vehicle content but is not linked to a "
                    "resolved vehicle record."
                ),
            })

        if not entities:
            findings.append({
                "severity": "info", "code": "no_vehicle_records",
                "message": "No vehicle definitions were resolved in the retained sources.",
            })
        report = {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "analyzer": "vehicle_relationships",
            "entities": entities,
            "relations": relations,
            "findings": findings,
            "summary": {
                "entities": len(entities), "relations": len(relations),
                "errors": sum(item["severity"] == "error" for item in findings),
                "warnings": sum(item["severity"] == "warning" for item in findings),
                "analysis_roots": len(roots),
                "relation_groups": dict(Counter(
                    item["group"] for item in relations
                )),
            },
        }
        if persist:
            RpfPackageGraph.set_semantic_report(graph_path, report)
        return report

    @staticmethod
    def inspect(graph: str | Path) -> dict[str, Any]:
        state = RpfPackageGraph.validate(graph, verify_sources=False)
        report = state.get("semantic")
        if report is None:
            raise ValueError(
                "The graph has not been semantically analyzed; run analyze-package-graph"
            )
        return report

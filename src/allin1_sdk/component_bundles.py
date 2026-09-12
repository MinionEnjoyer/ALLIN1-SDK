"""Read-only component collection validation; each child retains its own lifecycle."""
from allin1_sdk.mod_package_contract import validate_component_bundle, rpf_targets_overlap


def load_collection(cls, path, data, validate_payload):
    from allin1_sdk.mods import _contained_path, _sha256

    children = []
    for edition, rows in validate_component_bundle(data).items():
        group = []
        for row in rows:
            child_path = _contained_path(path.parent, row["manifest"])
            if not child_path.is_file() or _sha256(child_path) != row["sha256"]:
                raise ValueError(f"Component manifest missing or checksum mismatch: {row['manifest']}")
            child = cls.load(child_path, validate_payload=validate_payload, _allow_bundle=False)
            if child.editions != (edition,):
                raise ValueError(f"Component must declare only its matching edition: {edition}")
            if child.mod_id == data["id"] or any(c.mod_id == child.mod_id for c in group):
                raise ValueError("Component ids must be unique within an edition and differ from the bundle id")
            if any(item.sha256 is None for item in (*child.files, *child.rpf_entries)):
                raise ValueError("Component bundle payloads require SHA-256 checksums")
            group.append(child)
        validate_inventory(group)
        children.extend(group)
    return cls(manifest_path=path, mod_id=data["id"], name=data["name"], version=data["version"],
               mod_type="collection", description=str(data.get("description", "")),
               editions=tuple(data["editions"]), dependencies=(), conflicts=(), dlc_packs=(),
               files=(), rpf_entries=(), schema_version=6, variants=tuple(children))


def installation_component(manifest, edition, component_id=None):
    """Revalidate the complete source, including unselected editions, before writes."""
    if manifest.bundle_manifest_path is not None:
        parent = type(manifest).load(manifest.bundle_manifest_path)
        selected = parent.select_component(edition, manifest.mod_id if parent.schema_version == 6 else None)
        if selected.manifest_path != manifest.manifest_path or component_id is not None:
            raise ValueError("Selected bundle component changed")
        return selected
    if manifest.schema_version in (5, 6):
        manifest = type(manifest).load(manifest.manifest_path)
        return manifest.select_component(edition, component_id)
    if component_id is not None:
        raise ValueError("Component selection requires a schema-6 bundle")
    return manifest


def validate_inventory(children):
    """Reject conflicting ownership and misordered internal requirements before writes."""
    by_id = {child.mod_id: child for child in children}
    preceding, files, rpfs, packs = set(), [], [], set()
    for child in children:
        if set(child.conflicts) & by_id.keys():
            raise ValueError("Bundle components conflict with each other")
        for requirement in child.package_requirements:
            if requirement.mod_id in by_id and (
                    requirement.mod_id not in preceding
                    or not requirement.accepts(by_id[requirement.mod_id].version)):
                raise ValueError("Bundle component requirements must precede dependents with compatible versions")
        for item in child.files:
            target = item.destination.as_posix().casefold()
            if any(target == old or target.startswith(old + "/") or old.startswith(target + "/") for old in files):
                raise ValueError("Bundle components have overlapping file ownership")
            if any(target == archive or archive.startswith(target + "/") for archive, _ in rpfs):
                raise ValueError("Bundle component file overlaps an RPF archive")
            files.append(target)
        for item in child.rpf_entries:
            archive, entry = str(item.archive).casefold(), str(item.entry).casefold()
            if any(archive == target or archive.startswith(target + "/") for target in files):
                raise ValueError("Bundle component file overlaps an RPF archive")
            if any(archive == old_archive and rpf_targets_overlap(entry, old_entry) for old_archive, old_entry in rpfs):
                raise ValueError("Bundle components have overlapping RPF ownership")
            rpfs.append((archive, entry))
        for pack in child.dlc_packs:
            if pack.casefold() in packs:
                raise ValueError("Bundle components have overlapping DLC ownership")
            packs.add(pack.casefold())
        preceding.add(child.mod_id)

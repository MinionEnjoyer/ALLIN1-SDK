"""Read-only folder package validation with explicit per-file coverage.

Native model decoding occurs only in temporary workspaces. Embedded archives
are inventoried, not silently considered validated. No metadata references are
promoted to definition identities.
"""
import hashlib
from pathlib import Path
import tempfile

from lxml import etree

from allin1_sdk import __version__, asset_validation, metadata_validation, animation_model, texture_validation, texture_dependencies, attachment_validation, package_intake
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root
from allin1_sdk.workspace_desktop import _inventory, digest, path
from allin1_sdk.implementation_identity import identify
from allin1_sdk import fragment_validation, addon_sdk, package_metadata, material_roles
from allin1_sdk import assembly_validation


def _load_model(file, inspector, temporary, edition):
    with file.open("rb") as stream:
        data=stream.read(16*1024**2+1)
    if len(data)>16*1024**2:
        raise ValueError("Model exceeds the 16 MiB report bound")
    assets=file.parent/"assets"
    if file.suffix.casefold()!=".xml":
        if not edition:
            raise ValueError("Native decoding requires an explicit source edition")
        inspector.export_workspace_bytes(file.name,data,temporary,edition=edition)
        xml=temporary/"edit"/f"{file.name}.xml"
        with xml.open("rb") as stream:
            data=stream.read(16*1024**2+1)
        assets=temporary/"edit/assets"
        if len(data)>16*1024**2:
            raise ValueError("Decoded model XML exceeds 16 MiB")
    return data,assets,animation_model._drawables(data)


def inspect(source, *, comparison=None, edition=None, gta_path=None, rig_bindings=None, assembly_bindings=None):
    from contextlib import ExitStack
    with ExitStack() as stack:
        root,provenance=stack.enter_context(package_intake.materialize(source,edition=edition,gta_path=gta_path))
        other=None;comparison_provenance=None
        if comparison:
            other,comparison_provenance=stack.enter_context(package_intake.materialize(comparison,edition=edition,gta_path=gta_path))
        report=_inspect_folder(str(root),comparison=str(other) if other else None,edition=edition,gta_path=gta_path,rig_bindings=rig_bindings,assembly_bindings=assembly_bindings)
        if provenance["archives"] or (comparison_provenance and comparison_provenance["archives"]):
            report["source_kind"]=provenance["source_kind"]
            report["source_sha256"]=provenance["source_sha256"]
            report["archive_provenance"]={"package":provenance,"comparison":comparison_provenance}
            report["scope"]+=" RPFs marked expanded were recursively decoded into temporary source trees; member IDs and extracted-byte hashes bind back to the unchanged outer archive. Unexpanded archives remain outside validation coverage."
            report.pop("report_sha256")
            report["report_sha256"]=digest(report)
        return report


def _inspect_folder(source, *, comparison=None, edition=None, gta_path=None, rig_bindings=None, assembly_bindings=None):
    root = path(source)
    other = path(comparison) if comparison else None
    if not root.is_dir() or (other and not other.is_dir()):
        raise ValueError("Choose package/comparison folders for asset validation")
    game = path(gta_path) if gta_path else None
    if game and not game.is_dir():
        raise ValueError("Choose a matching GTA installation directory")
    if edition not in {None, "", "Legacy", "Enhanced"}:
        raise ValueError("Choose Legacy or Enhanced")
    before = _inventory(root, limit=1000, size_limit=2*1024**3)
    comparison_before = _inventory(other, limit=1000, size_limit=2*1024**3) if other else {}
    checks = {key:{"category":key,"status":"pass","findings":[],"finding_count":0,"truncated":False} for key in asset_validation.CATEGORIES}
    ranks = {"pass":0,"warning":1,"not_checked":2,"fail":3}

    def add(category, status, code, location, message):
        c = checks[category]
        if ranks[status] > ranks[c["status"]]: c["status"] = status
        c["finding_count"] += 1
        if len(c["findings"]) < 40:
            c["findings"].append({"status":status,"code":code,"location":location[:256],"message":message[:500]})
        c["truncated"] = c["finding_count"] > len(c["findings"])

    files, metrics, records, other_records, distances, fragments = [], [], [], [], [], []
    textures=texture_dependencies.TextureDependencies(add)
    attachments=attachment_validation.Attachments(add)
    dictionary_coverage={}
    metadata_evidence=[];metadata_coverage={}
    model_names = [name for name in before if name.lower().endswith((".ydr", ".ydd", ".yft", ".ydr.xml", ".ydd.xml", ".yft.xml"))]
    if len(model_names) > 32:
        raise ValueError("Package validation is bounded to 32 model files per report; select a narrower package")
    if rig_bindings is None: rig_bindings=[]
    if not isinstance(rig_bindings,list) or len(rig_bindings)>128:
        raise ValueError("Choose at most 128 explicit drawable/shared-rig bindings")
    for base, inventory, target, label in ((root,before,records,"package"),(other,comparison_before,other_records,"comparison")):
        for name, metadata_bytes, metadata_row in package_metadata.documents(base,inventory,label,edition,game,add,metadata_evidence):
            try:
                parsed, supported = metadata_validation.definitions(metadata_bytes, f"{label}:{name}")
                metadata_row["definition_schema_supported"]=supported
                metadata_row["definition_count"]=len(parsed)
                if label=="package": metadata_coverage[name]="definition_scan" if supported else "metadata_schema_unmapped"
                try:
                    attachments.metadata(metadata_bytes,f"{label}:{name}",include_links=label=="package")
                except (ValueError,etree.XMLSyntaxError) as exc:
                    add("attachments","fail","attachment_metadata_invalid",f"{label}:{name}",str(exc))
                for model_name,dictionary in metadata_validation.texture_bindings(metadata_bytes):
                    textures.bindings[model_name].add(dictionary)
                try:
                    textures.metadata(metadata_bytes,f"{label}:{name}")
                except (ValueError,etree.XMLSyntaxError) as exc:
                    add("textures","fail","texture_relationship_invalid",f"{label}:{name}",str(exc))
                if not supported:
                    add("metadata","not_checked","metadata_schema_unsupported",f"{label}:{name}","Definition identities are not mapped for this metadata schema.")
                target.extend(parsed)
            except (ValueError, etree.XMLSyntaxError) as exc:
                metadata_row["status"]="invalid_xml_or_definitions"
                add("metadata","fail","metadata_parse",f"{label}:{name}",str(exc))
            if len(records)+len(other_records)>8000:
                raise ValueError("Package validation exceeds 8,000 metadata definitions")
    for row in metadata_evidence:
        if row["source"].startswith("package:") and row["status"] != "xml_available":
            metadata_coverage[row["source"].removeprefix("package:")] = row["status"]
    textures.validate_parents()
    if not records:
        add("metadata","not_checked","no_definition_context","package","No supported metadata definition records were available.")
    for f in metadata_validation.collisions(records, other_records):
        add("metadata",f["status"],f["code"],f["location"],f["message"]+" Sources: "+", ".join(f["sources"]))
    if not other:
        add("metadata","not_checked","installed_namespace_unchecked","package","Only package-internal identities were checked. No comparison or installed/load-order context was selected.")
    inspector = NativeAssetInspector(project_root(), game)
    drawable_candidates=[];shared_rigs=[]
    with tempfile.TemporaryDirectory(prefix="allin1-package-validation-") as temporary:
        dictionary_number=0
        for base,inventory,label in ((root,before,"package"),(other,comparison_before,"comparison")):
            for name in inventory:
                if not name.lower().endswith((".ytd",".ytd.xml")): continue
                dictionary_number+=1
                if dictionary_number>32:
                    raise ValueError("Package texture validation exceeds 32 dictionaries")
                file=base/name
                try:
                    with file.open("rb") as stream: data=stream.read(16*1024**2+1)
                    if len(data)>16*1024**2: raise ValueError("Dictionary exceeds 16 MiB input bound")
                    assets=file.parent/"assets"
                    if not name.lower().endswith(".xml"):
                        if not edition: raise ValueError("Native texture decoding requires an explicit edition")
                        work=Path(temporary)/f"texture-{dictionary_number}"
                        inspector.export_workspace_bytes(file.name,data,work,edition=edition)
                        xml=work/"edit"/f"{file.name}.xml"
                        with xml.open("rb") as stream: data=stream.read(16*1024**2+1)
                        assets=work/"edit/assets"
                    key=Path(name[:-4] if name.lower().endswith(".xml") else name).stem.casefold()
                    textures.register_xml(data,assets,f"{label}:{name}",key)
                    if label=="package": dictionary_coverage[name]="texture_dictionary_checked"
                except (OSError,ValueError,RuntimeError,etree.XMLSyntaxError) as exc:
                    add("textures","not_checked","dictionary_unavailable",f"{label}:{name}",str(exc))
                    if label=="package": dictionary_coverage[name]="not_checked"
        documents={};unavailable={};decoded_bytes=0
        all_models=[(root,name,"package",before[name]) for name in model_names]
        all_models.extend((other,name,"comparison",checksum) for name,checksum in comparison_before.items() if name.lower().endswith((".ydr",".ydd",".yft",".ydr.xml",".ydd.xml",".yft.xml")))
        if len(all_models)>64: raise ValueError("Package/comparison model catalog exceeds 64 files")
        for ordinal,(base,name,label,checksum) in enumerate(all_models):
            key=f"{label}:{name}"
            try:
                data,assets,owners=_load_model(base/name,inspector,Path(temporary)/f"model-{ordinal}",edition)
                decoded_bytes+=len(data)
                if decoded_bytes>128*1024**2: raise ValueError("Decoded model context exceeds 128 MiB")
                documents[key]=(data,assets,owners,checksum)
                for index,owner in enumerate(owners):
                    drawable_candidates.append({"source":key,"sha256":checksum,"drawable":index,"name":(owner.findtext("Name") or str(index))[:160],"bones":len(owner.findall("Skeleton/Bones/Item"))})
            except (ValueError,RuntimeError,OSError) as exc:
                unavailable[key]=str(exc)
                if label=="comparison":
                    add("textures","not_checked","comparison_model_unavailable",key,"Comparison model could not be decoded; its sampler roles cannot be inspected.")
        if len(drawable_candidates)>1000: raise ValueError("Drawable candidate catalog exceeds 1,000 owners")
        selected_rigs={}
        for binding in rig_bindings:
            if not isinstance(binding,dict) or set(binding)!={"model","drawable","rig","rig_drawable","model_sha256","rig_sha256"}:
                raise ValueError("Choose exact model/rig sources, owners and hashes")
            model_key,rig_key=binding["model"],binding["rig"]
            if not isinstance(model_key,str) or not model_key.startswith("package:") or model_key not in documents or not isinstance(rig_key,str) or rig_key not in documents:
                raise ValueError("Selected model or shared-rig source is unavailable in this context")
            model_doc,rig_doc=documents[model_key],documents[rig_key]
            model_index,rig_index=binding["drawable"],binding["rig_drawable"]
            if type(model_index) is not int or type(rig_index) is not int or not 0<=model_index<len(model_doc[2]) or not 0<=rig_index<len(rig_doc[2]):
                raise ValueError("Select exact drawable ordinals from the current context")
            if binding["model_sha256"]!=model_doc[3] or binding["rig_sha256"]!=rig_doc[3]:
                raise ValueError("Selected model/shared-rig bytes changed; inspect context and choose again")
            if (model_key,model_index) in selected_rigs: raise ValueError("Duplicate shared rig selection for one drawable")
            selected_rigs[(model_key,model_index)]=rig_doc[2][rig_index]
            shared_rigs.append({**binding,"scope":"Explicit user-selected rig; static bind compatibility is checked, not game-intended rig or runtime proof."})
        for name in model_names:
            key=f"package:{name}"
            try:
                if key in unavailable: raise ValueError(unavailable[key])
                data,assets,owners,_=documents[key]
                rigs={index:owner for (model,index),owner in selected_rigs.items() if model==key}
                model = asset_validation.analyze(data,rig_owners=rigs)
                model_key=Path(name[:-4] if name.lower().endswith(".xml") else name).stem.casefold()
                attachments.model(model_key,name,owners,rig_owners=rigs)
                textures.check_model(owners,model_key,name,assets)
                for child_source,child in material_roles.fragment_owners(data):
                    textures.check_model([child],model_key,name+"/"+child_source,assets)
                for c in model["checks"]:
                    if c["category"] == "metadata": continue
                    for f in c["findings"]:
                        if c["category"]=="attachments" and not f["code"].startswith("fragment_"): continue
                        if c["category"]=="textures" and f["code"] in {"external_texture_unresolved","embedded_payload_unverified","unbound_texture_slot"}: continue
                        add(c["category"],f["status"],f["code"],f"{name}/{f['location']}",f["message"])
                    if c["truncated"]:
                        add(c["category"],"not_checked","model_findings_truncated",name,"This model has additional findings beyond the bounded report.")
                metrics.extend({**row,"source":name} for row in model["lod_metrics"])
                distances.extend({**row,"source":name} for row in model["lod_distances"])
                fragments.extend({**row,"source":name} for row in model["fragment_children"])
                files.append({"path":name,"sha256":before[name],"coverage":"model_xml_checked","report_sha256":model["report_sha256"]})
            except (ValueError, RuntimeError, OSError) as exc:
                files.append({"path":name,"sha256":before[name],"coverage":"not_checked"})
                for category in ("skeleton","skinning","textures","lods"):
                    add(category,"not_checked","model_unavailable",name,str(exc))
        # Explicit comparison context can supply attachment parent/child models;
        # duplicates remain ambiguous rather than assigning a load-order winner.
        for key,(data,assets,owners,___) in documents.items():
            if not key.startswith("comparison:"): continue
            name=key.removeprefix("comparison:")
            model_key=Path(name[:-4] if name.lower().endswith(".xml") else name).stem.casefold()
            attachments.model(model_key,key,owners)
            if len(textures.bindings.get(model_key,set()))==1:
                textures.check_model(owners,model_key,key,assets)
                for child_source,child in material_roles.fragment_owners(data):
                    textures.check_model([child],model_key,key+"/"+child_source,assets)
    attachment_bindings=attachments.resolve()
    assemblies = assembly_validation.inspect(assembly_bindings, documents, selected_rigs, add)
    if not model_names:
        for category in ("skeleton","skinning","textures","lods"):
            add(category,"not_checked","no_model_context","package","No directly readable model files were supplied.")
    for name, sha in before.items():
        if name in model_names: continue
        files.append({"path":name,"sha256":sha,"coverage":dictionary_coverage.get(name,metadata_coverage.get(name,"inventory_only"))})
        if name.lower().endswith((".rpf",".zip",".oiv",".7z",".rar")):
            for category in checks:
                add(category,"not_checked","embedded_archive_unexpanded",name,"Embedded archive is inventoried, not expanded by this adapter. Its contents remain outside validation coverage.")
    if len(metrics)>512 or len(distances)>2000:
        raise ValueError("Package LOD report exceeds 512 rows")
    if len(fragments)>256:
        raise ValueError("Package fragment evidence exceeds 256 children; select a narrower package")
    if _inventory(root,limit=1000,size_limit=2*1024**3)!=before or (other and _inventory(other,limit=1000,size_limit=2*1024**3)!=comparison_before):
        raise ValueError("Package or comparison changed during validation")
    implementation=identify([module.__file__ for module in (asset_validation,metadata_validation,texture_dependencies,texture_validation,attachment_validation,animation_model,package_intake,fragment_validation,addon_sdk,package_metadata,material_roles,assembly_validation)]+[__file__])
    report = {"schema_version":1,"ruleset":"package-asset-validation/1","sdk_version":__version__,"read_only":True,
        "source_kind":"package_folder","source_sha256":digest(before),"source_identity":{"files":len(before),"edition":edition,"comparison_sha256":digest(comparison_before) if other else None},
        "validator_sha256":implementation["sha256"],"validator_identity":implementation,
        "drawable_candidates":drawable_candidates,"shared_rigs":shared_rigs,
        "assembly_evidence":assemblies,"assembly_scope":assembly_validation.SCOPE,
        "metadata_evidence":metadata_evidence,
        "fragment_children":fragments,"fragment_scope":fragment_validation.SCOPE,
        "attachment_bindings":attachment_bindings,"attachment_frame_convention":"Row-major matrices acting on column vectors; parent-composed authored skeleton bind frames, not an inferred child placement or game pose.",
        "texture_costs":textures.costs,"texture_resolutions":textures.resolutions,"texture_storage_bytes":sum(row["storage_bytes"] for row in textures.costs),
        "texture_parent_relationships":textures.parent_relationships,
        "texture_memory_scope":"Sum of validated tightly packed 2D mip chains, counted per dictionary entry (not deduplicated); not measured GPU residency. Missing/unsupported payloads are excluded, not treated as zero cost.",
        "checks":list(checks.values()),"lod_metrics":metrics,"lod_distances":distances,"files":files,"definition_count":len(records),"comparison_definition_count":len(other_records),
        "static_status":"fail" if any(c["status"]=="fail" for c in checks.values()) else "incomplete" if any(c["status"]=="not_checked" for c in checks.values()) else "warning" if any(c["status"]=="warning" for c in checks.values()) else "pass",
        "runtime_status":"not_tested","scope":"Folder package static evidence. Exact file hashes; bounded native/XML model checks, explicit texture bindings with 2D DDS mip evidence, and supported metadata definitions. Comparison is explicit context, not inferred installation/load order. Embedded archives, unresolved parent/shared texture dictionaries, attachment assembly and runtime behavior remain unverified."}
    report["report_sha256"] = digest(report)
    return report

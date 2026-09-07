"""Generic package candidates with exact originals and a reviewed reverse path.

Only selected YTD color payloads may change. Models, skeletons, attachments,
animations, metadata and every other package file remain byte-identical. Native
YTDs are rebuilt and reparsed through the existing native workspace verifier.
"""
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import shutil
import tempfile

from lxml import etree

from allin1_sdk import optimization_texture, package_validation, artifact_identity, package_intake, material_roles
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import contained, strict_json
from allin1_sdk.workspace_desktop import _inventory, digest, path, file_hash

ARTIFACT_FILE = "sdk-artifact.json"
MAX_RECEIPT_BYTES = 4*1024**2


def _settings(payload):
    settings = payload.get("settings", {})
    if not isinstance(settings, dict) or set(settings) - {"textures","rig_bindings","assembly_bindings"}:
        raise ValueError("Unsupported optimization settings")
    rigs=settings.get("rig_bindings",[])
    if not isinstance(rigs,list) or len(rigs)>128:
        raise ValueError("Select at most 128 explicit shared-rig bindings")
    targets = settings.get("textures", [])
    if not isinstance(targets, list) or len(targets) > 8:
        raise ValueError("Select at most eight texture candidates per reviewed operation")
    seen = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"dictionary", "texture", "format", "mips", "role"}:
            raise ValueError("Select an exact dictionary, texture, format, mip count and role")
        if not isinstance(target["texture"], str) or not 1 <= len(target["texture"]) <= 160:
            raise ValueError("Choose a bounded texture identity")
        if not isinstance(target["dictionary"], str):
            raise ValueError("Choose a dictionary path")
        key = (target["dictionary"].casefold(), target["texture"].casefold())
        if key in seen:
            raise ValueError("Duplicate texture candidate")
        seen.add(key)
    return targets


def _xml(file):
    with file.open("rb") as stream:
        data = stream.read(16*1024**2+1)
    if len(data) > 16*1024**2:
        raise ValueError("Dictionary exceeds the 16 MiB XML bound")
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    if root.getroottree().docinfo.doctype or root.tag != "TextureDictionary":
        raise ValueError("Expected a DTD-free TextureDictionary")
    return root


def _copy(source, destination, inventory):
    required=sum(contained(source,name).stat().st_size for name in inventory)
    if shutil.disk_usage(destination.parent).free < required+64*1024**2:
        raise ValueError("Insufficient staging space for a recoverable package copy")
    destination.mkdir()
    for name in inventory:
        target = contained(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contained(source, name), target)
    if _inventory(destination, limit=1000) != inventory:
        raise ValueError("Source changed while making an immutable package copy")


@contextmanager
def staged(payload):
    """A single RPF and a folder use the same package transaction and recovery."""
    selected=path(payload.get("source"))
    if selected.is_dir():
        with _staged_folder(payload) as value:
            yield value
        return
    if selected.suffix.casefold()!=".rpf" or not selected.is_file() or selected.stat().st_size>2*1024**3:
        raise ValueError("Choose a package folder or a bounded RPF archive")
    original=file_hash(selected)
    with tempfile.TemporaryDirectory(prefix="allin1-optimization-input-") as temporary:
        root=Path(temporary)/"input"
        root.mkdir()
        if shutil.disk_usage(root).free<selected.stat().st_size+64*1024**2:
            raise ValueError("Insufficient staging space for the RPF original")
        shutil.copyfile(selected,root/selected.name)
        if file_hash(root/selected.name)!=original or file_hash(selected)!=original:
            raise ValueError("RPF source changed while creating its immutable snapshot")
        with _staged_folder({**payload,"source":str(root)}) as (result,candidate,source):
            result["source"]=str(selected)
            result["source_kind"]="rpf_archive"
            result["state_sha256"]=_state(result)
            _recheck_inputs(result)
            yield result,candidate,source


def _state(result):
    return digest({key:([{k:v for k,v in item.items() if k!="preview_region"} for item in value] if key=="changes" else value)
                   for key,value in result.items() if key!="state_sha256"})


def _input_identity(selected):
    if selected.is_dir():
        return {"kind":"folder","inventory":_inventory(selected,limit=1000)}
    if not selected.is_file() or selected.suffix.casefold()!=".rpf" or selected.stat().st_size>2*1024**3:
        raise ValueError("Choose a package folder or a bounded RPF comparison archive")
    return {"kind":"rpf_archive","inventory":{selected.name:file_hash(selected)}}


def _recheck_inputs(result):
    if _input_identity(path(result["source"]))["inventory"]!=result["before_inventory"]:
        raise ValueError("Optimization source changed before publication")
    comparison=result.get("comparison")
    if comparison and digest(_input_identity(path(comparison)))!=result["validation_context"]["comparison_identity_sha256"]:
        raise ValueError("Optimization comparison context changed before publication")


def _check_destination(destination,source,comparison=None):
    if destination.is_relative_to(source):
        raise ValueError("Output must be outside the source package")
    if comparison and destination.is_relative_to(path(comparison)):
        raise ValueError("Output must be outside the comparison context")


def _receipt(result):
    receipt={"schema_version":1,"kind":"optimization_package",**result}
    data=json.dumps(receipt,indent=2,allow_nan=False).encode("utf-8")
    if len(data)>MAX_RECEIPT_BYTES:
        raise ValueError("Optimization receipt exceeds the recovery reader's size bound; narrow the reviewed package/context")
    from allin1_sdk.optimization_recovery import validate_receipt
    validate_receipt(receipt, _state)
    return receipt,data


@contextmanager
def _staged_folder(payload):
    source = path(payload.get("source"))
    if not source.is_dir():
        raise ValueError("Choose an asset package folder")
    edition = payload.get("edition") or None
    game = path(payload["gta_path"]) if payload.get("gta_path") else None
    targets = _settings(payload)
    comparison=path(payload["comparison"]) if payload.get("comparison") else None
    comparison_identity=_input_identity(comparison) if comparison else None
    rigs=payload.get("settings",{}).get("rig_bindings",[])
    assemblies=payload.get("settings",{}).get("assembly_bindings",[])
    before = _inventory(source, limit=1000)
    inspector = NativeAssetInspector(project_root(), game)
    baseline = package_validation.inspect(str(source),comparison=str(comparison) if comparison else None,
                                          rig_bindings=rigs,assembly_bindings=assemblies,edition=edition,gta_path=str(game) if game else None)
    with tempfile.TemporaryDirectory(prefix="allin1-optimization-") as temporary, ExitStack() as intake:
        work = Path(temporary)
        candidate = work / "candidate"
        _copy(source, candidate, before)
        # A prior provenance envelope belongs to the original build. Preserve
        # it in originals, exclude it from payload validation, and replace only
        # this reserved metadata file when emitting the new sealed package.
        if ARTIFACT_FILE in before:
            contained(candidate, ARTIFACT_FILE).unlink()
        editable,provenance=intake.enter_context(package_intake.materialize(source,edition=edition,gta_path=game,force_copy=True))
        if ARTIFACT_FILE in before:
            contained(editable,ARTIFACT_FILE).unlink()
        editable_before=_inventory(editable,limit=1000)
        dictionaries, choices, changes, allowed, ownership = {}, [], [], set(), {}
        for name in editable_before:
            if not name.lower().endswith((".ytd", ".ytd.xml")):
                continue
            if len(dictionaries) >= 32:
                raise ValueError("Optimization is bounded to 32 dictionaries per package")
            native = name.lower().endswith(".ytd")
            native_root = None
            if native:
                if edition not in {"Legacy", "Enhanced"}:
                    raise ValueError("Native optimization needs the exact Legacy or Enhanced edition")
                native_root = work / f"native-{len(dictionaries)}"
                inspector.export_workspace(contained(editable, name), native_root, edition=edition)
                xml = native_root / "edit" / (Path(name).name + ".xml")
                assets = native_root / "edit/assets"
            else:
                xml = contained(editable, name)
                assets = xml.parent / "assets"
            tree = _xml(xml)
            nodes = tree.findall("Item")
            if len(nodes) > 512:
                raise ValueError("Dictionary exceeds 512 textures")
            names = [(node.findtext("Name") or "").casefold() for node in nodes]
            if len(names) != len(set(names)) or any(not item for item in names):
                raise ValueError("Ambiguous dictionary texture names")
            for node in nodes:
                payload_file = contained(assets, node.findtext("FileName"))
                ownership.setdefault(payload_file, []).append((name, node.findtext("Name")))
                choices.append({"dictionary": name, "texture": node.findtext("Name"), "format": node.findtext("Format"),
                                "material_usage":material_roles.profile(baseline,name,node.findtext("Name")),
                                "role": "unknown", "mips": node.find("MipLevels").get("value") if node.find("MipLevels") is not None else None})
            dictionaries[name] = (tree, nodes, xml, assets, native_root)
        if len(choices) > 1000:
            raise ValueError("Optimization catalog exceeds 1,000 textures")
        converted = set()
        for target in targets:
            name = target["dictionary"]
            if name not in dictionaries:
                raise ValueError("Selected dictionary is not in the package")
            tree, nodes, xml, assets, native_root = dictionaries[name]
            matches = [node for node in nodes if node.findtext("Name", "").casefold() == target["texture"].casefold()]
            if len(matches) != 1:
                raise ValueError("Selected texture does not resolve exactly once")
            node = matches[0]
            material_usage = material_roles.profile(baseline,name,target["texture"])
            material_roles.require_color_context(baseline,material_usage)
            original = contained(assets, node.findtext("FileName"))
            if len(ownership[original]) != 1:
                raise ValueError("Selected payload has multiple dictionary owners; resolve shared ownership before optimization")
            if original in converted:
                raise ValueError("Multiple selected dictionary entries share one payload; split or resolve the shared ownership first")
            converted.add(original)
            generated = work / f"texture-{len(changes)}.dds"
            region = payload.get("preview_region")
            if region is not None and (not isinstance(region,dict) or set(region)!={"dictionary","texture","mip","x","y","channel"}):
                raise ValueError("Choose an exact texture and pixel region")
            selected_region = {key:region[key] for key in ("mip","x","y","channel")} if region is not None and region["dictionary"]==name and region["texture"]==target["texture"] else None
            evidence = optimization_texture.candidate(original, generated, {key:target[key] for key in ("format", "mips", "role")}, region=selected_region)
            shutil.copyfile(generated, original)
            for field, value in (("Format", evidence["after"]["format"]), ("MipLevels", evidence["after"]["mip_levels"])):
                element = node.find(field)
                if element is None:
                    raise ValueError(f"Missing dictionary declaration: {field}")
                if field == "MipLevels":
                    element.set("value", str(value))
                else:
                    element.text = str(value)
            xml.write_bytes(etree.tostring(tree, encoding="utf-8", xml_declaration=True))
            if native_root is None:
                allowed.update((name, original.relative_to(editable).as_posix()))
            else:
                allowed.add(name)
            changes.append({"dictionary": name, "texture": target["texture"], "material_usage":material_usage, **evidence})
        for name in {target["dictionary"] for target in targets}:
            native_root = dictionaries[name][4]
            if native_root:
                output = work / "rebuilt" / name
                inspector.build_workspace(native_root, output)
                shutil.copyfile(output, contained(editable, name))
        editable_after=_inventory(editable,limit=1000)
        if set(editable_before)!=set(editable_after) or any(editable_before[name]!=editable_after[name] for name in editable_before if name not in allowed):
            raise ValueError("Optimization changed a protected logical archive member or loose file")
        archive_rebuilds=[];changed_containers=set();loose_allowed=set(allowed)
        for archive in provenance["archives"]:
            if archive["status"]!="expanded":continue
            name=archive["path"];prefix=name+".source/"
            changed={value for value in allowed if value.startswith(prefix)}
            if not changed:continue
            output=work/"repacked"/name
            output.parent.mkdir(parents=True,exist_ok=True)
            _,report_file=RpfArchiveBuilder(project_root(),game).build(contained(editable,name+".source"),output)
            shutil.copyfile(output,contained(candidate,name))
            from allin1_sdk.workspace_desktop import file_hash
            archive_rebuilds.append({"container":name,"before_sha256":before[name],"after_sha256":file_hash(output),
                "changed_members":sorted(changed),"verification":json.loads(report_file.read_bytes())["summary"],
                "preservation":"All nonselected extracted members unchanged before rebuild; rebuilt recursive resource content is canonical-payload verified, and ordinary member bytes are exact. Archive/resource compression envelopes may differ."})
            changed_containers.add(name);loose_allowed-=changed
        for name in loose_allowed:
            shutil.copyfile(contained(editable,name),contained(candidate,name))
        allowed_outputs=loose_allowed|changed_containers
        after = _inventory(candidate, limit=1000)
        if set(before)-{ARTIFACT_FILE} != set(after) or any(before[name] != after[name] for name in before if name not in allowed_outputs and name!=ARTIFACT_FILE):
            raise ValueError("Optimization changed a protected package file")
        # Baseline and candidate consume the same validator, not separate checks.
        reports = [baseline,package_validation.inspect(str(candidate),comparison=str(comparison) if comparison else None,
                   rig_bindings=rigs,assembly_bindings=assemblies,edition=edition,gta_path=str(game) if game else None)]
        if comparison and _input_identity(comparison)!=comparison_identity:
            raise ValueError("Comparison context changed during optimization validation")
        if reports[0]["source_identity"]["comparison_sha256"]!=reports[1]["source_identity"]["comparison_sha256"]:
            raise ValueError("Before and candidate reports do not share the same comparison evidence")
        if any(check["truncated"] for report in reports for check in report["checks"]):
            raise ValueError("Optimization comparison cannot use truncated findings; narrow the package")
        baseline_failures = {(c["category"], f["code"], f["location"]) for c in reports[0]["checks"] for f in c["findings"] if f["status"] == "fail"}
        new_failures = [(c["category"], f["code"], f["location"]) for c in reports[1]["checks"] for f in c["findings"] if f["status"] == "fail" and (c["category"], f["code"], f["location"]) not in baseline_failures]
        if new_failures:
            raise ValueError("Candidate introduced static validation failures: " + str(new_failures)[:500])
        if _inventory(source, limit=1000) != before:
            raise ValueError("Source changed during optimization")
        build = artifact_identity.current()
        artifact = artifact_identity.manifest(build, before, after, edition=edition,
            reports=[report["report_sha256"] for report in reports], changes=[{k:v for k,v in change.items() if not k.startswith("preview_")} for change in changes])
        result = {"source": str(source), "source_kind":"package_folder","settings": payload.get("settings", {}), "edition": edition,
                  "comparison":str(comparison) if comparison else None,
                  "validation_context":{"comparison_identity_sha256":digest(comparison_identity) if comparison else None,
                      "comparison_kind":comparison_identity["kind"] if comparison else None,
                      "shared_rig_count":len(rigs),"assembly_pair_count":len(assemblies),
                      "scope":"Both reports use the same explicitly selected comparison bytes, exact hash-bound rigs and declared assembly pairs. Context is not installed or modified; stale selections reject export. Declared placement is not engine behavior proof."},
                  "artifact_manifest": artifact,
                  "metadata_scope":"sdk-artifact.json is generated provenance metadata, excluded from the payload output inventory to avoid self-reference. Any prior envelope is preserved only in originals.",
                  "before_inventory": before, "after_inventory": after, "choices": choices, "changes": changes,
                  "archive_rebuilds":archive_rebuilds,"archive_provenance":provenance,
                  "before_report": reports[0], "after_report": reports[1],
                  "storage_delta_bytes": sum(item["storage_delta_bytes"] for item in changes),
                  "preservation": "All files outside the explicitly selected dictionary/payload or rebuilt-container set are byte-identical. Inside rebuilt archives, nonselected extracted members remain unchanged and the builder verifies recursive canonical resource content (ordinary members byte-exact). Geometry, rigs, attachment metadata and animations are not authored by this workflow. Static preservation is not in-game quality or behavior acceptance.",
                  "runtime_status": "not_tested"}
        # Navigating a read-only pixel region must not change the candidate's
        # export identity. Source bytes, settings, metrics and build remain bound.
        result["state_sha256"] = _state(result)
        yield result, candidate, source


def inspect(payload):
    if payload.get("workspace"):
        root, receipt = recovery(payload)
        return {"workspace": str(root), "state_sha256": digest(receipt), "recovery_files": len(receipt["before_inventory"])}
    with staged(payload) as (result, _, __):
        if payload.get("preview_region") is not None:
            if not any("preview_region" in item for item in result["changes"]):
                raise ValueError("Pixel region does not select a queued candidate")
            if payload.get("expected_state_sha256")!=result["state_sha256"]:
                raise ValueError("Optimization inputs changed before pixel inspection; preview again")
        return result


def review(payload):
    if payload.get("action") not in {"export", "recover"}:
        raise ValueError("Choose optimized export or recover originals")
    destination = path(payload.get("destination"), new=True, writable=True)
    if payload["action"] == "recover":
        root, receipt = recovery(payload)
        if digest(receipt) != payload.get("expected_state_sha256"):
            raise ValueError("Recovery receipt changed before export")
        state = digest(receipt)
        source = root
        result = {"state_sha256": state, "recovery_files": len(receipt["before_inventory"])}
    else:
        result = inspect(payload)
        source = Path(result["source"])
        if not result["changes"]:
            raise ValueError("Select at least one explicit candidate before export")
        _receipt(result)
    if result["state_sha256"] != payload.get("expected_state_sha256"):
        raise ValueError("Optimization inputs changed; inspect and review again")
    _check_destination(destination,source,payload.get("comparison"))
    return {**result, "action": payload["action"], "destination": str(destination)}


def recovery(payload):
    root = path(payload.get("workspace"))
    file = contained(root, "optimization.json")
    with file.open("rb") as stream:
        data=stream.read(MAX_RECEIPT_BYTES+1)
    if len(data)>MAX_RECEIPT_BYTES:
        raise ValueError("Optimization receipt exceeds 4 MiB")
    receipt = strict_json(data)
    from allin1_sdk.optimization_recovery import validate_receipt
    validate_receipt(receipt, _state)
    if _inventory(contained(root, "originals"), limit=1000) != receipt.get("before_inventory"):
        raise ValueError("Recovery originals were modified; do not trust this recovery set")
    return root, receipt


def apply(payload):
    destination = path(payload.get("destination"), new=True, writable=True)
    if payload["action"] == "recover":
        root, receipt = recovery(payload)
        _check_destination(destination,root)
        if digest(receipt) != payload.get("expected_state_sha256"):
            raise ValueError("Recovery receipt changed before export")
        with tempfile.TemporaryDirectory(prefix=".allin1-recovery-", dir=destination.parent) as temporary:
            staged_root = Path(temporary)/"recovered"
            _copy(contained(root, "originals"), staged_root, receipt["before_inventory"])
            _, current = recovery(payload)
            if digest(current) != digest(receipt):
                raise ValueError("Recovery receipt changed during copying")
            if _inventory(staged_root, limit=1000) != receipt["before_inventory"]:
                raise ValueError("Recovery staged bytes changed before publication")
            path(str(destination), new=True, writable=True)
            staged_root.rename(destination)
        return {"output": str(destination), "output_sha256": digest(receipt["before_inventory"]), "recovered_inventory_sha256": digest(receipt["before_inventory"])}
    with staged(payload) as (result, candidate, source):
        _check_destination(destination,Path(result["source"]),result.get("comparison"))
        if result["state_sha256"] != payload.get("expected_state_sha256"):
            raise ValueError("Optimization inputs changed before export")
        receipt,receipt_bytes=_receipt(result)
        with tempfile.TemporaryDirectory(prefix=".allin1-optimized-", dir=destination.parent) as temporary:
            output = Path(temporary)/"export"
            output.mkdir()
            _copy(source, output/"originals", result["before_inventory"])
            _copy(candidate, output/"package", result["after_inventory"])
            artifact_file = output/"package"/ARTIFACT_FILE
            artifact_file.write_text(json.dumps(result["artifact_manifest"],indent=2,allow_nan=False),encoding="utf-8")
            (output/"optimization.json").write_bytes(receipt_bytes)
            expected_export = {**{f"originals/{name}":checksum for name,checksum in result["before_inventory"].items()},
                               **{f"package/{name}":checksum for name,checksum in result["after_inventory"].items()},
                               f"package/{ARTIFACT_FILE}":file_hash(artifact_file),
                               "optimization.json":file_hash(output/"optimization.json")}
            _recheck_inputs(result)
            if _inventory(output, limit=2002, size_limit=4*1024**3+2*MAX_RECEIPT_BYTES) != expected_export:
                raise ValueError("Optimization staged evidence changed before publication")
            path(str(destination), new=True, writable=True)
            output.rename(destination)
        return {"output": str(destination), "output_sha256": digest(result["after_inventory"]), "receipt": str(destination/"optimization.json"), "recovery_state_sha256": digest(receipt)}

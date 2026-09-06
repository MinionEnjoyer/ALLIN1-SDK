"""Bounded metadata definition identities, not references or guessed load order."""
from collections import defaultdict
import hashlib
import json
import re

from lxml import etree

from allin1_sdk.addon_sdk import joaat

MAX_BYTES = 8 * 1024**2
MAX_RECORDS = 2000


def _semantic(node):
    return [node.tag, sorted(node.attrib.items()), (node.text or "").strip(), [_semantic(child) for child in node if isinstance(child.tag, str)]]


def definitions(data, source):
    if len(data) > MAX_BYTES:
        raise ValueError("Metadata exceeds 8 MiB")
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("Metadata DTDs are not supported")
    specs = {
        "CVehicleModelInfo__InitDataList": [("model", "InitDatas/Item", "modelName")],
        "CPedModelInfo__InitDataList": [("model", "InitDatas/Item", "Name")],
        "CWeaponModelInfo__InitDataList": [("model", "InitDatas/Item", "modelName")],
        "CHandlingDataMgr": [("handling", "HandlingData/Item", "handlingName")],
        "CWeaponComponentInfoBlob": [("component", "Infos/Item", "Name")],
        "CVehicleModelInfoVarGlobal": [("tuning_kit", "Kits/Item", "kitName"), ("tuning_kit_id", "Kits/Item", "id")],
        "CMapTypes": [("archetype", "archetypes/Item", "name")],
        "CMapData": [("map", ".", "name")],
    }
    candidates = []
    if root.tag == "CWeaponInfoBlob":
        for item in root.findall(".//Infos/Item"):
            if item.find("Name") is None:
                continue
            if item.find("AmmoInfo") is not None or item.get("type") == "CWeaponInfo":
                candidates.append(("weapon", item, "Name"))
            elif item.find("AmmoMax") is not None or item.get("type", "").startswith("CAmmo"):
                candidates.append(("ammo", item, "Name"))
    elif root.tag in specs:
        for namespace, selector, field in specs[root.tag]:
            candidates.extend((namespace, item, field) for item in root.findall(selector))
    else:
        return [], False
    if len(candidates) > MAX_RECORDS:
        raise ValueError("Metadata exceeds 2,000 definition records")
    records = []
    for namespace, node, field in candidates:
        child = node.find(field)
        name = ((child.get("value") or child.text or "") if child is not None else "").strip()
        if not name or len(name) > 160:
            raise ValueError(f"Missing or unbounded {namespace} identity")
        if namespace == "tuning_kit_id":
            if not name.isdecimal() or not 0 <= int(name) <= 65535:
                raise ValueError("Invalid numeric tuning-kit identity")
            key = str(int(name))
        elif namespace in {"archetype","map"} and re.fullmatch(r"hash_[0-9a-fA-F]{8}",name):
            # MetaXml.HashString emits a raw uint this way when the name is
            # unknown. Re-hashing the spelling would create a different key.
            key = name[5:].upper()
        else:
            key = f"{joaat(name):08X}"
        semantic = _semantic(node)
        if namespace in {"archetype","map"}:
            for child in semantic[3]:
                if child[0] == field:
                    child[1], child[2] = [], key
        records.append({"namespace": namespace, "name": name, "key": key, "source": source,
                        "definition_sha256": hashlib.sha256(json.dumps(semantic, separators=(",", ":")).encode()).hexdigest()})
    return records, True


def collisions(records, comparison=()):
    if len(records) + len(comparison) > 8000:
        raise ValueError("Metadata collision context exceeds 8,000 definitions")
    groups = defaultdict(list)
    for record in records:
        groups[(record["namespace"], record["key"])].append(record)
    findings = []
    for (namespace, key), entries in groups.items():
        if len(entries) < 2:
            continue
        known_names = {r["name"].casefold() for r in entries
                       if not (namespace in {"archetype","map"} and re.fullmatch(r"hash_[0-9a-fA-F]{8}",r["name"]))}
        same_name = len(known_names) <= 1
        identical = len({r["definition_sha256"] for r in entries}) == 1
        findings.append({"status": "warning" if same_name and identical else "fail",
                         "code": "duplicate_definition" if same_name and identical else "conflicting_definition" if same_name else "hash_collision",
                         "location": f"{namespace}:{key}", "message": "Multiple definitions in the selected package; no load-order winner is inferred.", "sources": [r["source"] for r in entries][:20]})
    for record in comparison:
        key = (record["namespace"], record["key"])
        if key in groups:
            findings.append({"status": "warning", "code": "external_override_candidate", "location": ":".join(key),
                             "message": "Selected comparison context defines the same identity. This may be intentional replacement or a conflict; load order and intent are not supplied.",
                             "sources": [groups[key][0]["source"], record["source"]]})
    return findings


def texture_bindings(data):
    """Only explicit model→dictionary declarations; never filename heuristics."""
    if len(data)>MAX_BYTES:
        raise ValueError("Metadata exceeds 8 MiB")
    root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("Metadata DTDs are not supported")
    if root.tag not in {"CVehicleModelInfo__InitDataList","CWeaponModelInfo__InitDataList"}:
        return []
    result=[]
    for item in root.findall("InitDatas/Item"):
        model=(item.findtext("modelName") or "").strip()
        dictionary=(item.findtext("txdName") or "").strip()
        if model and dictionary:
            if len(model)>160 or len(dictionary)>160:
                raise ValueError("Unbounded model/texture dictionary identity")
            result.append((model.casefold(),dictionary.casefold()))
    return result

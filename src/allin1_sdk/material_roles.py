"""Conservative sampler-role hints, not shader-bytecode or runtime certification."""
from collections import defaultdict
from copy import deepcopy

from lxml import etree

from allin1_sdk.addon_sdk import joaat

# Grounded in bundled CodeWalker BasicShader/TerrainShader texture bindings and
# ShaderParamNames. Color is only a hint; custom shaders/channel use can differ.
SLOTS = {**{name:"normal" for name in ("BumpSampler","BumpSampler2","PlateBgBumpSampler","DetailBumpSampler",
                                     *(f"BumpSampler_layer{i}" for i in range(7)))},
         **{name:"data" for name in ("SpecSampler","distanceMapSampler","heightSampler","FlowSampler")},
         **{name:"palette" for name in ("TintPaletteSampler","TextureSamplerDiffPal")},
         **{name:"color_hint" for name in ("DiffuseSampler","DiffuseSampler2","DiffuseExtraSampler","PlateBgSampler")}}
HASH_ROLES = {f"hash_{joaat(name):08x}":role for name,role in SLOTS.items()}
ROLES = {name.casefold():role for name,role in SLOTS.items()}
SCOPE = "Sampler-name hints from the bundled renderer, not proof of shader bytecode, channel semantics or runtime appearance. Unknown/color-only usage still requires the author's explicit color declaration."


def usages(owner):
    result=defaultdict(list)
    for shader_index,shader in enumerate(owner.findall("ShaderGroup/Shaders/Item")):
        for parameter_index,item in enumerate(shader.findall("Parameters/Item")):
            if item.get("type","").casefold()!="texture": continue
            texture=(item.findtext("Name") or "").strip().casefold()
            slot=item.get("name","")
            if len(slot)>160: raise ValueError("Unbounded texture sampler name")
            if len(result[texture])>=64: raise ValueError("Texture usage exceeds 64 shader slots per drawable")
            result[texture].append({"shader":shader_index,"parameter":parameter_index,"slot":slot,
                                    "role":ROLES.get(slot.casefold(),HASH_ROLES.get(slot.casefold(),"unknown"))})
    return result


def fragment_owners(data):
    root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
    if root.tag!="Fragment": return []
    result=[];primary=root.find("Drawable")
    for lod in ("LOD1","LOD2","LOD3"):
        children=root.findall(f"Physics/{lod}/Children/Item")
        if len(children)>64: raise ValueError("Fragment texture context exceeds 64 children per LOD")
        for index,child in enumerate(children):
            for variant in ("Drawable","Drawable2"):
                node=child.find(variant)
                if node is None or not len(node): continue
                node=deepcopy(node)
                if node.find("ShaderGroup") is None and primary is not None and primary.find("ShaderGroup") is not None:
                    node.append(deepcopy(primary.find("ShaderGroup")))
                result.append((f"Physics/{lod}/Children/{index}/{variant}",node))
    return result


def profile(report,dictionary,texture):
    selected=[row for row in report.get("texture_resolutions",[]) if row["dictionary"]=="package:"+dictionary and row["texture"].casefold()==texture.casefold()]
    evidence=[{"sampler":row["sampler"],"payload_sha256":row["payload_sha256"],**usage} for row in selected for usage in row.get("usages",[])]
    if len(evidence)>512: raise ValueError("Selected texture exceeds 512 material uses")
    roles=sorted({row["role"] for row in evidence})
    return {"roles":roles or ["unknown"],"usages":evidence,"color_conversion_blocked":any(role in {"normal","data","palette"} for role in roles),"scope":SCOPE}


def require_color_context(report,profile):
    if any(check["truncated"] for check in report["checks"]):
        raise ValueError("Optimization comparison cannot use truncated findings; narrow the package")
    if profile["color_conversion_blocked"]:
        raise ValueError("Color conversion is blocked by material usage: "+", ".join(profile["roles"]))
    if any(row["path"].lower().endswith((".ydr",".ydd",".yft",".ydr.xml",".ydd.xml",".yft.xml")) and row["coverage"]!="model_xml_checked" for row in report.get("files",[])):
        raise ValueError("Decode all selected models before declaring texture material roles")
    unresolved={"dictionary_binding_unresolved","dictionary_context_unresolved","texture_parent_ambiguous","texture_parent_depth","dictionary_hash_ambiguous","texture_hash_ambiguous","comparison_model_unavailable"}
    if any(f["code"] in unresolved for c in report["checks"] if c["category"]=="textures" for f in c["findings"]):
        raise ValueError("Resolve texture dictionary context before declaring material roles")

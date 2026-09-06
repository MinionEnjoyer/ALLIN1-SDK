"""Explicit dictionary/sampler bindings with per-payload provenance."""
from collections import defaultdict
import hashlib

from lxml import etree

from allin1_sdk.native_assets import _texture_xml_value
from allin1_sdk.release_paths import contained
from allin1_sdk import texture_validation
from allin1_sdk import material_roles
from allin1_sdk.addon_sdk import joaat


class TextureDependencies:
    def __init__(self, add):
        self.add=add
        self.dictionaries=defaultdict(list)
        self.costs=[]
        self.resolutions=[]
        self.bindings=defaultdict(set)
        self.parents=defaultdict(lambda:defaultdict(list))
        self.parent_relationships=[]
        self.dictionary_hashes=defaultdict(set)
        self.ambiguous_dictionary_keys=set()

    def metadata(self,data,source):
        """Explicit CodeWalker VehiclesFile/GtxdFile/PedsFile relationship XML."""
        if len(data)>8*1024**2:
            raise ValueError("Texture relationship metadata exceeds 8 MiB")
        root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
        if root.getroottree().docinfo.doctype:
            raise ValueError("Texture relationship metadata cannot contain DTDs")
        if root.tag not in {"CVehicleModelInfo__InitDataList","CPedModelInfo__InitDataList","CMapParentTxds"}:
            return
        metadata_sha=hashlib.sha256(data).hexdigest()
        pending=[]

        def identity(node,field):
            fields=node.findall(field)
            if len(fields)!=1:
                raise ValueError(f"Expected one texture relationship {field}")
            value=(fields[0].text or "").strip().casefold()
            if not 1<=len(value)<=160:
                raise ValueError("Missing or unbounded texture relationship identity")
            return value

        def record(child,parent):
            if len(self.parent_relationships)+len(pending)>=1000:
                raise ValueError("Texture relationship context exceeds 1,000 declarations")
            evidence={"child":child,"parent":parent,"source":source,"source_sha256":metadata_sha}
            pending.append(evidence)

        for item in root.findall("txdRelationships/Item")+root.findall("txdRelationships/item"):
            record(identity(item,"child"),identity(item,"parent"))
        if root.tag=="CPedModelInfo__InitDataList":
            for item in root.findall("multiTxdRelationships/Item"):
                parent=identity(item,"parent")
                children=item.findall("children/Item")
                if not children:
                    raise ValueError("Multi-texture relationship has no child declarations")
                for child in children:
                    value=(child.text or "").strip().casefold()
                    if not 1<=len(value)<=160:
                        raise ValueError("Missing or unbounded multi-texture child identity")
                    record(value,parent)
        # An invalid document contributes no partially parsed binding context.
        for evidence in pending:
            self.parent_relationships.append(evidence)
            self.parents[evidence["child"]][evidence["parent"]].append(evidence)

    def validate_parents(self):
        for child,choices in self.parents.items():
            if len(choices)>1:
                self.add("textures","not_checked","texture_parent_ambiguous",child,"Multiple parent dictionaries are declared in the selected context; no load-order winner is inferred.")
        cycles=set()
        for start in self.parents:
            seen=[];cursor=start
            while len(self.parents.get(cursor,{}))==1:
                if cursor in seen:
                    cycle=tuple(sorted(seen[seen.index(cursor):]))
                    if cycle not in cycles:
                        cycles.add(cycle)
                        self.add("textures","fail","texture_parent_cycle",start,"Texture dictionary parent declarations contain a cycle; lookup cannot be safely resolved.")
                    break
                seen.append(cursor)
                cursor=next(iter(self.parents[cursor]))

    def resolve_external(self,key,ref,location):
        chain=[];relationships=[];visited=set()
        for _ in range(32):
            if key in visited:
                self.add("textures","fail","texture_parent_cycle",location,"Texture lookup encountered a parent cycle")
                return None
            visited.add(key)
            if key in self.ambiguous_dictionary_keys:
                self.add("textures","fail","dictionary_hash_ambiguous",location,"Different selected dictionary names share an engine hash; no lookup winner was selected")
                return None
            candidates=self.dictionaries.get(key,[])
            if len(candidates)!=1:
                self.add("textures","not_checked","dictionary_context_unresolved",location,f"Dictionary {key} has {len(candidates)} readable candidates in selected context; no shared-game/load-order winner is inferred.")
                return None
            source,entries=candidates[0]
            chain.append({"dictionary":key,"source":source})
            if ref in entries:
                evidence=entries[ref]
                return {"dictionary":source,"payload_sha256":evidence["sha256"],"dictionary_chain":chain,"parent_chain":relationships} if evidence is not None else None
            parents=self.parents.get(key,{})
            if len(parents)>1:
                self.add("textures","not_checked","texture_parent_ambiguous",location,f"Dictionary {key} has conflicting parent declarations; traversal stopped.")
                return None
            if not parents:
                self.add("textures","not_checked","sampler_not_in_dictionary",location,f"{ref} is absent from the supplied dictionary chain. No further explicit parent is supplied; this is not proof the texture is globally missing.")
                return None
            parent=next(iter(parents))
            relationships.append({"child":key,"parent":parent})
            key=parent
        self.add("textures","not_checked","texture_parent_depth",location,"Texture parent chain exceeds the 32-dictionary lookup bound")
        return None

    def register(self, key, owner, assets, source):
        if not key.startswith("embedded:"):
            names=self.dictionary_hashes[joaat(key)]
            names.add(key)
            if len(names)>1:
                self.ambiguous_dictionary_keys.update(names)
                self.add("textures","fail","dictionary_hash_ambiguous",source,"Different selected texture dictionary names have the same engine lookup hash")
        nodes=owner.findall("Item")
        if len(nodes)>512:
            raise ValueError("Texture dictionary exceeds 512 entries")
        entries={};hash_names=defaultdict(set)
        for node in nodes:
            name=_texture_xml_value(node,"Name").casefold()
            if not name or len(name)>160:
                self.add("textures","fail","texture_identity",source,"Texture name is missing or exceeds the identity bound.")
                continue
            if name in entries:
                entries[name]=None
                self.add("textures","fail","duplicate_texture_name",source,f"Texture name is duplicated: {name}")
                continue
            entries[name]=None
            try:
                relative=_texture_xml_value(node,"FileName")
                file=contained(assets,relative)
                if not relative or file.suffix.casefold()!=".dds":
                    raise ValueError("Texture dependency must name an exact DDS payload")
                evidence=texture_validation.inspect(file)
                for field,key_name in (("Width","width"),("Height","height"),("MipLevels","mip_levels")):
                    if int(_texture_xml_value(node,field))!=evidence[key_name]:
                        raise ValueError(f"Declared {field} disagrees with DDS payload")
                if _texture_xml_value(node,"Format")!=evidence["format"]:
                    raise ValueError("Declared texture format disagrees with DDS payload")
                entries[name]=evidence
                if len(self.costs)>=1000:
                    raise ValueError("Texture cost report exceeds 1,000 payloads")
                self.costs.append({"source":source,"name":name,"payload":relative,**evidence})
                if evidence["trailing_bytes"]:
                    self.add("textures","warning","texture_trailing_bytes",source,f"{name} has {evidence['trailing_bytes']} bytes beyond the validated mip chain.")
                if not evidence["full_chain"]:
                    self.add("textures","warning","partial_mip_chain",source,f"{name} does not contain a complete mip chain; distant sampling quality and runtime cost require review.")
            except NotImplementedError as exc:
                self.add("textures","not_checked","texture_layout_unsupported",source,f"{name}: {exc}")
            except (OSError,ValueError) as exc:
                self.add("textures","fail","texture_payload_invalid",source,f"{name}: {exc}")
            names=hash_names[joaat(name)]
            names.add(name)
            if len(names)>1:
                for ambiguous in names:
                    entries[ambiguous]=None
                self.add("textures","fail","texture_hash_ambiguous",source,"Different texture names share an engine lookup hash; affected samplers cannot be resolved uniquely")
        self.dictionaries[key].append((source,entries))

    def register_xml(self,data,assets,source,key):
        if len(data)>16*1024**2:
            raise ValueError("Texture dictionary XML exceeds 16 MiB")
        root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
        if root.getroottree().docinfo.doctype or root.tag!="TextureDictionary":
            raise ValueError("Expected a DTD-free TextureDictionary XML document")
        self.register(key,root,assets,source)

    def check_model(self,owners,model_name,source,assets):
        def resolved(location, dictionary, ref, evidence, usages):
            if evidence is None: return
            if len(self.resolutions)>=1000:
                raise ValueError("Texture resolution report exceeds 1,000 samplers")
            self.resolutions.append({"sampler":location,"texture":ref,"dictionary":dictionary,"payload_sha256":evidence["sha256"],"usages":usages})
        for ordinal,owner in enumerate(owners):
            refs=material_roles.usages(owner)
            embedded=owner.find("ShaderGroup/TextureDictionary")
            embedded_entries={}
            if embedded is not None:
                key=f"embedded:{source}:{ordinal}"
                self.register(key,embedded,assets,source)
                embedded_entries=self.dictionaries[key][-1][1]
            declarations=self.bindings.get(model_name,set())
            for ref in sorted(refs):
                location=f"{source}/drawable:{ordinal}/{ref}"
                if ref in embedded_entries:
                    resolved(location,f"embedded:{source}:{ordinal}",ref,embedded_entries[ref],refs[ref])
                    continue  # Payload success/failure was recorded while registering.
                if not ref:
                    self.add("textures","not_checked","unbound_texture_slot",location,"Shader has unbound slots; required versus optional bindings are not established by the supplied shader/material-role context.")
                    continue
                if len(declarations)!=1:
                    self.add("textures","not_checked","dictionary_binding_unresolved",location,"Select an unambiguous explicit model→texture dictionary declaration; no filename-based binding is inferred.")
                    continue
                key=next(iter(declarations))
                evidence=self.resolve_external(key,ref,location)
                if evidence is not None:
                    if len(self.resolutions)>=1000:
                        raise ValueError("Texture resolution report exceeds 1,000 samplers")
                    self.resolutions.append({"sampler":location,"texture":ref,"usages":refs[ref],**evidence})

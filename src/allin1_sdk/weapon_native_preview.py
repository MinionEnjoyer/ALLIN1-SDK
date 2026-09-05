"""Read-only, exact-name native asset links for the weapon workbench.

This is package evidence, not an assembled weapon or an attachment transform.
Only already-scanned metadata is parsed; native bytes remain behind the bounded
PackageAssetReader used by the shared viewport renderer.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from lxml import etree

from allin1_sdk.addon_importer import PackageScan

MAX_CHOICES = 500
MAX_ARCHETYPE_BYTES = 2 * 1024 * 1024


def _identifier(value: str) -> str:
    return value.casefold() if re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", value) else ""


def native_preview(scan: PackageScan, weapon: str | None, component: str | None = None) -> dict:
    warnings: list[str] = []
    texture_names: dict[str, set[str]] = {}
    for entry in scan.entries:
        if entry.name.casefold() != "weaponarchetypes.meta":
            continue
        content = entry.content
        if content is None or len(content) > MAX_ARCHETYPE_BYTES:
            warnings.append(f"Texture declarations unavailable: {entry.path} exceeds the metadata preview limit.")
            continue
        try:
            # Check the complete bounded document, including UTF-16/32 spellings.
            normalized = content.replace(b"\0", b"").upper()
            if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
                raise ValueError("DTD/entity declarations are not supported")
            root = etree.fromstring(content, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
            if root.tag != "CWeaponModelInfo__InitDataList":
                raise ValueError("Unexpected weapon archetype root")
            for item in root.findall("./InitDatas/Item"):
                model = _identifier((item.findtext("modelName") or "").strip())
                txd = _identifier((item.findtext("txdName") or "").strip())
                if model and txd:
                    texture_names.setdefault(model, set()).add(txd)
                else:
                    warnings.append(f"Invalid model/texture identifier in {entry.path}; declaration ignored.")
        except (ValueError, etree.XMLSyntaxError):
            warnings.append(f"Unsafe or malformed texture declarations ignored: {entry.path}.")

    models = [entry for entry in scan.entries if entry.suffix in {".ydr", ".ydd", ".yft"}]
    textures = sorted(entry.path for entry in scan.entries if entry.suffix == ".ytd")
    if len(textures) > MAX_CHOICES:
        warnings.append(f"Texture choices limited to the first {MAX_CHOICES} package entries.")
    textures = textures[:MAX_CHOICES]

    def part(kind: str, name: str, model: str, reason: str = "", **extra) -> dict:
        key = _identifier(model)
        matches = sorted((entry for entry in models if key and entry.stem.casefold() in {key, key + "_hi"}), key=lambda entry: entry.path)
        assets = []
        declared = texture_names.get(key, set())
        if len(declared) > 1:
            warnings.append(f"Conflicting texture declarations for {model}; choose a dictionary explicitly.")
        for entry in matches[:MAX_CHOICES]:
            names = declared or {key, entry.stem.casefold()}
            candidates = [path for path in textures if PurePosixPath(path).stem.casefold() in names]
            # Duplicate filenames across package folders are never auto-resolved.
            assets.append({"path": entry.path, "texture_entries": candidates,
                           "texture_entry": candidates[0] if len(candidates) == 1 and len(declared) <= 1 else None})
        if len(matches) > MAX_CHOICES:
            warnings.append(f"Model choices for {name} limited to {MAX_CHOICES} entries.")
        return {"id": f"{kind}:{name}", "kind": kind, "name": name, "model": model,
                "assets": assets, "reason": reason or ("" if assets else "Referenced model is not bundled in this package." if model else "No model reference is declared."), **extra}

    parts = []
    weapons = [item for item in scan.weapons if weapon and item.name.casefold() == weapon.casefold()]
    if len(weapons) == 1:
        parts.append(part("weapon", weapons[0].name, weapons[0].model))
    links = [link for link in scan.weapon_component_links if weapon and link.weapon_name.casefold() == weapon.casefold()]
    names = list(dict.fromkeys(link.component_name for link in links))
    if component and component.casefold() not in {name.casefold() for name in names}:
        names.insert(0, component)
    for name in names[:MAX_CHOICES]:
        definitions = [item for item in scan.weapon_components if item.name.casefold() == name.casefold()]
        matching_links = [link for link in links if link.component_name.casefold() == name.casefold()]
        model = definitions[0].model if len(definitions) == 1 else ""
        reason = "" if len(definitions) == 1 else "Component definition is not bundled in this package." if not definitions else "Multiple definitions share this component identity; no model was selected."
        parts.append(part("component", name, model, reason,
                          attach_bones=sorted({link.attach_bone for link in matching_links}),
                          default=any(link.default for link in matching_links)))
    if len(names) > MAX_CHOICES:
        warnings.append(f"Attachment choices limited to {MAX_CHOICES} entries.")
    selected_part = next((item["id"] for item in parts if component and item["name"].casefold() == component.casefold()), None)
    return {"parts": parts, "selected_part": selected_part or (parts[0]["id"] if parts else None),
            "texture_entries": textures, "warnings": list(dict.fromkeys(warnings))[:100]}

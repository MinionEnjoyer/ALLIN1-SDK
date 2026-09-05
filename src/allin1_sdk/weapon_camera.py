"""Existing-node camera/flag editing; no guessed or synthesized game schema."""

from __future__ import annotations

import math
import re

from lxml import etree


_VECTORS = (
    ("FirstPersonRNGOffset", "Run-and-gun position", "Position"),
    ("FirstPersonRNGRotationOffset", "Run-and-gun rotation", "Rotation"),
    ("FirstPersonLTOffset", "Aimed position", "Position"),
    ("FirstPersonLTRotationOffset", "Aimed rotation", "Rotation"),
    ("FirstPersonScopeOffset", "Scope position", "Position"),
    ("FirstPersonScopeAttachmentOffset", "Attached scope position", "Position"),
    ("FirstPersonScopeRotationOffset", "Scope rotation", "Rotation"),
    ("FirstPersonScopeAttachmentRotationOffset", "Attached scope rotation", "Rotation"),
    ("FirstPersonAsThirdPersonIdleOffset", "Third-person-style idle", "Position"),
    ("FirstPersonAsThirdPersonRNGOffset", "Third-person-style run-and-gun", "Position"),
    ("FirstPersonAsThirdPersonLTOffset", "Third-person-style aim", "Position"),
    ("FirstPersonAsThirdPersonScopeOffset", "Third-person-style scope", "Position"),
    ("FirstPersonAsThirdPersonWeaponBlockedOffset", "Third-person-style blocked", "Position"),
)


def _specs() -> dict[str, dict]:
    fields = {}
    for tag, label, group in _VECTORS:
        for axis in "xyz":
            key = f"weapon.{tag[0].lower() + tag[1:]}.{axis}"
            limit = 360 if group == "Rotation" else 10
            fields[key] = dict(key=key, tag=tag, attribute=axis,
                               label=f"{label} {axis.upper()}", group=label,
                               unit="degrees" if group == "Rotation" else "metres",
                               minimum=-limit, maximum=limit, step="0.00001")
    for tag, label in (
        ("CameraFov", "Aim FOV"),
        ("FirstPersonScopeFov", "Scope FOV"),
        ("FirstPersonScopeAttachmentFov", "Attached scope FOV"),
    ):
        key = "weapon." + tag[0].lower() + tag[1:]
        fields[key] = dict(key=key, tag=tag, attribute="value", label=label,
                           group="Field of view", unit="degrees", minimum=1,
                           maximum=179, step="0.01")
    return fields


CAMERA_FIELDS = _specs()
FLAGS_KEY = "weapon.weaponFlags"
ADVANCED_FIELDS = (*CAMERA_FIELDS, FLAGS_KEY)
MAX_FLAGS_LENGTH = 8192


def _node(item: etree._Element, tag: str) -> etree._Element | None:
    matches = [child for child in item
               if isinstance(child.tag, str) and etree.QName(child).localname == tag]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous weapon camera/flags node: {tag}")
    return matches[0] if matches else None


def advanced_values(item: etree._Element) -> dict[str, str]:
    values = {}
    for key, spec in CAMERA_FIELDS.items():
        node = _node(item, spec["tag"])
        if node is not None and spec["attribute"] in node.attrib:
            values[key] = node.get(spec["attribute"])
    flags = _node(item, "WeaponFlags")
    if flags is not None and not len(flags) and not flags.attrib:
        values[FLAGS_KEY] = " ".join((flags.text or "").split())
    return values


def validate_advanced_value(key: str, value: str) -> str:
    if key == FLAGS_KEY:
        tokens = value.split()
        if len(value) > MAX_FLAGS_LENGTH or len(tokens) > 256 or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", token) for token in tokens
        ):
            raise ValueError("Weapon flags must be at most 256 bounded, space-separated names")
        if len({token.casefold() for token in tokens}) != len(tokens):
            raise ValueError("Weapon flags must not contain duplicates")
        return " ".join(tokens)
    spec = CAMERA_FIELDS[key]
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{spec['label']} must be a finite number") from exc
    if not math.isfinite(number) or not spec["minimum"] <= number <= spec["maximum"]:
        raise ValueError(f"{spec['label']} must be between {spec['minimum']} and {spec['maximum']} {spec['unit']}")
    # Keep reviewed precision, including the user's explicitly authored zeros.
    return value


def set_advanced_value(item: etree._Element, key: str, value: str) -> tuple[str, str]:
    current = advanced_values(item)
    if key not in current:
        raise ValueError(f"Existing weapon record has no editable {key}; schema fields are not synthesized")
    value = validate_advanced_value(key, value)
    if key == FLAGS_KEY:
        _node(item, "WeaponFlags").text = value
    else:
        spec = CAMERA_FIELDS[key]
        _node(item, spec["tag"]).set(spec["attribute"], value)
    return current[key], value

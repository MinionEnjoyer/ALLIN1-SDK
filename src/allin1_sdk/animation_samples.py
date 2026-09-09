"""Read-only bounded YCD channel sampling through the pinned native decoder."""
import math
from pathlib import Path
import re
import subprocess
import tempfile

from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
from allin1_sdk.release_paths import strict_json

MAX_XML = 16 * 1024**2


def analyze(data: bytes, selection=None, *, inventory_only=False):
    if not isinstance(inventory_only, bool) or (inventory_only and selection is not None):
        raise ValueError("Inventory mode must be a boolean and cannot select a clip")
    if not 0 < len(data) <= MAX_XML:
        raise ValueError("Animation XML exceeds the 16 MiB limit")
    if selection is not None and (not isinstance(selection, str) or not re.fullmatch(r"(?:animation|clip):[0-9A-F]{8}", selection)):
        raise ValueError("Choose an exact animation or clip key")
    helper = project_root() / "tools/RpfPatcher/RpfPatcher.exe"
    if not helper.is_file():
        raise ValueError("Animation inspection requires the current RpfPatcher helper")
    with tempfile.TemporaryDirectory(prefix="allin1-animation-") as temporary:
        xml = Path(temporary) / "source.ycd.xml"
        xml.write_bytes(data)
        command = [str(helper), "animation-samples", str(xml)]
        if inventory_only:
            command.append("--inventory")
        elif selection is not None:
            command.append(selection)
        try:
            result = run_hidden(command, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Animation decoder unavailable: {exc}") from exc
    if result.returncode:
        raise ValueError("Animation decoder failed: " + (result.stderr or result.stdout or "unknown error")[:500])
    if len(result.stdout) > 8 * 1024**2:
        raise ValueError("Animation response exceeds 8 MiB")
    packet = strict_json(result.stdout.encode("utf-8"))
    validate(packet)
    return packet


def validate(packet):
    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e9
    if (not isinstance(packet, dict) or packet.get("schema_version") != 1 or packet.get("read_only") is not True
            or not isinstance(packet.get("scope"), str) or not isinstance(packet.get("choices"), list) or len(packet["choices"]) > 2000
            or not isinstance(packet.get("tracks"), list) or len(packet["tracks"]) > 512 or not isinstance(packet.get("times"), list)
            or len(packet["times"]) > 240 or not number(packet.get("duration")) or not 0 <= packet["duration"] <= 86400):
        raise ValueError("Invalid animation decoder packet")
    times, duration = packet["times"], packet["duration"]
    if (any(not number(value) for value in times) or (times and (times[0] != 0 or abs(times[-1]-duration) > 1e-6
            or any(a >= b for a, b in zip(times, times[1:]))))):
        raise ValueError("Invalid animation sample times")
    keys = set()
    for choice in packet["choices"]:
        if (not isinstance(choice, dict) or not isinstance(choice.get("key"), str) or not re.fullmatch(r"(?:animation|clip):[0-9A-F]{8}", choice["key"])
                or choice["key"] in keys or not isinstance(choice.get("name"), str) or not number(choice.get("duration"))
                or choice.get("kind") not in {"clip", "animation"} or not (choice.get("error") is None or isinstance(choice["error"], str))):
            raise ValueError("Invalid animation inventory")
        keys.add(choice["key"])
    if packet.get("selected") is not None and (packet["selected"] not in keys or len(times) < 2 or duration <= 0):
        raise ValueError("Invalid selected animation")
    ids = set()
    for track in packet["tracks"]:
        if (not isinstance(track, dict) or not isinstance(track.get("id"), str) or track["id"] in ids
                or not isinstance(track.get("label"), str) or type(track.get("quaternion")) is not bool
                or any(type(track.get(key)) is not int or not 0 <= track[key] <= high for key, high in (("bone_tag", 65535), ("track", 255), ("flags", 255), ("layer", 15)))
                or not isinstance(track.get("values"), list) or len(track["values"]) != len(times)*4
                or any(not number(value) for value in track["values"])):
            raise ValueError("Invalid animation track samples")
        ids.add(track["id"])
    if packet.get("selected") is None and (times or packet["tracks"]):
        raise ValueError("Animation samples lack a selected record")

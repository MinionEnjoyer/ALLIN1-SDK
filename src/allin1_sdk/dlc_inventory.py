"""Edition-aware, read-only DLC package inventory and reconciliation."""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from allin1_sdk.addon_importer import _local_name, _parse_xml
from allin1_sdk.processes import run_hidden


_PACK = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)
_DLC_PATH = re.compile(r"^dlcpacks:/([^/]+)/?$", re.IGNORECASE)


@dataclass(frozen=True)
class DlcFinding:
    severity: str
    code: str
    message: str
    pack: str | None = None


@dataclass(frozen=True)
class DlcPackStatus:
    name: str
    stock_present: bool
    mod_present: bool
    stock_payload: bool
    mod_payload: bool
    registration_count: int
    receipt_declared: bool
    allin1_owned: bool
    ownership: str
    state: str
    recommendation: str


@dataclass(frozen=True)
class DlcInventoryReport:
    game_root: Path
    edition: str
    registrations: tuple[str, ...]
    packs: tuple[DlcPackStatus, ...]
    findings: tuple[DlcFinding, ...]

    @property
    def issue_count(self) -> int:
        return sum(item.state != "Ready" for item in self.packs) + len(self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "game_root": str(self.game_root), "edition": self.edition,
            "registrations": list(self.registrations),
            "packs": [asdict(item) for item in self.packs],
            "findings": [asdict(item) for item in self.findings],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# {self.edition} DLC inventory", "",
            f"Game root: `{self.game_root}`", "",
            "| Package | Ownership | Stock | Mods | Registrations | State | Recommendation |",
            "|---|---|:---:|:---:|---:|---|---|",
        ]
        for item in self.packs:
            stock = "yes" if item.stock_present else "—"
            mod = "yes" if item.mod_present else "—"
            lines.append(
                f"| `{item.name}` | {item.ownership} | {stock} | {mod} | "
                f"{item.registration_count} | {item.state} | {item.recommendation} |"
            )
        lines.extend(["", "## Scanner findings", ""])
        if not self.findings:
            lines.append("No inventory scanner errors were found.")
        for item in self.findings:
            pack = f" `{item.pack}`" if item.pack else ""
            lines.append(
                f"- **{item.severity.upper()} `{item.code}`**{pack}: {item.message}"
            )
        lines.extend([
            "", "This report is read-only. It does not rewrite `dlclist.xml`, "
            "delete folders, or claim ownership of externally installed packs.", "",
        ])
        return "\n".join(lines)

    def write(self, destination: str | Path) -> Path:
        path = Path(destination).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_markdown(), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8",
        )
        return path


class DlcInventory:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def scan(
        self, game_root: str | Path, *, dlclist_xml: str | bytes | None = None,
    ) -> DlcInventoryReport:
        game = Path(game_root).expanduser().resolve()
        if not game.is_dir():
            raise FileNotFoundError(f"GTA V folder not found: {game}")
        edition = (
            "Enhanced" if (game / "GTA5_Enhanced.exe").is_file() else
            "Legacy" if (game / "GTA5.exe").is_file() else "Unknown"
        )
        findings: list[DlcFinding] = []
        if dlclist_xml is None:
            try:
                dlclist_xml = self._extract_dlclist(game)
            except (FileNotFoundError, RuntimeError, OSError) as exc:
                findings.append(DlcFinding(
                    "warning", "dlclist_unavailable", str(exc),
                ))
                dlclist_xml = b"<SMandatoryPacksData><Paths /></SMandatoryPacksData>"
        registrations = self._registrations(dlclist_xml)
        counts = Counter(value.casefold() for value in registrations)

        stock = self._folders(game / "update" / "x64" / "dlcpacks")
        modded = self._folders(game / "mods" / "update" / "x64" / "dlcpacks")
        declared, owned, receipt_findings = self._receipt_packs(game)
        findings.extend(receipt_findings)
        names = set(stock) | set(modded) | set(counts) | declared | owned
        packs: list[DlcPackStatus] = []
        for name in sorted(names):
            stock_dir = stock.get(name)
            mod_dir = modded.get(name)
            stock_payload = bool(stock_dir and (stock_dir / "dlc.rpf").is_file())
            mod_payload = bool(mod_dir and (mod_dir / "dlc.rpf").is_file())
            count = counts.get(name, 0)
            state, recommendation = self._state(
                bool(stock_dir), bool(mod_dir), stock_payload, mod_payload, count,
                name in declared,
            )
            ownership = (
                "ALLIN1 managed" if name in owned else
                "External mod" if mod_dir else "Rockstar stock" if stock_dir else
                "Receipt only" if name in declared else "Registration only"
            )
            packs.append(DlcPackStatus(
                name, bool(stock_dir), bool(mod_dir), stock_payload, mod_payload,
                count, name in declared, name in owned, ownership, state,
                recommendation,
            ))
        return DlcInventoryReport(
            game, edition, tuple(registrations), tuple(packs), tuple(findings),
        )

    @staticmethod
    def _registrations(xml: str | bytes) -> list[str]:
        content = xml.encode("utf-8") if isinstance(xml, str) else xml
        try:
            root = _parse_xml(content, "dlclist.xml")
        except ET.ParseError as exc:
            raise ValueError(f"Invalid dlclist.xml: {exc}") from exc
        registrations: list[str] = []
        for item in root.iter():
            if _local_name(item.tag).casefold() != "item":
                continue
            match = _DLC_PATH.fullmatch((item.text or "").strip())
            if match and _PACK.fullmatch(match.group(1)):
                registrations.append(match.group(1).casefold())
        return registrations

    @staticmethod
    def _folders(root: Path) -> dict[str, Path]:
        if not root.is_dir():
            return {}
        return {
            candidate.name.casefold(): candidate for candidate in root.iterdir()
            if candidate.is_dir() and _PACK.fullmatch(candidate.name)
        }

    @staticmethod
    def _receipt_packs(
        game: Path,
    ) -> tuple[set[str], set[str], list[DlcFinding]]:
        declared: set[str] = set()
        owned: set[str] = set()
        findings: list[DlcFinding] = []
        root = game / "scripts" / ".allin1" / "mods"
        if not root.is_dir():
            return declared, owned, findings
        for receipt in root.glob("*.json"):
            try:
                data = json.loads(receipt.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("receipt root is not an object")
                declared_values = data.get("dlc_packs", [])
                owned_values = data.get("owned_dlc_packs", [])
                if not isinstance(declared_values, list) or not isinstance(owned_values, list):
                    raise ValueError("DLC ownership fields are not arrays")
                declared.update(
                    str(value).casefold() for value in declared_values
                    if _PACK.fullmatch(str(value))
                )
                owned.update(
                    str(value).casefold() for value in owned_values
                    if _PACK.fullmatch(str(value))
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                findings.append(DlcFinding(
                    "warning", "invalid_receipt",
                    f"Could not read {receipt.name}: {exc}",
                ))
        return declared, owned, findings

    @staticmethod
    def _state(
        stock: bool, mod: bool, stock_payload: bool, mod_payload: bool, count: int,
        receipt_declared: bool,
    ) -> tuple[str, str]:
        if (stock and not stock_payload) or (mod and not mod_payload):
            return "Incomplete payload", "Restore or remove the folder after review."
        if count > 1:
            return "Duplicate registration", "Keep one load-order entry."
        if not stock and not mod and receipt_declared:
            return "Missing payload", "Restore the package or remove the stale entry."
        if not stock and not mod and count:
            return "Registration only", "May be embedded; compare with the stock edition before changing it."
        if mod and not count:
            return "Unregistered", "Review and register this mod package."
        if stock and not count:
            return "Stock not registered", "Compare against the edition's stock load order."
        return "Ready", "No change proposed."

    def _extract_dlclist(self, game: Path) -> bytes:
        patcher = self.project_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"
        if not patcher.is_file():
            raise FileNotFoundError("RpfPatcher.exe is required to read dlclist.xml")
        archive = next((candidate for candidate in (
            game / "mods" / "update" / "update.rpf",
            game / "update" / "update.rpf",
        ) if candidate.is_file()), None)
        if archive is None:
            raise FileNotFoundError("No update.rpf was found for DLC registration inventory")
        with tempfile.TemporaryDirectory(prefix="allin1_dlclist_") as temporary:
            output = Path(temporary) / "dlclist.xml"
            result = run_hidden(
                [patcher, "extract-entry", game, archive, "dlclist.xml", output],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120,
            )
            if result.returncode or not output.is_file():
                detail = (result.stderr or result.stdout or "unknown helper error").strip()
                raise RuntimeError(f"Could not extract dlclist.xml: {detail}")
            return output.read_bytes()

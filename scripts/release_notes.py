"""Render one curated release body; never publish, sign or qualify artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote, unquote, urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from allin1_sdk.release_paths import contained, no_links

PRODUCTS = {
    "sdk": ("ALLIN1 SDK", "allin1-sdk", "MinionEnjoyer/ALLIN1-SDK"),
    "launcher": ("GTA V ALLIN1", "gta-v-allin1", "MinionEnjoyer/GTAV-ALLIN1"),
}
SECTIONS = ["## What's new", "## Download and trust", "## Release status"]


def release_version(value: str) -> str:
    if not re.fullmatch(r"v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        raise ValueError("Release notes require an exact stable version")
    return value.removeprefix("v")


def render_notes(text: str, version: str, product: str, *, require_final: bool = False, release_ref: str | None = None) -> str:
    version = release_version(version)
    ref = release_ref or f"v{version}"
    if not re.fullmatch(re.escape(f"v{version}") + r"(?:-rc\.[1-9]\d*)?", ref):
        raise ValueError("Release reference must match the source version or its numbered release candidate")
    title, _, repo = PRODUCTS[product]
    lines = text.strip().splitlines()
    heading = f"# {title} {version}"
    if not lines or lines[0] not in {heading, heading + " — unreleased", heading + " — unsigned prerelease"}:
        raise ValueError("Release notes product/version heading does not match")
    if [line for line in lines[1:] if line.startswith("#")] != SECTIONS:
        raise ValueError("Release notes must contain only the three current-release sections")
    if len(text.split()) > 450 or len(lines) > 80:
        raise ValueError("Release notes exceed the concise current-release limit")
    if "**Unsigned manual download.**" not in text or "SHA-256" not in text:
        raise ValueError("Release notes must disclose unsigned downloads and checksums")
    if require_final and (ref != f"v{version}" or re.search(r"\bunreleased\b|\bdraft\b|\bprerelease\b|not release-qualified", text, re.I)):
        raise ValueError("Unreleased/draft notes cannot be used for final publication")

    def link(match: re.Match) -> str:
        label, target = match.groups()
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Release links must use HTTPS or safe repository paths")
            return match.group(0)
        if not parsed.path:
            return match.group(0)
        path = unquote(parsed.path)
        if ("\\" in path or ":" in path or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts or parsed.query):
            raise ValueError("Release link leaves the selected repository")
        target = f"https://github.com/{repo}/blob/{ref}/{quote(path, safe='/')}"
        if parsed.fragment:
            target += "#" + parsed.fragment
        return f"[{label}]({target})"

    return re.sub(r"\[([^\]\n]+)\]\(([^\s)]+)\)", link, text.strip()) + "\n"


def prepare(root: Path, product: str, version: str, output: str | None, *, require_final: bool = False, release_ref: str | None = None) -> str:
    root = no_links(root.absolute())
    project = tomllib.loads(contained(root, "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if (project["name"], project["version"]) != (PRODUCTS[product][1], release_version(version)):
        raise ValueError("Source product/version differs from selected release")
    text = contained(root, "RELEASE_NOTES.md").read_text(encoding="utf-8")
    body = render_notes(text, version, product, require_final=require_final, release_ref=release_ref)
    if output is not None:
        destination = contained(root, output)
        if not destination.is_relative_to(root / "build"):
            raise ValueError("Rendered notes must be a new file under build/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--product", choices=PRODUCTS, default="sdk")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", help="New repository-relative file under build/; never overwritten")
    parser.add_argument("--require-final", action="store_true")
    parser.add_argument("--release-ref", help="Exact vX.Y.Z or vX.Y.Z-rc.N link target; does not qualify or publish a build")
    args = parser.parse_args()
    try:
        body = prepare(args.root, args.product, args.version, args.output, require_final=args.require_final, release_ref=args.release_ref)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Release notes refused: {error}\n")
    if args.output:
        print(f"Prepared {args.output}; publication and artifact qualification were not performed.")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

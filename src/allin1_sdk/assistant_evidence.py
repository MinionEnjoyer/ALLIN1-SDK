"""Bounded read-only source and telemetry evidence for the SDK assistant."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from collections import OrderedDict, deque
from pathlib import Path
from typing import Iterable, Mapping

from allin1_sdk.processes import hidden_process_options


MAX_EVIDENCE_FILE_BYTES = 8 * 1024 * 1024
MAX_LOG_FILE_BYTES = 256 * 1024 * 1024
MAX_LOG_LINE_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_CHARS = 24_000
MAX_EXPLICIT_SYMBOL_CHARS = 96_000
MAX_SOURCE_DEPENDENCIES = 32
MAX_SOURCE_DECLARATIONS = 32
MAX_REPOSITORY_SOURCE_FILES = 6000
MAX_REPOSITORY_RELATIONSHIPS = 18
MAX_UNTRACKED_SOURCE_FILES = 256
MAX_UNTRACKED_SOURCE_BYTES = 32 * 1024 * 1024
MAX_GIT_INVENTORY_BYTES = 2 * 1024 * 1024
EVIDENCE_CACHE_ENTRIES = 48
REVIEW_PRIORITIES = ("callers", "tests", "state-transitions")
_COMPACTION_STOPWORDS = frozenset({
    "about", "after", "against", "also", "before", "between", "could",
    "diagnose", "does", "from", "have", "into", "only", "please", "review",
    "should", "source", "symbol", "that", "their", "there", "these", "this",
    "through", "using", "what", "when", "where", "which", "while", "with",
    "without", "would",
})
TEXT_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".cs", ".py", ".toml", ".json", ".jsonl", ".log", ".txt",
    ".md", ".xml", ".yaml", ".yml", ".ini", ".cfg",
})
SOURCE_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".cs", ".py",
})
_REPOSITORY_SKIP_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".idea", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", "node_modules", "packages", "bin",
    "obj", "build", "dist", "__pycache__",
})
_EVIDENCE_CACHE: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
_EVIDENCE_CACHE_LOCK = threading.RLock()
_COUNTER_HINT = re.compile(
    r"(?i)(counter|count|check|hit|reject|attempt|success|failure|admission|candidate|scan)"
)
_COUNTER_READ = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*load\s*\(", re.IGNORECASE,
)
_IMMUTABLE_DECLARATION = re.compile(
    r"\b(?:const|constexpr|constinit|readonly)\b"
    r"(?P<body>[^;=]{0,256})\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=",
    re.IGNORECASE,
)
_STATE_IDENTIFIER_HINT = re.compile(
    r"(?i)(state|status|phase|mode|generation|epoch|active|ready|pending|"
    r"lifecycle|terminal|record|reset|retire|publish|observe|owner|queue)"
)
_STATE_TRANSITION = re.compile(
    r"(?i)(?:\.\s*(?:store|exchange|compare_exchange(?:_weak|_strong)?|"
    r"fetch_add|fetch_sub|clear|erase|reset|emplace|push_back)\s*\(|"
    r"\b(?:transition|retire|publish|reset|activate|deactivate|invalidate)\w*\s*\(|"
    r"\+\+|--|(?<![=!<>])=(?!=))"
)


def _resolve_text_path(path: Path, *, max_bytes: int) -> tuple[Path, int]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"Evidence path is not a file: {resolved}")
    if resolved.suffix.casefold() not in TEXT_SUFFIXES:
        raise ValueError(f"Evidence file type is not approved as text: {resolved.suffix}")
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"Evidence file exceeds the {max_bytes // (1024 * 1024)} MiB limit: "
            f"{resolved}"
        )
    with resolved.open("rb") as source:
        if b"\0" in source.read(8192):
            raise ValueError(f"Evidence file appears to be binary: {resolved}")
    return resolved, size


def _read_text(path: Path) -> tuple[Path, str, str]:
    resolved, _size = _resolve_text_path(path, max_bytes=MAX_EVIDENCE_FILE_BYTES)
    raw = resolved.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return resolved, text, hashlib.sha256(raw).hexdigest()


def _repository_source_files(
    root: Path, *, limit: int = MAX_REPOSITORY_SOURCE_FILES,
) -> tuple[tuple[Path, ...], bool]:
    """Return a deterministic, symlink-free repository source inventory."""
    resolved = root.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"Repository root is not a directory: {resolved}")
    files: list[Path] = []
    truncated = False
    for directory, names, filenames in os.walk(resolved, followlinks=False):
        names[:] = sorted(
            name for name in names
            if name.casefold() not in _REPOSITORY_SKIP_DIRECTORIES
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(filenames, key=str.casefold):
            candidate = Path(directory) / name
            if candidate.suffix.casefold() not in SOURCE_SUFFIXES or candidate.is_symlink():
                continue
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            if size > MAX_EVIDENCE_FILE_BYTES:
                continue
            if len(files) >= limit:
                truncated = True
                return tuple(files), truncated
            files.append(candidate)
    return tuple(files), truncated


def _git_inventory_bytes(
    root: Path, *arguments: str,
) -> tuple[bytes | None, bool]:
    """Read one bounded local Git inventory without invoking a shell."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=False,
            **hidden_process_options(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if completed.returncode != 0:
        return None, False
    if len(completed.stdout) > MAX_GIT_INVENTORY_BYTES:
        return None, True
    return completed.stdout, False


def _repository_worktree_state(root: Path) -> dict[str, object]:
    """Return tracked, dirty, and untracked path evidence for one repository."""
    status_raw, status_truncated = _git_inventory_bytes(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        "--ignored=no",
    )
    tracked_raw, tracked_truncated = _git_inventory_bytes(
        root, "ls-files", "-z", "--cached",
    )
    if status_raw is None or tracked_raw is None:
        return {
            "available": False, "paths": {}, "tracked": set(),
            "inventory_truncated": status_truncated or tracked_truncated,
        }
    statuses: dict[str, str] = {}
    records = status_raw.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        code = record[:2].decode("ascii", errors="replace")
        relative = record[3:].decode(
            "utf-8", errors="surrogateescape",
        ).replace("\\", "/")
        if not relative:
            continue
        if code == "??":
            status = "untracked"
        elif "R" in code or "C" in code:
            status = "renamed_or_copied"
            index += 1  # Porcelain v1 -z includes the second rename/copy path.
        elif code[0] not in {" ", "?"} and code[1] not in {" ", "?"}:
            status = "staged_and_modified"
        elif code[0] not in {" ", "?"}:
            status = "staged"
        else:
            status = "modified"
        statuses[relative] = status
    tracked = {
        value.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for value in tracked_raw.split(b"\0") if value
    }
    return {
        "available": True, "paths": statuses, "tracked": tracked,
        "inventory_truncated": status_truncated or tracked_truncated,
    }


def _path_worktree_evidence(
    root: Path, path: Path, state: Mapping[str, object],
) -> dict[str, object]:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    statuses = state.get("paths", {})
    tracked = state.get("tracked", set())
    status = "unavailable"
    if state.get("available") and isinstance(statuses, Mapping):
        status = str(statuses.get(relative, ""))
        if not status:
            status = "tracked_clean" if relative in tracked else "untracked_ignored"
    return {
        "worktree_status": status,
        "worktree_dirty": status not in {"tracked_clean", "unavailable"},
        "worktree_untracked": status in {"untracked", "untracked_ignored"},
        "worktree_status_available": bool(state.get("available")),
    }


def _repository_review_files(
    root: Path,
) -> tuple[tuple[Path, ...], bool, dict[str, object]]:
    """Merge the regular source walk with bounded Git-reported untracked sources."""
    resolved = root.expanduser().resolve(strict=True)
    files, truncated = _repository_source_files(resolved)
    state = _repository_worktree_state(resolved)
    output = list(files)
    known = {path.resolve() for path in output}
    untracked_added = 0
    untracked_bytes = 0
    untracked_truncated = bool(state.get("inventory_truncated", False))
    statuses = state.get("paths", {})
    if isinstance(statuses, Mapping):
        for relative, status in sorted(statuses.items(), key=lambda item: item[0].casefold()):
            if status != "untracked":
                continue
            candidate = (resolved / str(relative)).resolve()
            if (
                candidate in known or not candidate.is_relative_to(resolved)
                or candidate.is_symlink() or not candidate.is_file()
                or candidate.suffix.casefold() not in SOURCE_SUFFIXES
            ):
                continue
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            if (
                size > MAX_EVIDENCE_FILE_BYTES
                or untracked_added >= MAX_UNTRACKED_SOURCE_FILES
                or untracked_bytes + size > MAX_UNTRACKED_SOURCE_BYTES
            ):
                untracked_truncated = True
                continue
            output.append(candidate)
            known.add(candidate)
            untracked_added += 1
            untracked_bytes += size
    state = dict(state)
    state.update({
        "untracked_sources_added": untracked_added,
        "untracked_source_bytes": untracked_bytes,
        "untracked_sources_truncated": untracked_truncated,
    })
    return tuple(output), bool(truncated or untracked_truncated), state


def _repository_signature(root: Path) -> tuple[str, bool]:
    """Hash source paths and stat identities so cached relations cannot go stale."""
    resolved = root.expanduser().resolve(strict=True)
    files, truncated, state = _repository_review_files(resolved)
    digest = hashlib.sha256()
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b":")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b":")
        digest.update(
            str(_path_worktree_evidence(resolved, path, state)["worktree_status"])
            .encode("utf-8", errors="replace")
        )
        digest.update(b"\n")
    digest.update(b"truncated=" + (b"1" if truncated else b"0"))
    digest.update(
        b"untracked=" + str(state.get("untracked_sources_added", 0)).encode("ascii")
    )
    return digest.hexdigest(), truncated


def _read_repository_source(path: Path) -> tuple[list[str], str] | None:
    try:
        if path.stat().st_size > MAX_EVIDENCE_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return text.splitlines(), text


def discover_symbol_sources(
    repository_root: Path, symbols: Iterable[str], *, allow_missing: bool = False,
) -> dict[str, object]:
    """Locate exact definitions for a bounded set of requested symbols.

    The result is evidence only. It never guesses a path when no definition is
    found, and it reports repository inventory truncation explicitly.
    """
    root = repository_root.expanduser().resolve(strict=True)
    selected = tuple(dict.fromkeys(item.strip() for item in symbols if item.strip()))
    if not selected:
        raise ValueError("At least one symbol is required for repository discovery")
    if len(selected) > 32:
        raise ValueError("Repository symbol discovery is limited to 32 symbols")
    files, truncated, worktree = _repository_review_files(root)
    matches: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in selected}
    for path in files:
        loaded = _read_repository_source(path)
        if loaded is None:
            continue
        lines, text = loaded
        folded = text.casefold()
        for symbol in selected:
            if symbol.casefold() not in folded:
                continue
            expression = re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)
            for index, line in enumerate(lines):
                if expression.search(line) is None:
                    continue
                definition = _function_definition_window(lines, index, symbol)
                declaration = None if definition is not None else _declaration_window(
                    lines, index, symbol,
                )
                window = definition or declaration
                if window is None:
                    continue
                start, end = window
                record = {
                    "path": str(path.resolve()),
                    "line_start": start + 1,
                    "line_end": end,
                    "selection": "definition" if definition is not None else "declaration",
                    **_path_worktree_evidence(root, path, worktree),
                }
                if record not in matches[symbol]:
                    matches[symbol].append(record)
    missing = [symbol for symbol, records in matches.items() if not records]
    if missing and not allow_missing:
        suffix = " (repository scan reached its safety limit)" if truncated else ""
        raise ValueError(
            "Requested review symbols were not defined in the repository: "
            + ", ".join(missing) + suffix
        )
    sources = sorted({
        str(record["path"]) for records in matches.values() for record in records
    }, key=str.casefold)
    return {
        "repository_root": str(root), "symbols": list(selected),
        "symbol_sources": matches, "sources": sources,
        "missing_symbols": missing,
        "scanned_files": len(files), "scan_truncated": truncated,
        "worktree_status_available": bool(worktree.get("available")),
        "untracked_sources_added": int(worktree.get("untracked_sources_added", 0)),
        "untracked_source_bytes": int(worktree.get("untracked_source_bytes", 0)),
        "untracked_sources_truncated": bool(
            worktree.get("untracked_sources_truncated", False)
        ),
    }


def _line_windows(
    lines: list[str], matches: Iterable[int], *, context_lines: int,
) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for index in matches:
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def _numbered(lines: list[str], start: int, end: int) -> str:
    return "\n".join(f"{index + 1:>6}: {lines[index]}" for index in range(start, end))


def _code_for_braces(line: str) -> str:
    """Remove strings and line comments for a conservative brace count."""
    output: list[str] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            output.append(" ")
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            output.append(" ")
            index += 1
            continue
        if char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            break
        output.append(char)
        index += 1
    return "".join(output)


def _function_definition_window(
    lines: list[str], occurrence: int, symbol: str,
) -> tuple[int, int] | None:
    """Return a brace-balanced function or named-lambda definition."""
    line = lines[occurrence]
    lambda_match = re.search(
        rf"\b(?:const\s+)?auto\s+{re.escape(symbol)}\s*=\s*\[[^\]]*\]\s*\(",
        line, re.IGNORECASE,
    )
    match = lambda_match or re.search(
        rf"\b{re.escape(symbol)}\s*\(", line, re.IGNORECASE,
    )
    if not match:
        return None
    prefix = line[:match.start()].strip()
    lambda_definition = lambda_match is not None
    if not lambda_definition and (
        not prefix
        or re.search(r"\b(if|while|for|return|switch|case|catch)\b|[=!?:]", prefix)
    ):
        return None
    brace_line = -1
    brace_column = -1
    parameter_depth = 0
    parameter_opened = False
    parameter_closed = False
    suffix_depth = 0
    for index in range(occurrence, min(len(lines), occurrence + 16)):
        code = _code_for_braces(lines[index])
        start_column = match.end() - 1 if index == occurrence else 0
        for column in range(start_column, len(code)):
            char = code[column]
            if not parameter_closed:
                if char == "(":
                    parameter_depth += 1
                    parameter_opened = True
                elif char == ")" and parameter_opened:
                    parameter_depth -= 1
                    if parameter_depth == 0:
                        parameter_closed = True
                continue
            if char == "(":
                suffix_depth += 1
            elif char == ")":
                if suffix_depth == 0:
                    # An unmatched close belongs to an if/call expression, not
                    # a function signature (for example: call(...)) {).
                    return None
                suffix_depth -= 1
            elif suffix_depth == 0 and char == ";":
                return None
            elif suffix_depth == 0 and char == "=":
                return None
            elif suffix_depth == 0 and char == "{" :
                brace_line = index
                brace_column = column
                break
            elif suffix_depth == 0 and char in "&|" and column + 1 < len(code):
                if code[column + 1] == char:
                    return None
        if brace_line >= 0:
            break
    if brace_line < 0:
        return None
    start = occurrence
    while start > 0 and occurrence - start < 4:
        # Comments after a closing brace must not make an adjacent one-line
        # function look like a continuation of the requested definition.
        previous = _code_for_braces(lines[start - 1]).strip()
        if not previous or previous.endswith((";", "}", "{")):
            break
        start -= 1
    depth = 0
    opened = False
    for index in range(brace_line, len(lines)):
        code = _code_for_braces(lines[index])
        if index == brace_line:
            code = code[brace_column:]
        for char in code:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}" and opened:
                depth -= 1
                if depth == 0:
                    return start, index + 1
    return None


def _declaration_window(
    lines: list[str], occurrence: int, symbol: str,
) -> tuple[int, int] | None:
    """Return one bounded constant, field, macro, or type declaration.

    An exact ``--symbol`` may name data rather than a function. Treating that
    as an ordinary context window can hide the declaration among unrelated
    lines or make it the first text discarded during prompt compaction. This
    selector keeps the declaration itself as first-class evidence.
    """
    escaped = re.escape(symbol)
    line = lines[occurrence]
    code = _code_for_braces(line)
    declaration = (
        re.search(rf"^\s*#\s*define\s+{escaped}\b", code, re.IGNORECASE)
        or re.search(
            rf"\b(?:class|struct|interface|enum|record)\s+{escaped}\b",
            code, re.IGNORECASE,
        )
        or (
            re.search(r"\b(?:const|constexpr|constinit|readonly)\b", code, re.IGNORECASE)
            and re.search(rf"\b{escaped}\b\s*(?:=|;)", code, re.IGNORECASE)
        )
        or re.search(
            rf"^\s*{escaped}\s*(?::[^=]+)?=", code, re.IGNORECASE,
        )
    )
    if not declaration:
        return None
    start = occurrence
    while start > 0 and occurrence - start < 3:
        previous = lines[start - 1].strip()
        if previous.startswith(("[", "@")):
            start -= 1
            continue
        break
    end = occurrence + 1
    # Preserve a short multiline declaration, but never expand a type
    # declaration into its entire class body.
    is_type = re.search(
        rf"\b(?:class|struct|interface|enum|record)\s+{escaped}\b",
        code, re.IGNORECASE,
    ) is not None
    if not is_type and ";" not in code and not code.lstrip().startswith("#"):
        for index in range(occurrence + 1, min(len(lines), occurrence + 16)):
            end = index + 1
            if ";" in _code_for_braces(lines[index]):
                break
    return start, end


def _referenced_declarations(
    lines: list[str], definition_ranges: Iterable[tuple[int, int]],
    requested_symbols: Iterable[str],
) -> tuple[list[dict[str, object]], list[str], int]:
    """Retrieve immutable declarations read by selected definitions.

    Package identifiers and similar constants often live immediately above a
    class while the requested constructor or method appears much later. Their
    values are necessary to evaluate extension/package integration, but they
    are not counter dependencies. Keep those declarations separately and in
    full so a model cannot infer that an identifier is missing merely because
    its declaration sat outside a method window.
    """
    ranges = tuple(definition_ranges)
    if not ranges:
        return [], [], 0
    requested = {item.casefold() for item in requested_symbols}
    selected_text = "\n".join(
        line for start, end in ranges for line in lines[start:end]
    )
    declarations: list[dict[str, object]] = []
    identifiers: list[str] = []
    omitted = 0
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = _IMMUTABLE_DECLARATION.search(_code_for_braces(line))
        if match is None:
            continue
        identifier = match.group("name")
        key = identifier.casefold()
        if key in requested or key in seen:
            continue
        if re.search(rf"\b{re.escape(identifier)}\b", selected_text) is None:
            continue
        seen.add(key)
        if len(declarations) >= MAX_SOURCE_DECLARATIONS:
            omitted += 1
            continue
        window = _declaration_window(lines, index, identifier) or (index, index + 1)
        start, end = window
        declarations.append({
            "identifier": identifier,
            "role": "referenced_immutable_declaration",
            "line_start": start + 1,
            "line_end": end,
            "text": _numbered(lines, start, end),
            "truncated": False,
            "preserve_full": True,
        })
        identifiers.append(identifier)
    return declarations, identifiers, omitted


def _source_dependencies(
    lines: list[str], definition_ranges: Iterable[tuple[int, int]],
) -> tuple[list[dict[str, object]], list[str], int]:
    identifiers: list[str] = []
    for start, end in definition_ranges:
        for line in lines[start:end]:
            for match in _COUNTER_READ.finditer(line):
                identifier = match.group(1)
                if _COUNTER_HINT.search(identifier) and identifier not in identifiers:
                    identifiers.append(identifier)
    dependencies: list[dict[str, object]] = []
    omitted = 0
    for identifier in identifiers:
        escaped = re.escape(identifier)
        writer = re.compile(
            rf"\b{escaped}\b\s*(?:\.\s*(?:fetch_add|fetch_sub|store|exchange)\s*\(|"
            rf"(?:\+\+|--|[+\-*/]?=))"
        )
        reset = re.compile(
            rf"\b{escaped}\b\s*(?:\.\s*(?:store|exchange)\s*\(\s*0(?:[uUlL]*)\b|"
            rf"=\s*0(?:[uUlL]*)\b)"
        )
        for index, line in enumerate(lines):
            if not writer.search(line):
                continue
            if len(dependencies) >= MAX_SOURCE_DEPENDENCIES:
                omitted += 1
                continue
            start = max(0, index - 2)
            end = min(len(lines), index + 3)
            dependencies.append({
                "identifier": identifier,
                "role": "reset" if reset.search(line) else "writer",
                "line_start": start + 1,
                "line_end": end,
                "text": _numbered(lines, start, end),
            })
    return dependencies, identifiers, omitted


def _is_test_source(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    parts = {part.casefold() for part in relative.parts[:-1]}
    name = path.stem.casefold()
    return (
        bool(parts.intersection({"test", "tests", "spec", "specs"}))
        or name.startswith("test_") or name.endswith(("_test", "tests", "spec"))
    )


def _relationship_record(
    root: Path, path: Path, lines: list[str], index: int, *, role: str,
    symbol: str = "", identifier: str = "",
) -> dict[str, object]:
    start = max(0, index - 2)
    end = min(len(lines), index + 3)
    return {
        "role": role, "path": str(path.resolve()),
        "relative_path": path.relative_to(root).as_posix(),
        "symbol": symbol, "identifier": identifier,
        "line_start": start + 1, "line_end": end,
        "text": _numbered(lines, start, end), "truncated": False,
    }


def _repository_relationships(
    repository_root: Path, selected_path: Path, lines: list[str],
    selected_symbols: tuple[str, ...],
    definition_records: tuple[tuple[str, int, int], ...],
    *, priorities: tuple[str, ...],
) -> dict[str, object]:
    root = repository_root.expanduser().resolve(strict=True)
    source = selected_path.resolve(strict=True)
    if source != root and not source.is_relative_to(root):
        raise ValueError(f"Selected source is outside the review repository: {source}")
    unknown = [item for item in priorities if item not in REVIEW_PRIORITIES]
    if unknown:
        raise ValueError("Unknown source-review priorities: " + ", ".join(unknown))
    files, scan_truncated, worktree = _repository_review_files(root)
    callers: list[dict[str, object]] = []
    tests: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    omitted = {"callers": 0, "tests": 0, "state-transitions": 0}
    state_identifiers: list[str] = []
    transition_candidates: dict[str, list[tuple[int, int]]] = {}
    for symbol, start, end in definition_records:
        candidates: list[tuple[int, int]] = []
        for index in range(start, end):
            code = _code_for_braces(lines[index])
            for identifier in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", code):
                if (
                    _STATE_IDENTIFIER_HINT.search(identifier)
                    and identifier not in state_identifiers
                ):
                    state_identifiers.append(identifier)
            if "state-transitions" in priorities and _STATE_TRANSITION.search(code):
                score = 1
                folded = code.casefold()
                if "compare_exchange" in folded:
                    score += 6
                elif any(token in folded for token in (".store(", ".exchange(")):
                    score += 5
                elif any(token in folded for token in ("fetch_add", "fetch_sub")):
                    score += 4
                if any(identifier.casefold() in folded for identifier in state_identifiers):
                    score += 2
                candidates.append((score, index))
        transition_candidates[symbol] = candidates

    # A long publication routine must not consume the entire transition budget
    # before reset/retire and observe routines are represented. Allocate the
    # bounded slots round-robin across requested definitions, selecting the
    # strongest atomic/state mutations within each one.
    ranked_transitions = {
        symbol: sorted(candidates, key=lambda item: (-item[0], item[1]))
        for symbol, candidates in transition_candidates.items()
    }
    while len(transitions) < MAX_REPOSITORY_RELATIONSHIPS:
        added = False
        for symbol, _start, _end in definition_records:
            candidates = ranked_transitions.get(symbol, [])
            if not candidates or len(transitions) >= MAX_REPOSITORY_RELATIONSHIPS:
                continue
            _score, index = candidates.pop(0)
            transitions.append(_relationship_record(
                root, source, lines, index,
                role="selected_state_transition", symbol=symbol,
            ))
            added = True
        if not added:
            break
    omitted["state-transitions"] += max(
        0,
        sum(len(candidates) for candidates in transition_candidates.values())
        - len(transitions),
    )

    definition_ranges = tuple(
        (start, end) for _symbol, start, end in definition_records
    )

    symbol_patterns = {
        symbol: re.compile(rf"\b{re.escape(symbol)}\s*\(", re.IGNORECASE)
        for symbol in selected_symbols
    }
    state_patterns = {
        identifier: re.compile(rf"\b{re.escape(identifier)}\b", re.IGNORECASE)
        for identifier in state_identifiers[:32]
    }
    seen: dict[str, set[tuple[str, int, str]]] = {
        "callers": set(), "tests": set(), "state-transitions": set(),
    }
    seen["state-transitions"].update(
        (str(item["path"]), int(item["line_start"]), str(item.get("symbol", "")))
        for item in transitions
    )

    def append(kind: str, record: dict[str, object]) -> None:
        key = (str(record["path"]), int(record["line_start"]), str(record.get("symbol", "")))
        if key in seen[kind]:
            return
        seen[kind].add(key)
        target = callers if kind == "callers" else tests if kind == "tests" else transitions
        if len(target) >= MAX_REPOSITORY_RELATIONSHIPS:
            omitted[kind] += 1
            return
        target.append(record)

    for path in files:
        loaded = _read_repository_source(path)
        if loaded is None:
            continue
        candidate_lines, candidate_text = loaded
        folded = candidate_text.casefold()
        is_test = _is_test_source(path, root)
        if "tests" in priorities and is_test:
            for symbol in selected_symbols:
                if symbol.casefold() not in folded:
                    continue
                expression = re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)
                for index, line in enumerate(candidate_lines):
                    if expression.search(line):
                        append("tests", _relationship_record(
                            root, path, candidate_lines, index,
                            role="nearby_test", symbol=symbol,
                        ))
        if "callers" in priorities and not is_test:
            for symbol, expression in symbol_patterns.items():
                if symbol.casefold() not in folded:
                    continue
                for index, line in enumerate(candidate_lines):
                    if expression.search(line) is None:
                        continue
                    if path.resolve() == source and any(
                        start <= index < end for start, end in definition_ranges
                    ):
                        continue
                    if _function_definition_window(candidate_lines, index, symbol) is not None:
                        continue
                    append("callers", _relationship_record(
                        root, path, candidate_lines, index,
                        role="direct_caller", symbol=symbol,
                    ))
        if "state-transitions" in priorities and state_patterns:
            for index, line in enumerate(candidate_lines):
                code = _code_for_braces(line)
                if _STATE_TRANSITION.search(code) is None:
                    continue
                for identifier, expression in state_patterns.items():
                    if expression.search(code) is None:
                        continue
                    append("state-transitions", _relationship_record(
                        root, path, candidate_lines, index,
                        role="related_state_transition", identifier=identifier,
                    ))
                    break
    return {
        "review_priorities": list(priorities),
        "callers": callers, "tests": tests,
        "state_transitions": transitions,
        "relationship_omitted": omitted,
        "repository_scan_files": len(files),
        "repository_scan_truncated": scan_truncated,
        "repository_untracked_sources_added": int(
            worktree.get("untracked_sources_added", 0)
        ),
        "repository_untracked_sources_truncated": bool(
            worktree.get("untracked_sources_truncated", False)
        ),
        "state_identifiers": state_identifiers[:32],
    }


def inspect_source(
    path: Path, *, symbols: Iterable[str] = (), context_lines: int = 16,
    max_chars: int = MAX_EVIDENCE_CHARS,
    repository_root: Path | None = None,
    priorities: Iterable[str] = (),
) -> dict[str, object]:
    """Return complete requested definitions plus bounded references and dependencies."""
    if not 0 <= context_lines <= 200:
        raise ValueError("Source context lines must be between 0 and 200")
    if not 256 <= max_chars <= MAX_EVIDENCE_CHARS:
        raise ValueError(f"Source excerpt limit must be 256-{MAX_EVIDENCE_CHARS:,} characters")
    resolved, text, digest = _read_text(path)
    worktree_evidence: dict[str, object] = {}
    if repository_root is not None:
        root = repository_root.expanduser().resolve(strict=True)
        if resolved != root and not resolved.is_relative_to(root):
            raise ValueError(f"Selected source is outside the review repository: {resolved}")
        worktree_evidence = _path_worktree_evidence(
            root, resolved, _repository_worktree_state(root),
        )
    lines = text.splitlines()
    selected = tuple(dict.fromkeys(item.strip() for item in symbols if item.strip()))
    missing_symbols: list[str] = []
    excerpts: list[dict[str, object]] = []
    definition_ranges: list[tuple[int, int]] = []
    reference_windows: list[tuple[int, int]] = []
    for symbol in selected:
        expression = re.compile(re.escape(symbol), re.IGNORECASE)
        found = [index for index, line in enumerate(lines) if expression.search(line)]
        if not found:
            missing_symbols.append(symbol)
            continue
        definition: tuple[int, int] | None = None
        definition_occurrence = -1
        declaration: tuple[int, int] | None = None
        declaration_occurrence = -1
        for occurrence in found:
            candidate = _function_definition_window(lines, occurrence, symbol)
            if candidate is not None:
                definition = candidate
                definition_occurrence = occurrence
                break
            candidate = _declaration_window(lines, occurrence, symbol)
            if declaration is None and candidate is not None:
                declaration = candidate
                declaration_occurrence = occurrence
        if definition is None:
            if declaration is not None:
                definition = declaration
                definition_occurrence = declaration_occurrence
                selection = "declaration"
            else:
                start = max(0, found[0] - context_lines)
                end = min(len(lines), found[0] + context_lines + 1)
                definition = (start, end)
                definition_occurrence = found[0]
                selection = "occurrence"
        else:
            selection = "definition"
            definition_ranges.append(definition)
        start, end = definition
        text_value = _numbered(lines, start, end)
        if len(text_value) > MAX_EXPLICIT_SYMBOL_CHARS:
            raise ValueError(
                f"Requested symbol definition exceeds the {MAX_EXPLICIT_SYMBOL_CHARS:,} "
                f"character preservation limit: {symbol}"
            )
        excerpts.append({
            "symbol": symbol,
            "selection": selection,
            "symbol_line": definition_occurrence + 1,
            "line_start": start + 1,
            "line_end": end,
            "text": text_value,
            "truncated": False,
            "preserve_full": True,
        })
        for occurrence in found:
            if start <= occurrence < end:
                continue
            reference_windows.append((
                max(0, occurrence - min(2, context_lines)),
                min(len(lines), occurrence + min(2, context_lines) + 1),
            ))
    if not selected:
        start, end = 0, min(len(lines), max(1, context_lines * 2 + 1))
        excerpts.append({
            "symbol": "", "selection": "file_start", "symbol_line": None,
            "line_start": start + 1, "line_end": end,
            "text": _numbered(lines, start, end),
            "truncated": False, "preserve_full": False,
        })
    references: list[dict[str, object]] = []
    reference_used = 0
    omitted_windows = 0
    for start, end in sorted(set(reference_windows)):
        numbered = _numbered(lines, start, end)
        if reference_used + len(numbered) > max_chars:
            omitted_windows += 1
            continue
        references.append({
            "line_start": start + 1, "line_end": end,
            "text": numbered, "truncated": False,
        })
        reference_used += len(numbered)
    dependencies, dependency_identifiers, dependencies_omitted = _source_dependencies(
        lines, definition_ranges,
    )
    declarations, declaration_identifiers, declarations_omitted = (
        _referenced_declarations(lines, definition_ranges, selected)
    )
    selected_priorities = tuple(dict.fromkeys(
        item.strip().casefold() for item in priorities if item.strip()
    ))
    relationships: dict[str, object] = {}
    if selected_priorities:
        if repository_root is None:
            raise ValueError("Source-review priorities require a repository root")
        relationships = _repository_relationships(
            repository_root, resolved, lines, selected,
            tuple(
                (str(excerpt["symbol"]), int(excerpt["line_start"]) - 1,
                 int(excerpt["line_end"]))
                for excerpt in excerpts
                if excerpt.get("selection") == "definition" and excerpt.get("symbol")
            ),
            priorities=selected_priorities,
        )
    return {
        "kind": "source", "path": str(resolved), "sha256": digest,
        "symbols": list(selected), "missing_symbols": missing_symbols,
        "line_count": len(lines), "excerpts": excerpts,
        "references": references, "omitted_windows": omitted_windows,
        "dependency_identifiers": dependency_identifiers,
        "dependencies": dependencies,
        "dependencies_omitted": dependencies_omitted,
        "declaration_identifiers": declaration_identifiers,
        "declarations": declarations,
        "declarations_omitted": declarations_omitted,
        "explicit_symbols_preserved": all(
            not item["truncated"] and item["preserve_full"] for item in excerpts
            if item.get("symbol")
        ),
        **worktree_evidence,
        **relationships,
    }


def _metric_value(value: str) -> int | float:
    normalized = value.replace(",", "")
    number = float(normalized)
    return int(number) if number.is_integer() else number


def _pattern_metrics(line: str, pattern: str) -> dict[str, int | float]:
    match = re.search(re.escape(pattern), line, re.IGNORECASE)
    if match is None:
        return {}
    start = line.find("(", match.end())
    if start < 0 or start - match.end() > 8:
        return {}
    depth = 0
    end = len(line)
    for index in range(start, len(line)):
        if line[index] == "(":
            depth += 1
        elif line[index] == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    body = line[start + 1:end]
    return {
        metric.group(1): _metric_value(metric.group(2))
        for metric in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*)=(-?\d[\d,]*(?:\.\d+)?)\b", body,
        )
    }


def _update_metric(
    state: dict[str, object], value: int | float, line_number: int,
    *, cumulative_counter: bool,
) -> None:
    if not state:
        state.update({
            "samples": 1, "first": value, "last": value,
            "min": value, "max": value, "sum": value,
            "nonzero_samples": int(value != 0), "resets": 0,
            "first_line": line_number, "last_line": line_number,
            "peak_line": line_number,
        })
        if cumulative_counter:
            state.update({
                "counter_segments": 1,
                "observed_counter_total": max(0, value),
            })
        return
    previous = state["last"]
    state["samples"] = int(state["samples"]) + 1
    state["last"] = value
    state["last_line"] = line_number
    state["sum"] = state["sum"] + value
    state["nonzero_samples"] = int(state["nonzero_samples"]) + int(value != 0)
    if value < previous:
        state["resets"] = int(state["resets"]) + 1
        if cumulative_counter:
            state["counter_segments"] = int(state["counter_segments"]) + 1
            state["observed_counter_total"] = (
                state["observed_counter_total"] + max(0, value)
            )
    elif cumulative_counter:
        state["observed_counter_total"] = (
            state["observed_counter_total"] + max(0, value - previous)
        )
    if value < state["min"]:
        state["min"] = value
    if value > state["max"]:
        state["max"] = value
        state["peak_line"] = line_number


def inspect_log(
    path: Path, *, patterns: Iterable[str] = (), max_lines: int = 200,
    max_chars: int = MAX_EVIDENCE_CHARS,
) -> dict[str, object]:
    """Return bounded matching or trailing telemetry lines."""
    if not 1 <= max_lines <= 1000:
        raise ValueError("Log line limit must be between 1 and 1,000")
    if not 256 <= max_chars <= MAX_EVIDENCE_CHARS:
        raise ValueError(f"Log excerpt limit must be 256-{MAX_EVIDENCE_CHARS:,} characters")
    resolved, _size = _resolve_text_path(path, max_bytes=MAX_LOG_FILE_BYTES)
    selected = tuple(dict.fromkeys(item.strip() for item in patterns if item.strip()))
    expressions = [re.compile(re.escape(item), re.IGNORECASE) for item in selected]
    indexed: deque[tuple[int, str]] = deque(maxlen=max_lines)
    digest_builder = hashlib.sha256()
    line_count = 0
    matched_lines = 0
    oversized_lines = 0
    aggregate_state: dict[str, dict[str, dict[str, object]]] = {
        pattern: {} for pattern in selected
    }
    pattern_matches: dict[str, int] = {pattern: 0 for pattern in selected}
    with resolved.open("rb") as source:
        for raw_line in source:
            digest_builder.update(raw_line)
            line_count += 1
            if len(raw_line) > MAX_LOG_LINE_BYTES:
                oversized_lines += 1
                raw_line = raw_line[:MAX_LOG_LINE_BYTES]
            line = raw_line.decode("utf-8-sig", errors="replace").rstrip("\r\n")
            if expressions:
                matches = [expression.search(line) for expression in expressions]
                positions = [match.start() for match in matches if match is not None]
                if not positions:
                    continue
                matched_lines += 1
                for pattern, match in zip(selected, matches):
                    if match is None:
                        continue
                    pattern_matches[pattern] += 1
                    for metric, value in _pattern_metrics(line, pattern).items():
                        metric_state = aggregate_state[pattern].setdefault(metric, {})
                        counter_name = _COUNTER_HINT.search(metric) is not None and not re.search(
                            r"(?i)(?:^|_)(?:cap|limit|size|width|height|slots?)(?:_|$)",
                            metric,
                        )
                        _update_metric(
                            metric_state, value, line_count,
                            cumulative_counter=counter_name,
                        )
                # Keep the requested field in view even when telemetry is one huge line.
                center = min(positions)
                window = min(8_000, max_chars)
                # Put the selected field near the front so later context pruning
                # cannot retain only unrelated fields that preceded the match.
                start = max(0, center - min(512, window // 4))
                end = min(len(line), start + window)
                excerpt = line[start:end]
                if start:
                    excerpt = "...[earlier line content omitted]..." + excerpt
                if end < len(line):
                    excerpt += "...[later line content omitted]..."
                indexed.append((line_count - 1, excerpt))
            else:
                indexed.append((line_count - 1, line[:max_chars]))
    output: list[str] = []
    used = 0
    omitted = 0
    # Cumulative telemetry is normally most authoritative at the end of a run.
    # Newest-first order also ensures later evidence survives context compaction.
    for index, line in reversed(indexed):
        value = f"{index + 1:>6}: {line}"
        if used + len(value) + 1 > max_chars:
            omitted += 1
            continue
        output.append(value)
        used += len(value) + 1
    session_aggregates = [
        {
            "pattern": pattern,
            "matched_records": pattern_matches[pattern],
            "metrics": aggregate_state[pattern],
        }
        for pattern in selected
    ]
    return {
        "kind": "telemetry", "path": str(resolved),
        "sha256": digest_builder.hexdigest(),
        "patterns": list(selected), "line_count": line_count,
        "matched_lines": matched_lines if selected else len(indexed),
        "excerpt": "\n".join(output),
        "omitted_lines": omitted + max(0, matched_lines - len(indexed)),
        "oversized_lines": oversized_lines, "order": "newest_first",
        "aggregation_scope": "entire_selected_file",
        "session_aggregates": session_aggregates,
    }


def _numeric_metrics(text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        match = re.fullmatch(
            r"\s*([A-Za-z_][A-Za-z0-9_. /-]{0,100})\s*[:=]\s*"
            r"(-?\d+(?:\.\d+)?)\s*%?\s*", line,
        )
        if match:
            metrics[match.group(1).strip()] = float(match.group(2))
    return metrics


def compare_telemetry(baseline: Path, current: Path) -> dict[str, object]:
    """Compare exact numeric key/value telemetry without executing either input."""
    base_path, base_text, base_hash = _read_text(baseline)
    current_path, current_text, current_hash = _read_text(current)
    base = _numeric_metrics(base_text)
    now = _numeric_metrics(current_text)
    shared = sorted(base.keys() & now.keys(), key=str.casefold)
    changes = [
        {"metric": key, "baseline": base[key], "current": now[key],
         "delta": now[key] - base[key]}
        for key in shared
    ]
    return {
        "kind": "telemetry_comparison",
        "baseline": {"path": str(base_path), "sha256": base_hash},
        "current": {"path": str(current_path), "sha256": current_hash},
        "changes": changes,
        "baseline_only": sorted(base.keys() - now.keys(), key=str.casefold),
        "current_only": sorted(now.keys() - base.keys(), key=str.casefold),
    }


def _cache_key(
    kind: str, path: Path, values: tuple[object, ...],
) -> tuple[object, ...]:
    resolved = path.expanduser().resolve(strict=True)
    stat = resolved.stat()
    return (kind, str(resolved), stat.st_size, stat.st_mtime_ns, *values)


def _cached_evidence(
    key: tuple[object, ...], builder,
) -> dict[str, object]:
    with _EVIDENCE_CACHE_LOCK:
        cached = _EVIDENCE_CACHE.get(key)
        if cached is not None:
            _EVIDENCE_CACHE.move_to_end(key)
            output = json.loads(json.dumps(cached))
            output["cache_hit"] = True
            return output
    created = builder()
    with _EVIDENCE_CACHE_LOCK:
        _EVIDENCE_CACHE[key] = json.loads(json.dumps(created))
        _EVIDENCE_CACHE.move_to_end(key)
        while len(_EVIDENCE_CACHE) > EVIDENCE_CACHE_ENTRIES:
            _EVIDENCE_CACHE.popitem(last=False)
    output = json.loads(json.dumps(created))
    output["cache_hit"] = False
    return output


def cached_inspect_source(
    path: Path, *, symbols: Iterable[str] = (), context_lines: int = 16,
    max_chars: int = MAX_EVIDENCE_CHARS,
    repository_root: Path | None = None,
    priorities: Iterable[str] = (),
) -> dict[str, object]:
    """Cache unchanged source grounding inside a long-lived SDK/Agent process."""
    selected = tuple(dict.fromkeys(item.strip() for item in symbols if item.strip()))
    selected_priorities = tuple(dict.fromkeys(
        item.strip().casefold() for item in priorities if item.strip()
    ))
    repository = (
        repository_root.expanduser().resolve(strict=True)
        if repository_root is not None else None
    )
    repository_signature = _repository_signature(repository) if repository else ("", False)
    key = _cache_key(
        "source", path,
        (
            selected, context_lines, max_chars,
            str(repository) if repository else "", selected_priorities,
            *repository_signature,
        ),
    )
    return _cached_evidence(
        key, lambda: inspect_source(
            path, symbols=selected, context_lines=context_lines, max_chars=max_chars,
            repository_root=repository, priorities=selected_priorities,
        ),
    )


def cached_inspect_log(
    path: Path, *, patterns: Iterable[str] = (), max_lines: int = 200,
    max_chars: int = MAX_EVIDENCE_CHARS,
) -> dict[str, object]:
    """Cache unchanged session aggregation inside a long-lived SDK/Agent process."""
    selected = tuple(dict.fromkeys(item.strip() for item in patterns if item.strip()))
    key = _cache_key("telemetry", path, (selected, max_lines, max_chars))
    return _cached_evidence(
        key, lambda: inspect_log(
            path, patterns=selected, max_lines=max_lines, max_chars=max_chars,
        ),
    )


def clear_evidence_cache() -> None:
    with _EVIDENCE_CACHE_LOCK:
        _EVIDENCE_CACHE.clear()


def _compact_session_aggregates(records: object) -> list[dict[str, object]]:
    """Preserve aggregate meaning with a prompt-efficient metric representation."""
    compacted: list[dict[str, object]] = []
    if not isinstance(records, list):
        return compacted
    for raw in records:
        if not isinstance(raw, dict):
            continue
        if "active_metrics" in raw:
            # Context planning may tighten excerpt text in multiple passes.
            # Aggregate compaction must be idempotent across those passes.
            compacted.append(json.loads(json.dumps(raw)))
            continue
        active: dict[str, dict[str, object]] = {}
        zero_metrics: list[str] = []
        constants: dict[str, int | float] = {}
        metrics = raw.get("metrics", {})
        if isinstance(metrics, dict):
            for name, state in metrics.items():
                if not isinstance(state, dict):
                    continue
                minimum = state.get("min")
                maximum = state.get("max")
                if minimum == maximum == 0:
                    zero_metrics.append(str(name))
                    continue
                if minimum == maximum and "observed_counter_total" not in state:
                    constants[str(name)] = maximum
                    continue
                fields = (
                    ("last", "max", "resets", "peak_line", "observed_counter_total")
                    if "observed_counter_total" in state
                    else ("first", "last", "min", "max", "resets", "peak_line")
                )
                active[str(name)] = {
                    key: state[key] for key in fields if key in state
                }
        compacted.append({
            "pattern": raw.get("pattern"),
            "matched_records": raw.get("matched_records"),
            "active_metrics": active,
            "constant_metrics": constants,
            "all_zero_metrics": zero_metrics,
            "scope": "all_matches; counter_total_includes_resets",
        })
    return compacted


def compact_grounding(record: dict[str, object], *, max_chars: int) -> dict[str, object]:
    """Shrink excerpt text while retaining hashes, locations, and omission evidence."""
    payload = json.loads(json.dumps(record))
    if payload.get("kind") == "source":
        excerpts = payload.get("excerpts", [])
        symbols = [str(item) for item in payload.get("symbols", []) if str(item)]
        compactable = [item for item in excerpts if not item.get("preserve_full")]
        per_excerpt = max(256, max_chars // max(1, len(compactable)))
        remaining = max_chars
        for excerpt in excerpts:
            text = str(excerpt.get("text", ""))
            if excerpt.get("preserve_full"):
                continue
            limit = min(per_excerpt, remaining)
            if len(text) <= limit:
                compacted = text
            else:
                positions = [
                    text.casefold().find(symbol.casefold()) for symbol in symbols
                    if text.casefold().find(symbol.casefold()) >= 0
                ]
                center = ((min(positions) + max(positions)) // 2) if positions else 0
                start = max(0, center - (limit // 3))
                end = min(len(text), start + limit)
                start = max(0, end - limit)
                compacted = text[start:end]
                if start:
                    compacted = "...[earlier source omitted]..." + compacted
                if end < len(text):
                    compacted += "...[later source omitted]..."
                excerpt["truncated"] = True
            excerpt["text"] = compacted[:remaining]
            remaining = max(0, remaining - len(excerpt["text"]))
        for collection in (
            "callers", "state_transitions", "tests", "references", "dependencies",
        ):
            records = payload.get(collection, [])
            if not isinstance(records, list):
                continue
            share = max(128, remaining // max(1, len(records)))
            for item in records:
                text = str(item.get("text", ""))
                item["text"] = text[:share]
                if len(text) > share:
                    item["truncated"] = True
                remaining = max(0, remaining - len(item["text"]))
    elif payload.get("kind") == "telemetry":
        text = str(payload.get("excerpt", ""))
        payload["excerpt"] = text[:max_chars]
        if len(text) > max_chars:
            payload["omitted_lines"] = int(payload.get("omitted_lines", 0)) + 1
        payload["session_aggregates"] = _compact_session_aggregates(
            payload.get("session_aggregates", []),
        )
    return payload


def compact_explicit_symbols(
    record: dict[str, object], *, max_chars: int, query: str = "",
) -> dict[str, object]:
    """Budget requested definitions without dropping any grounded symbol.

    Full brace-balanced definitions remain the normal evidence. This stricter
    compactor is only used by prompt admission control after unrelated excerpts,
    operation details, references, and dependencies have already been reduced.
    Each requested symbol retains its numbered declaration and ending. When the
    question contains terms found inside the definition, the highest-scoring
    numbered middle lines are retained before generic head/tail context. The
    host still reports that omitted lines are not confirmation evidence.
    """
    payload = json.loads(json.dumps(record))
    if payload.get("kind") != "source":
        return compact_grounding(payload, max_chars=max_chars)
    excerpts = [
        item for item in payload.get("excerpts", [])
        if isinstance(item, dict) and item.get("symbol")
    ]
    if not excerpts:
        return compact_grounding(payload, max_chars=max_chars)
    if max_chars < len(excerpts) * 128:
        raise ValueError(
            "Explicit symbol compaction requires at least 128 characters per symbol"
        )
    per_excerpt = max_chars // len(excerpts)
    query_terms = {
        item.casefold() for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)
        if item.casefold() not in _COMPACTION_STOPWORDS
    }
    compacted_symbols = [
        str(item) for item in payload.get("compacted_symbols", []) if str(item)
    ]
    was_compacted = any(
        item.get("truncated") or not item.get("preserve_full") for item in excerpts
    )
    for excerpt in excerpts:
        text = str(excerpt.get("text", ""))
        if len(text) <= per_excerpt:
            continue
        lines = text.splitlines()
        symbol = str(excerpt.get("symbol", ""))
        symbol_terms = {
            item.casefold()
            for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", symbol)
        }
        relevant_terms = query_terms - symbol_terms

        def render(indices: set[int]) -> tuple[str, list[dict[str, int]]]:
            selected = sorted(indices)
            rendered: list[str] = []
            retained: list[dict[str, int]] = []
            previous = -1
            range_start = -1
            range_end = -1
            for index in selected:
                if previous >= 0 and index > previous + 1:
                    previous_match = re.match(r"^\s*(\d+):", lines[previous])
                    next_match = re.match(r"^\s*(\d+):", lines[index])
                    omitted_start = (
                        int(previous_match.group(1)) + 1
                        if previous_match else previous + 2
                    )
                    omitted_end = (
                        int(next_match.group(1)) - 1
                        if next_match else index
                    )
                    rendered.append(
                        "...[requested definition lines "
                        f"{omitted_start}-{omitted_end} omitted by context budget; "
                        "not confirmation evidence]..."
                    )
                    if range_start >= 0:
                        retained.append({"line_start": range_start, "line_end": range_end})
                    range_start = -1
                match = re.match(r"^\s*(\d+):", lines[index])
                line_number = int(match.group(1)) if match else index + 1
                rendered.append(lines[index])
                if range_start < 0:
                    range_start = line_number
                range_end = line_number
                previous = index
            if range_start >= 0:
                retained.append({"line_start": range_start, "line_end": range_end})
            return "\n".join(rendered), retained

        # The declaration and final decision stay represented. Query-matching
        # middle lines, plus one adjacent line, consume the remaining budget
        # before generic head/tail context does.
        selected = {0, len(lines) - 1}
        scored: list[tuple[int, int]] = []
        matched_terms: set[str] = set()
        for index, line in enumerate(lines[1:-1], start=1):
            folded = line.casefold()
            matches = {term for term in relevant_terms if term in folded}
            if matches:
                matched_terms.update(matches)
                scored.append((sum(len(term) for term in matches), index))
        for _score, index in sorted(scored, key=lambda item: (-item[0], item[1])):
            candidate = set(selected)
            candidate.update(range(max(0, index - 1), min(len(lines), index + 2)))
            candidate_text, _ranges = render(candidate)
            if len(candidate_text) <= per_excerpt:
                selected = candidate
        for index in (1, len(lines) - 2):
            if 0 <= index < len(lines):
                candidate = {*selected, index}
                candidate_text, _ranges = render(candidate)
                if len(candidate_text) <= per_excerpt:
                    selected = candidate
        compacted_text, retained_ranges = render(selected)
        if len(compacted_text) > per_excerpt:
            # Extremely long individual source lines cannot be represented as
            # complete numbered lines. Keep a bounded declaration/end signal
            # and deliberately publish no retained ranges for confirmation.
            marker = "\n...[requested definition omitted; not confirmation evidence]...\n"
            content_limit = max(32, per_excerpt - len(marker))
            head = max(16, content_limit // 2)
            tail = max(16, content_limit - head)
            compacted_text = text[:head] + marker + text[-tail:]
            retained_ranges = []
        excerpt["text"] = compacted_text[:per_excerpt]
        excerpt["truncated"] = True
        excerpt["preserve_full"] = False
        excerpt["compaction"] = (
            "query_ranked_numbered_windows" if matched_terms
            else "numbered_head_and_tail"
        )
        excerpt["retained_line_ranges"] = retained_ranges
        excerpt["query_terms_retained"] = sorted(matched_terms)
        if symbol and symbol not in compacted_symbols:
            compacted_symbols.append(symbol)
    for collection in (
        "callers", "state_transitions", "tests", "references", "dependencies",
    ):
        for item in payload.get(collection, []):
            if isinstance(item, dict) and item.get("text"):
                item["text"] = ""
                item["truncated"] = True
    payload["explicit_symbols_preserved"] = not (was_compacted or compacted_symbols)
    payload["compacted_symbols"] = compacted_symbols
    payload["confirmation_supported"] = payload["explicit_symbols_preserved"]
    return payload

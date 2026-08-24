"""Provider-neutral prompt client for the optional ALLIN1 assistant component.

The SDK can talk to a compatible API directly or start a configured
llama.cpp-compatible Windows server for a managed/custom GGUF model. Prompting
is deliberately read-only: no SDK command tools are exposed to the model by
this module.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from allin1_sdk import __version__
from allin1_sdk.paths import project_root
from allin1_sdk.processes import hidden_process_options
from allin1_sdk.assistant_context import (
    AssistantContextBundle, build_assistant_context,
)
from allin1_sdk.assistant_evidence import compact_explicit_symbols, compact_grounding


ASSISTANT_CONFIG = "config.json"
ASSISTANT_COMPONENT = "component"
ASSISTANT_MANIFEST = "assistant-package.json"
ASSISTANT_MODES = ("disabled", "managed_local", "custom_local", "compatible_api")
MAX_PROMPT_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CONTEXT_SAFETY_TOKENS = 512
CONTEXT_SAFETY_RATIO = 0.03
CONTEXT_METADATA_TOKENS = 256
LOCAL_RUNTIME_KEEPALIVE_SECONDS = 120.0
MAX_ASSISTANT_RECEIPTS = 50
MAX_ASSISTANT_RECEIPT_BYTES = 5 * 1024 * 1024
ASSISTANT_RESPONSE_SCHEMA_VERSION = 1
CAPABILITY_JSON_SCHEMA = "structured_output.json_schema"
CAPABILITY_THINKING_REASONING = "thinking.reasoning_effort"
CAPABILITY_THINKING_TEMPLATE = "thinking.chat_template_kwargs"
CAPABILITY_THINKING_QWEN = "thinking.enable_thinking"
CAPABILITY_PROMPT_CACHE = "prompt_cache"
PROVIDER_CAPABILITIES = frozenset({
    CAPABILITY_JSON_SCHEMA,
    CAPABILITY_THINKING_REASONING,
    CAPABILITY_THINKING_TEMPLATE,
    CAPABILITY_THINKING_QWEN,
    CAPABILITY_PROMPT_CACHE,
})
_THINKING_CAPABILITIES = frozenset({
    CAPABILITY_THINKING_REASONING,
    CAPABILITY_THINKING_TEMPLATE,
    CAPABILITY_THINKING_QWEN,
})
DEFAULT_SYSTEM_PROMPT = (
    "You are the embedded ALLIN1 SDK documentation and package-development assistant. "
    "Use only the authoritative context and exact typed operations supplied by the host. "
    "Never manually copy managed package files into GTA V and never bypass manifests, "
    "payload validation, signatures or hashes, receipts, ownership, backups, or rollback. "
    "The launcher owns game installation and package lifecycle; the SDK owns authoring, "
    "inspection, validation, and guarded operations; a VR repository produces a mod "
    "package and is not a launcher. Never invent a game path, repository role, SDK API, "
    "command, file state, or completed action. Preserve dirty worktrees and unrelated "
    "changes. Prompting is read-only and has no execution authority. Clearly distinguish "
    "verified evidence, inference, speculation, missing context, and abstention. You may "
    "propose a source-code change in advisory form, but a proposal is never authorization "
    "or execution. Do not recommend inspecting evidence already present in selected_grounding. "
    "If grounding_preflight.explicit_symbols_preserved is false, omitted source lines are "
    "missing context and cannot support a confirmed finding."
)
RESPONSE_SCHEMA_PROMPT = """Return exactly one JSON object with this schema and no prose fence:
{
  "summary": "concise answer",
  "findings": [{
    "severity_domain": "engineering|security",
    "severity": "info|low|medium|high|blocker|critical",
    "evidence": "specific retrieved evidence",
    "file": "path or empty string",
    "line": null,
    "confidence": 0.0,
    "status": "confirmed|inferred|speculative"
  }],
  "recommended_operations": [{
    "operation": "exact relevant_operations name",
    "arguments": ["literal argument"],
    "rationale": "why this exact operation fits the question and evidence",
    "expected_result": "what the operation should report"
  }],
  "proposed_changes": [{
    "file": "exact grounded source path",
    "symbol": "grounded symbol or empty string",
    "summary": "specific conceptual code change; no patch or shell command",
    "rationale": "why the evidence supports this proposal",
    "engineering_severity": "info|low|medium|high|blocker"
  }],
  "missing_context": ["evidence still needed"],
  "abstentions": ["action withheld and why"]
}
Do not emit shell commands or raw file-copy instructions. If no listed operation fits,
abstain instead of inventing one. A proposed code change is allowed in advisory or
planning mode and must remain executed=false. Do not claim an operation ran."""
ASSISTANT_RESPONSE_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ALLIN1 assistant advisory",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 800},
        "findings": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "severity_domain": {
                        "type": "string", "enum": ["engineering", "security"],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["info", "low", "medium", "high", "blocker", "critical"],
                    },
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "file": {"type": "string", "maxLength": 512},
                    "line": {"type": ["integer", "null"], "minimum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "status": {
                        "type": "string",
                        "enum": ["confirmed", "inferred", "speculative"],
                    },
                },
                "required": [
                    "severity_domain", "severity", "evidence", "file", "line",
                    "confidence", "status",
                ],
            },
        },
        "recommended_operations": {
            "type": "array", "maxItems": 6,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "operation": {"type": "string", "minLength": 1, "maxLength": 128},
                    "arguments": {
                        "type": "array", "maxItems": 16,
                        "items": {"type": "string", "maxLength": 512},
                    },
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "expected_result": {"type": "string", "maxLength": 800},
                },
                "required": ["operation", "arguments", "rationale", "expected_result"],
            },
        },
        "proposed_changes": {
            "type": "array", "maxItems": 6,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "file": {"type": "string", "minLength": 1, "maxLength": 512},
                    "symbol": {"type": "string", "maxLength": 256},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 800},
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "engineering_severity": {
                        "type": "string",
                        "enum": ["info", "low", "medium", "high", "blocker"],
                    },
                },
                "required": [
                    "file", "symbol", "summary", "rationale", "engineering_severity",
                ],
            },
        },
        "missing_context": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "maxLength": 800},
        },
        "abstentions": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "maxLength": 800},
        },
    },
    "required": [
        "summary", "findings", "recommended_operations", "proposed_changes",
        "missing_context", "abstentions",
    ],
}
ASSISTANT_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": f"allin1_assistant_advisory_v{ASSISTANT_RESPONSE_SCHEMA_VERSION}",
        "strict": True,
        "schema": ASSISTANT_RESPONSE_SCHEMA,
    },
}


def _compact_advisory_schema() -> dict[str, object]:
    """Return the same advisory contract with deliberately smaller bounds."""
    schema = json.loads(json.dumps(ASSISTANT_RESPONSE_SCHEMA))
    properties = schema["properties"]
    properties["summary"]["maxLength"] = 480
    properties["findings"]["maxItems"] = 4
    properties["recommended_operations"]["maxItems"] = 2
    properties["proposed_changes"]["maxItems"] = 3
    properties["missing_context"]["maxItems"] = 4
    properties["abstentions"]["maxItems"] = 4
    properties["findings"]["items"]["properties"]["evidence"]["maxLength"] = 600
    properties["recommended_operations"]["items"]["properties"]["arguments"][
        "maxItems"
    ] = 8
    properties["recommended_operations"]["items"]["properties"]["rationale"][
        "maxLength"
    ] = 500
    properties["recommended_operations"]["items"]["properties"]["expected_result"][
        "maxLength"
    ] = 400
    properties["proposed_changes"]["items"]["properties"]["summary"][
        "maxLength"
    ] = 500
    properties["proposed_changes"]["items"]["properties"]["rationale"][
        "maxLength"
    ] = 600
    properties["missing_context"]["items"]["maxLength"] = 400
    properties["abstentions"]["items"]["maxLength"] = 400
    return schema


ASSISTANT_REVIEW_RESPONSE_SCHEMA = _compact_advisory_schema()
ASSISTANT_REVIEW_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": f"allin1_assistant_review_v{ASSISTANT_RESPONSE_SCHEMA_VERSION}",
        "strict": True,
        "schema": ASSISTANT_REVIEW_RESPONSE_SCHEMA,
    },
}
ASSISTANT_REPAIR_RESPONSE_SCHEMA = _compact_advisory_schema()
ASSISTANT_REPAIR_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": f"allin1_assistant_repair_v{ASSISTANT_RESPONSE_SCHEMA_VERSION}",
        "strict": True,
        "schema": ASSISTANT_REPAIR_RESPONSE_SCHEMA,
    },
}
_SEVERITIES = frozenset({"info", "low", "medium", "high", "blocker", "critical"})
_SEVERITY_DOMAINS = frozenset({"engineering", "security"})
_EVIDENCE_STATES = frozenset({"confirmed", "inferred", "speculative"})
_UNSAFE_GUIDANCE = (
    re.compile(
        r"(?i)\b(copy-item|xcopy|robocopy|copy|drag)\b.{0,180}"
        r"(gta5|grand theft auto|\\scripts\b|/scripts\b|\.asi\b|\.dll\b)"
    ),
    re.compile(r"(?i)\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)\b"),
    re.compile(r"(?i)\b(rm\s+-rf|remove-item\b.{0,80}-recurse)\b"),
)
_NEGATED_SAFETY = re.compile(
    r"(?i)\b(do not|don't|never|must not|should not|avoid|refuse|blocked|bypass)\b"
)


@dataclass(frozen=True)
class AssistantSettings:
    root: Path
    mode: str
    workflow: str
    profile: str
    endpoint: str
    model_name: str
    api_key_env: str
    runtime_path: str
    model_path: str
    context_tokens: int
    temperature: float
    provider_capabilities: tuple[str, ...] = ()
    thinking: str = "provider_default"
    model_sha256: str = ""
    llama_cpp_revision: str = ""

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"


@dataclass(frozen=True)
class LocalRuntimeSpec:
    runtime: Path
    model: Path
    model_name: str
    context_tokens: int
    model_sha256: str = ""
    llama_cpp_revision: str = ""

    @property
    def identity(self) -> tuple[str, str, int]:
        return (str(self.runtime), str(self.model), self.context_tokens)


@dataclass(frozen=True)
class PromptResult:
    text: str
    model: str
    mode: str
    elapsed_seconds: float
    advisory: Mapping[str, object] | None = None
    context: Mapping[str, object] | None = None
    safety_flags: tuple[str, ...] = ()
    estimated_input_tokens: int = 0
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    reserved_output_tokens: int = 0
    startup_seconds: float = 0.0
    grounding_seconds: float = 0.0
    primary_inference_seconds: float = 0.0
    repair_seconds: float = 0.0
    inference_seconds: float = 0.0
    repair_attempts: tuple[Mapping[str, object], ...] = ()
    truncated: bool = False
    omitted_context: tuple[str, ...] = ()
    receipt_path: str = ""
    assistant_schema_version: int = ASSISTANT_RESPONSE_SCHEMA_VERSION
    sdk_build_id: str = ""
    model_sha256: str = ""
    llama_cpp_revision: str = ""
    provider_capabilities: tuple[str, ...] = ()
    thinking: str = "provider_default"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredPromptResult:
    payload: Mapping[str, object]
    model: str
    mode: str
    elapsed_seconds: float
    repaired: bool = False
    truncated: bool = False
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    sdk_build_id: str = ""
    model_sha256: str = ""
    llama_cpp_revision: str = ""
    provider_capabilities: tuple[str, ...] = ()
    thinking: str = "provider_default"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewResult:
    symbols: tuple[str, ...]
    priorities: tuple[str, ...]
    source_discovery: Mapping[str, object]
    chunks: tuple[Mapping[str, object], ...]
    advisory: Mapping[str, object]
    synthesis_method: str = "deterministic_validated_chunk_merge"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AssistantContextOverflow(ValueError):
    """Structured pre-inference refusal when the prompt cannot fit safely."""

    def __init__(self, details: Mapping[str, object]) -> None:
        self.details = dict(details)
        super().__init__(str(self.details.get("message", "Assistant context exceeds the model limit")))


@dataclass(frozen=True)
class GroundingPlan:
    system_prompt: str
    context: Mapping[str, object]
    estimated_input_tokens: int
    input_budget_tokens: int
    reserved_output_tokens: int
    omitted_context: tuple[str, ...]
    truncated: bool


class _ProgressHeartbeat:
    def __init__(
        self, callback: Callable[[str], None], state: str, *, interval: float = 10.0,
    ) -> None:
        self.callback = callback
        self.state = state
        self.interval = interval
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def __enter__(self):
        self._started = time.monotonic()

        def pulse() -> None:
            while not self._stopped.wait(self.interval):
                elapsed = int(time.monotonic() - self._started)
                self.callback(f"{self.state} ({elapsed}s)")

        self._thread = threading.Thread(target=pulse, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=1)


def estimate_tokens(value: str) -> int:
    """Conservative UTF-8 estimate suitable for preflight admission control."""
    return max(1, math.ceil(len(value.encode("utf-8")) / 3))


def _context_safety_tokens(context_tokens: int) -> int:
    """Reserve fixed and proportional headroom for provider tokenization drift."""
    return max(
        CONTEXT_SAFETY_TOKENS,
        math.ceil(context_tokens * CONTEXT_SAFETY_RATIO),
    )


def _grounding_preflight(context: Mapping[str, object]) -> dict[str, object]:
    requested_symbols: set[str] = set()
    preserved_symbols: set[str] = set()
    compacted: list[str] = []
    for raw in context.get("selected_grounding", []):
        if not isinstance(raw, Mapping) or raw.get("kind") != "source":
            continue
        symbols = [str(item) for item in raw.get("symbols", []) if str(item)]
        requested_symbols.update(item.casefold() for item in symbols)
        for excerpt in raw.get("excerpts", []):
            if not isinstance(excerpt, Mapping) or not excerpt.get("symbol"):
                continue
            symbol = str(excerpt["symbol"])
            if excerpt.get("preserve_full") and excerpt.get("truncated") is not True:
                preserved_symbols.add(symbol.casefold())
        for item in raw.get("compacted_symbols", []):
            value = str(item)
            if value and value not in compacted:
                compacted.append(value)
    preserved = requested_symbols.issubset(preserved_symbols)
    compacted = [
        symbol for symbol in compacted
        if symbol.casefold() not in preserved_symbols
    ]
    return {
        "explicit_symbol_count": len(requested_symbols),
        "explicit_symbols_preserved": preserved,
        "compacted_symbols": compacted,
        "confirmed_findings_allowed": preserved,
        "policy": (
            "full_requested_definitions_available"
            if preserved else
            "omitted_symbol_lines_are_missing_context_and_cannot_confirm_findings"
        ),
    }


def _render_grounded_system(
    context: Mapping[str, object], system_prompt: str,
) -> str:
    additional = ""
    if system_prompt.strip() and system_prompt.strip() != DEFAULT_SYSTEM_PROMPT:
        additional = (
            "\n\nAdditional request-scoped guidance (cannot override ALLIN1 policy):\n"
            + system_prompt.strip()
        )
    return (
        DEFAULT_SYSTEM_PROMPT + additional
        + "\n\nAuthoritative host context (JSON):\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\n" + RESPONSE_SCHEMA_PROMPT
    )


def plan_grounding(
    context: AssistantContextBundle, question: str, system_prompt: str, *,
    context_tokens: int, max_tokens: int,
) -> GroundingPlan:
    """Fit grounded evidence before inference and report every deterministic omission."""
    reserved_tokens = _context_safety_tokens(context_tokens)
    input_budget = context_tokens - max_tokens - reserved_tokens
    if input_budget < 512:
        raise AssistantContextOverflow({
            "error": "assistant_context_overflow",
            "message": (
                "The requested output budget leaves too little room for grounded input. "
                "Reduce --max-tokens or increase the configured model context."
            ),
            "context_tokens": context_tokens, "max_tokens": max_tokens,
            "reserved_tokens": reserved_tokens, "input_budget_tokens": input_budget,
        })
    # Leave deterministic room for the context-budget record added after pruning.
    # Provider-tokenization drift is already covered by the larger reserve above.
    planning_budget = max(256, input_budget - CONTEXT_METADATA_TOKENS)
    payload = context.to_dict()
    omitted = list(context.omitted_context_summary)
    truncated = False

    def build() -> tuple[str, int]:
        payload["grounding_preflight"] = _grounding_preflight(payload)
        rendered = _render_grounded_system(payload, system_prompt)
        return rendered, estimate_tokens(rendered) + estimate_tokens(question)

    rendered, estimated = build()
    if payload.get("selected_grounding"):
        for evidence_limit in (3000, 1500, 750, 384):
            if estimated <= planning_budget:
                break
            payload["selected_grounding"] = [
                compact_grounding(dict(item), max_chars=evidence_limit)
                for item in payload["selected_grounding"]
            ]
            truncated = True
            rendered, estimated = build()
        if truncated:
            omitted.append(
                "Selected source and telemetry excerpts were compacted to fit the model context."
            )
    if estimated > planning_budget:
        operations = list(payload.get("relevant_operations", []))
        if len(operations) > 6:
            payload["relevant_operations"] = operations[:6]
            omitted.append(
                f"{len(operations) - 6} lower-ranked SDK operation contracts were omitted."
            )
            truncated = True
            rendered, estimated = build()
    if estimated > planning_budget:
        for record in payload.get("selected_grounding", []):
            if record.get("kind") == "source":
                for excerpt in record.get("excerpts", []):
                    if excerpt.get("preserve_full"):
                        continue
                    excerpt["text"] = ""
                    excerpt["truncated"] = True
                # Generic references and declarations are lower-value than an
                # exact definition, its writers, direct callers, state
                # transitions, and tests. Drop them first under pressure.
                for collection in ("references", "declarations"):
                    for item in record.get(collection, []):
                        item["text"] = ""
                        item["truncated"] = True
            elif record.get("kind") == "telemetry":
                record["excerpt"] = ""
        omitted.append(
            "Low-priority generic references/declarations and non-symbol excerpts were "
            "omitted; exact definitions, callers, state transitions, tests, counter "
            "dependencies, hashes, aggregates, and locations remain."
        )
        truncated = True
        rendered, estimated = build()
    if estimated > planning_budget:
        payload["relevant_operations"] = [
            {"name": item.get("name"), "risk": item.get("risk")}
            for item in payload.get("relevant_operations", [])
        ]
        omitted.append("SDK operation contracts were reduced to exact names and risk classes.")
        truncated = True
        rendered, estimated = build()
    if estimated > planning_budget:
        removed_records = 0
        for record in payload.get("selected_grounding", []):
            if not isinstance(record, dict) or record.get("kind") != "source":
                continue
            for collection in ("references", "declarations"):
                values = record.get(collection, [])
                if isinstance(values, list):
                    removed_records += len(values)
                    record[collection] = []
            symbols = [str(item) for item in record.get("symbols", []) if str(item)]
            relationship_omitted = record.setdefault("relationship_omitted", {})
            for collection, omitted_key in (
                ("callers", "callers"), ("tests", "tests"),
                ("state_transitions", "state-transitions"),
            ):
                values = record.get(collection, [])
                if not isinstance(values, list) or len(values) <= 6:
                    continue
                retained: list[object] = []
                for symbol in symbols:
                    retained.extend([
                        item for item in values
                        if isinstance(item, Mapping)
                        and str(item.get("symbol", "")).casefold() == symbol.casefold()
                    ][:2])
                retained.extend(
                    item for item in values
                    if isinstance(item, Mapping) and not item.get("symbol")
                )
                retained = retained[:max(6, len(symbols) * 2)]
                removed = len(values) - len(retained)
                if removed > 0:
                    record[collection] = retained
                    removed_records += removed
                    if isinstance(relationship_omitted, dict):
                        relationship_omitted[omitted_key] = int(
                            relationship_omitted.get(omitted_key, 0)
                        ) + removed
        if removed_records:
            omitted.append(
                f"{removed_records} lower-ranked declaration/reference/relationship "
                "records were omitted before considering requested symbol compaction."
            )
            truncated = True
            rendered, estimated = build()
    if estimated > planning_budget:
        # A few large brace-balanced definitions should not prevent a useful
        # advisory answer. Only after every less-authoritative field has been
        # reduced, retain a bounded numbered beginning/end for every explicitly
        # grounded symbol. No symbol is silently dropped.
        source_records = [
            item for item in payload.get("selected_grounding", [])
            if item.get("kind") == "source"
        ]
        symbol_count = sum(
            1 for record in source_records
            for excerpt in record.get("excerpts", [])
            if isinstance(excerpt, Mapping) and excerpt.get("symbol")
        )
        if symbol_count:
            for per_symbol in (1800, 1200, 800, 512, 320, 192, 128):
                if estimated <= planning_budget:
                    break
                for index, record in enumerate(payload.get("selected_grounding", [])):
                    if record.get("kind") != "source":
                        continue
                    record_symbols = sum(
                        1 for excerpt in record.get("excerpts", [])
                        if isinstance(excerpt, Mapping) and excerpt.get("symbol")
                    )
                    if not record_symbols:
                        continue
                    payload["selected_grounding"][index] = compact_explicit_symbols(
                        dict(record), max_chars=per_symbol * record_symbols,
                        query=question,
                    )
                truncated = True
                rendered, estimated = build()
            omitted.append(
                "explicit_symbols_preserved=false: one or more requested definitions were "
                "reduced to query-ranked numbered windows only after full definitions could "
                "not fit; every requested symbol remains represented, but omitted lines are "
                "missing context and cannot support confirmed findings."
            )

    # Near-fit requests should degrade deterministically instead of being
    # refused because a generic command, repository field, or relationship
    # excerpt consumed the final handful of tokens. These stages retain every
    # requested symbol and all evidence locations/hashes.
    def record_omission(message: str) -> None:
        omitted.append(message)
        payload["omitted_context_summary"] = list(dict.fromkeys(omitted))

    def trim_validation_metadata() -> None:
        if payload.get("validation_commands"):
            payload["validation_commands"] = []
            record_omission("Generic validation command hints were omitted.")
        missing = payload.get("missing_context", [])
        if isinstance(missing, list) and missing:
            payload["missing_context"] = [str(item)[:240] for item in missing[:4]]

    def trim_repository_metadata() -> None:
        repositories = payload.get("workspace_repositories", [])
        if isinstance(repositories, list) and len(repositories) > 1:
            current_root = str(payload.get("current_repository", {}).get("root", ""))
            payload["workspace_repositories"] = [
                item for item in repositories
                if isinstance(item, Mapping) and str(item.get("root", "")) == current_root
            ][:1]
            record_omission("Unselected workspace repository metadata was omitted.")
        package = payload.get("package", {})
        if isinstance(package, dict):
            raw = package.get("raw_manifest")
            if isinstance(raw, dict) and raw.get("text"):
                raw["text"] = ""
                raw["truncated"] = True
                record_omission("Raw manifest text was omitted; validated fields remain.")

    def trim_relationship_prose() -> None:
        changed = False
        for record in payload.get("selected_grounding", []):
            if not isinstance(record, dict) or record.get("kind") != "source":
                continue
            for collection in ("callers", "tests", "state_transitions"):
                for relation in record.get(collection, []):
                    if isinstance(relation, dict) and relation.get("text"):
                        relation["text"] = ""
                        relation["truncated"] = True
                        changed = True
            for key in (
                "state_identifiers", "repository_scan_files",
                "repository_scan_truncated",
            ):
                record.pop(key, None)
        if changed:
            record_omission(
                "Relationship prose was omitted; caller/test/state-transition paths and "
                "line ranges remain."
            )

    def trim_auxiliary_records() -> None:
        removed_generic = 0
        removed_relationships = 0
        for record in payload.get("selected_grounding", []):
            if not isinstance(record, dict) or record.get("kind") != "source":
                continue
            for collection in ("references", "declarations"):
                values = record.get(collection, [])
                if isinstance(values, list):
                    removed_generic += len(values)
                    record[collection] = []
            relationship_omitted = record.setdefault("relationship_omitted", {})
            symbols = [str(item) for item in record.get("symbols", []) if str(item)]
            for collection, omitted_key in (
                ("callers", "callers"), ("tests", "tests"),
                ("state_transitions", "state-transitions"),
            ):
                values = record.get(collection, [])
                if not isinstance(values, list) or len(values) <= 6:
                    continue
                retained: list[object] = []
                for symbol in symbols:
                    matches = [
                        item for item in values
                        if isinstance(item, Mapping)
                        and str(item.get("symbol", "")).casefold() == symbol.casefold()
                    ]
                    retained.extend(matches[:2])
                retained.extend(
                    item for item in values
                    if isinstance(item, Mapping) and not item.get("symbol")
                )
                retained = retained[:max(6, len(symbols) * 2)]
                removed = len(values) - len(retained)
                if removed > 0:
                    record[collection] = retained
                    removed_relationships += removed
                    if isinstance(relationship_omitted, dict):
                        relationship_omitted[omitted_key] = int(
                            relationship_omitted.get(omitted_key, 0)
                        ) + removed
        if removed_generic:
            record_omission(
                f"{removed_generic} generic declaration/reference metadata records were "
                "omitted; exact definitions and counter dependencies remain."
            )
        if removed_relationships:
            record_omission(
                f"{removed_relationships} lower-ranked relationship records were omitted; "
                "balanced caller/test/state ranges remain for requested symbols."
            )

    def trim_operation_names() -> None:
        operations = payload.get("relevant_operations", [])
        if isinstance(operations, list) and operations:
            payload["relevant_operations"] = []
            record_omission(
                "Unneeded SDK operation suggestions were omitted from this code audit."
            )

    payload["omitted_context_summary"] = list(dict.fromkeys(omitted))
    rendered, estimated = build()
    for stage in (
        trim_validation_metadata, trim_repository_metadata,
        trim_relationship_prose, trim_auxiliary_records, trim_operation_names,
    ):
        if estimated <= planning_budget:
            break
        stage()
        truncated = True
        rendered, estimated = build()
    if estimated > planning_budget:
        summaries = payload.get("omitted_context_summary", [])
        if isinstance(summaries, list) and summaries:
            omitted_count = max(0, len(summaries) - 3)
            payload["omitted_context_summary"] = [
                str(item)[:220] for item in summaries[:3]
            ]
            if omitted_count:
                payload["omitted_context_summary"].append(
                    f"{omitted_count} additional deterministic omissions are recorded in "
                    "the host receipt."
                )
            truncated = True
            rendered, estimated = build()
    if estimated > input_budget:
        raise AssistantContextOverflow({
            "error": "assistant_context_overflow",
            "message": (
                "Grounded input still exceeds the configured model context after safe pruning. "
                "Select fewer source files/symbols, reduce the question, lower --max-tokens, "
                "or increase the assistant context setting."
            ),
            "estimated_input_tokens": estimated,
            "input_budget_tokens": input_budget,
            "context_tokens": context_tokens, "max_tokens": max_tokens,
            "omitted_context_summary": payload["omitted_context_summary"],
        })
    payload["context_budget"] = {
        "estimated_input_tokens": 0, "input_budget_tokens": input_budget,
        "configured_context_tokens": context_tokens, "requested_output_tokens": max_tokens,
        "reserved_output_tokens": max_tokens,
        "tokenization_safety_tokens": reserved_tokens,
        # Backward-compatible name retained for existing receipt consumers.
        "reserved_tokens": reserved_tokens,
        "reserve_policy": "max(512, 3% of configured context)",
        "truncated": truncated,
    }
    # Include the budget record in its own estimate. The digit width converges
    # immediately in normal cases, but a bounded loop avoids publishing the
    # smaller pre-metadata estimate that was used during pruning.
    for _attempt in range(3):
        rendered = _render_grounded_system(payload, system_prompt)
        estimated = estimate_tokens(rendered) + estimate_tokens(question)
        if payload["context_budget"]["estimated_input_tokens"] == estimated:
            break
        payload["context_budget"]["estimated_input_tokens"] = estimated
    rendered = _render_grounded_system(payload, system_prompt)
    estimated = estimate_tokens(rendered) + estimate_tokens(question)
    payload["context_budget"]["estimated_input_tokens"] = estimated
    if estimated > input_budget:
        # The metadata estimate can cross a digit boundary after it is inserted.
        # Compact diagnostic wording rather than refusing an otherwise fitting
        # audit by a few tokens.
        payload["omitted_context_summary"] = [
            str(item)[:120] for item in payload.get("omitted_context_summary", [])[:2]
        ]
        payload["context_budget"]["metadata_compacted"] = True
        rendered = _render_grounded_system(payload, system_prompt)
        estimated = estimate_tokens(rendered) + estimate_tokens(question)
        payload["context_budget"]["estimated_input_tokens"] = estimated
    if estimated > input_budget:
        raise AssistantContextOverflow({
            "error": "assistant_context_overflow",
            "message": (
                "Grounded input exceeds the configured model context after all safe "
                "low-priority context was trimmed. Select fewer symbols or increase the "
                "assistant context setting."
            ),
            "estimated_input_tokens": estimated,
            "input_budget_tokens": input_budget,
            "omitted_context_summary": payload["omitted_context_summary"],
        })
    return GroundingPlan(
        rendered, payload, estimated, input_budget, max_tokens,
        tuple(dict.fromkeys(omitted)), truncated,
    )


def _grounding_receipt_sources(context: Mapping[str, object] | None) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if not context:
        return records
    for raw in context.get("selected_grounding", []):
        if not isinstance(raw, Mapping):
            continue
        item = {
            key: raw.get(key) for key in (
                "kind", "path", "sha256", "symbols", "missing_symbols", "patterns",
                "line_count", "omitted_windows", "omitted_lines", "cache_hit",
                "explicit_symbols_preserved", "dependency_identifiers",
                "dependencies_omitted", "declaration_identifiers",
                "declarations_omitted", "aggregation_scope", "session_aggregates",
                "access_scope", "review_priorities", "relationship_omitted",
                "repository_scan_files", "repository_scan_truncated",
                "repository_untracked_sources_added",
                "repository_untracked_sources_truncated",
                "worktree_status", "worktree_dirty", "worktree_untracked",
                "worktree_status_available",
            ) if key in raw
        }
        if raw.get("kind") == "source":
            item["ranges"] = [
                {"line_start": excerpt.get("line_start"), "line_end": excerpt.get("line_end")}
                for excerpt in raw.get("excerpts", []) if isinstance(excerpt, Mapping)
            ]
            item["relationships"] = {
                role: [
                    {
                        key: relation.get(key) for key in (
                            "role", "path", "relative_path", "symbol", "identifier",
                            "line_start", "line_end", "truncated",
                        ) if key in relation
                    }
                    for relation in raw.get(role, []) if isinstance(relation, Mapping)
                ]
                for role in ("callers", "tests", "state_transitions")
                if raw.get(role)
            }
        records.append(item)
    return records


def _prune_receipts(directory: Path) -> None:
    files = sorted(
        (item for item in directory.glob("assistant-*.json") if item.is_file()),
        key=lambda item: item.stat().st_mtime, reverse=True,
    )
    retained = 0
    total = 0
    for item in files:
        try:
            size = item.stat().st_size
        except OSError:
            continue
        retained += 1
        total += size
        if retained > MAX_ASSISTANT_RECEIPTS or total > MAX_ASSISTANT_RECEIPT_BYTES:
            item.unlink(missing_ok=True)


def _receipt_response(
    advisory: Mapping[str, object] | None, context: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if advisory is None:
        return None
    gta = ""
    if context:
        installation = context.get("gta_installation", {})
        if isinstance(installation, Mapping):
            gta = str(installation.get("path", ""))

    def clean(value: object) -> object:
        if isinstance(value, str):
            return value.replace(gta, "<verified-gta-path>") if gta else value
        if isinstance(value, Mapping):
            return {str(key): clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    cleaned = clean(advisory)
    return cleaned if isinstance(cleaned, Mapping) else None


def write_assistant_receipt(
    root: Path, *, prompt_hash: str, model: str, mode: str,
    context: Mapping[str, object] | None, omitted_context: Iterable[str],
    estimated_input_tokens: int = 0, actual_input_tokens: int | None = None,
    actual_output_tokens: int | None = None, startup_seconds: float = 0.0,
    grounding_seconds: float = 0.0, primary_inference_seconds: float = 0.0,
    repair_seconds: float = 0.0, inference_seconds: float = 0.0,
    configured_context_tokens: int = 0, reserved_output_tokens: int = 0,
    repair_attempts: Iterable[Mapping[str, object]] = (), truncated: bool = False,
    advisory: Mapping[str, object] | None = None, failure_reason: str = "",
    safety_flags: Iterable[str] = (),
    sdk_build_id: str = "", model_sha256: str = "",
    llama_cpp_revision: str = "", provider_capabilities: Iterable[str] = (),
    thinking: str = "provider_default",
) -> Path:
    """Persist one bounded diagnostic receipt without prompt text, keys, or GTA data."""
    directory = root / "receipts"
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    receipt_id = now.strftime("%Y%m%dT%H%M%S.%fZ")
    destination = directory / f"assistant-{receipt_id}-{prompt_hash[:12]}.json"
    payload = {
        "schema": 3, "timestamp": now.isoformat(), "prompt_sha256": prompt_hash,
        "assistant_schema_version": ASSISTANT_RESPONSE_SCHEMA_VERSION,
        "sdk_build_id": sdk_build_id,
        "model": model, "mode": mode,
        "model_sha256": model_sha256,
        "llama_cpp_revision": llama_cpp_revision,
        "provider_capabilities": list(provider_capabilities),
        "thinking": thinking,
        "selected_grounding_sources": _grounding_receipt_sources(context),
        "omitted_context_summary": list(dict.fromkeys(omitted_context)),
        "estimated_input_tokens": estimated_input_tokens,
        "configured_context_tokens": configured_context_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "actual_input_tokens": actual_input_tokens,
        "actual_output_tokens": actual_output_tokens,
        "grounding_seconds": round(grounding_seconds, 3),
        "runtime_startup_seconds": round(startup_seconds, 3),
        "primary_inference_seconds": round(primary_inference_seconds, 3),
        "structured_repair_seconds": round(repair_seconds, 3),
        "inference_seconds": round(inference_seconds, 3),
        "total_latency_seconds": round(
            grounding_seconds + startup_seconds + inference_seconds, 3,
        ),
        "repair_attempts": [dict(item) for item in repair_attempts],
        "truncated": bool(truncated), "safety_flags": list(safety_flags),
        "structured_response": _receipt_response(advisory, context),
        "failure_reason": failure_reason,
    }
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    temporary.replace(destination)
    _prune_receipts(directory)
    return destination


def _json_object(text: str) -> dict[str, object]:
    candidate = text.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("Assistant did not return exactly one JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("Assistant structured response is not an object")
    return payload


def _schema_errors(
    value: object, schema: Mapping[str, object], path: str = "$",
) -> list[str]:
    """Validate the bounded response-schema subset without provider trust."""
    errors: list[str] = []
    expected = schema.get("type")
    kinds = expected if isinstance(expected, list) else [expected]

    def matches(kind: object) -> bool:
        if kind == "object":
            return isinstance(value, dict)
        if kind == "array":
            return isinstance(value, list)
        if kind == "string":
            return isinstance(value, str)
        if kind == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if kind == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if kind == "boolean":
            return isinstance(value, bool)
        if kind == "null":
            return value is None
        return False

    if expected is not None and not any(matches(kind) for kind in kinds):
        errors.append(f"{path} has the wrong type")
        return errors
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path} is not an allowed value")
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} is shorter than {minimum} characters")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} exceeds {maximum} characters")
    elif isinstance(value, list):
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} exceeds {maximum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, f"{path}[{index}]"))
                if len(errors) >= 12:
                    return errors[:12]
    elif isinstance(value, dict):
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in property_map:
                    errors.append(f"{path}.{key} is not allowed")
        for key, item in value.items():
            child_schema = property_map.get(key)
            if isinstance(child_schema, Mapping):
                errors.extend(_schema_errors(item, child_schema, f"{path}.{key}"))
                if len(errors) >= 12:
                    return errors[:12]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} is below {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} exceeds {maximum}")
    return errors[:12]


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Assistant {label} must be an array of strings")
    return [item.strip() for item in value if item.strip()]


def _unsafe_fragments(values: Iterable[str]) -> list[str]:
    violations: list[str] = []
    for value in values:
        for line in value.splitlines():
            if _NEGATED_SAFETY.search(line):
                continue
            if any(pattern.search(line) for pattern in _UNSAFE_GUIDANCE):
                violations.append(line.strip()[:240])
    return violations


def _ground_package_arguments(
    name: str, context: AssistantContextBundle,
) -> tuple[list[str] | None, str]:
    """Return host-owned arguments for package lifecycle operations.

    ``None`` means the operation is not one of the lifecycle commands handled
    here.  Model-supplied paths and package ids are never trusted for these
    commands; the values come only from the retrieved context bundle.
    """
    manifest = str(context.package.get("manifest", "")).strip()
    package_id = str(context.package.get("id", "")).strip()
    gta_path = str(context.gta_installation.get("path", "")).strip()
    gta_verified = bool(context.gta_installation.get("verified", False))
    if name in {"validate-package", "install-package"}:
        if not manifest:
            return [], "package manifest was not provided or found"
        return [manifest], ""
    if name in {
        "inspect-package-receipt", "verify-package-ownership", "uninstall-package",
    }:
        if not package_id:
            return [], "package id was not established by an authoritative manifest"
        values = [package_id]
        if gta_verified and gta_path:
            values.extend(("--gta-path", gta_path))
        return values, ""
    if name == "list-installed-packages":
        if gta_verified and gta_path:
            return ["--gta-path", gta_path], ""
        return [], ""
    selected = list(context.selected_grounding)
    if name == "inspect-source":
        sources = [item for item in selected if item.get("kind") == "source"]
        if not sources:
            return [], "no explicit source file was selected"
        record = sources[0]
        values = [str(record.get("path", ""))]
        for symbol in record.get("symbols", []):
            values.extend(("--symbol", str(symbol)))
        return values, ""
    if name == "inspect-log":
        logs = [item for item in selected if item.get("kind") == "telemetry"]
        if not logs:
            return [], "no explicit telemetry file was selected"
        record = logs[0]
        values = [str(record.get("path", ""))]
        for pattern in record.get("patterns", []):
            values.extend(("--pattern", str(pattern)))
        return values, ""
    if name == "compare-telemetry":
        logs = [item for item in selected if item.get("kind") == "telemetry"]
        if len(logs) < 2:
            return [], "two explicit telemetry files are required"
        return [str(logs[0].get("path", "")), str(logs[1].get("path", ""))], ""
    return None, ""


def validate_advisory(
    response_text: str, context: AssistantContextBundle, *,
    grounding_context: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Normalize model output and reject unsupported or unsafe recommendations."""
    planned = grounding_context or context.to_dict()
    preflight = _grounding_preflight(planned)
    compacted_sources: dict[str, Mapping[str, object]] = {}
    for record in planned.get("selected_grounding", []):
        if (
            isinstance(record, Mapping) and record.get("kind") == "source"
            and record.get("path") and record.get("explicit_symbols_preserved") is False
        ):
            try:
                key = os.path.normcase(str(Path(str(record["path"])).resolve()))
            except (OSError, ValueError):
                continue
            compacted_sources[key] = record
    try:
        payload = _json_object(response_text)
    except ValueError as exc:
        return ({
            "summary": "The assistant response was withheld because it was not structured.",
            "findings": [{
                "severity_domain": "engineering", "severity": "high",
                "evidence": str(exc), "file": "", "line": None,
                "confidence": 1.0, "status": "confirmed",
            }],
            "recommended_operations": [], "proposed_changes": [],
            "missing_context": list(context.missing_context),
            "abstentions": ["No recommendation was accepted from unstructured output."],
        }, ("unstructured_response",))

    shape_errors = _schema_errors(payload, ASSISTANT_RESPONSE_SCHEMA)
    if shape_errors:
        detail = "Invalid structured fields: " + "; ".join(shape_errors)
        return ({
            "summary": (
                "The assistant response was withheld because its JSON did not match "
                "the advisory schema."
            ),
            "findings": [{
                "severity_domain": "engineering", "severity": "high",
                "evidence": detail, "file": "", "line": None,
                "confidence": 1.0, "status": "confirmed",
            }],
            "recommended_operations": [], "proposed_changes": [],
            "missing_context": list(context.missing_context),
            "abstentions": ["No recommendation was accepted from malformed structured output."],
        }, ("unstructured_response", "invalid_response_schema"))

    summary = str(payload.get("summary", "")).strip()
    if not summary:
        summary = "The assistant did not provide a summary."
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        raw_findings = []
    findings: list[dict[str, object]] = []
    flags: list[str] = []
    confirmation_limited = False
    text_for_screening = [summary]
    for item in raw_findings[:8]:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "medium")).casefold()
        severity = {"warning": "medium", "error": "high"}.get(severity, severity)
        severity_domain = str(item.get("severity_domain", "engineering")).casefold()
        if severity_domain not in _SEVERITY_DOMAINS:
            severity_domain = "engineering"
        if severity_domain == "engineering" and severity == "critical":
            severity = "high"
            flags.append("engineering_critical_downgraded")
        state = str(item.get("status", "speculative")).casefold()
        evidence = str(item.get("evidence", "")).strip()
        file = str(item.get("file", "")).strip()
        line = item.get("line")
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        normalized_state = state if state in _EVIDENCE_STATES else "speculative"
        source_key = ""
        if file:
            try:
                source_key = os.path.normcase(str(Path(file).resolve()))
            except (OSError, ValueError):
                source_key = ""
        if normalized_state == "confirmed" and (
            source_key in compacted_sources
            or (
                not file
                and preflight["explicit_symbol_count"]
                and not preflight["explicit_symbols_preserved"]
            )
        ):
            normalized_state = "inferred"
            confidence = min(confidence, 0.75)
            confirmation_limited = True
            flags.append("confirmed_finding_downgraded_for_compacted_symbol")
        finding = {
            "severity_domain": severity_domain,
            "severity": severity if severity in _SEVERITIES else "medium",
            "evidence": evidence or "No evidence supplied.",
            "file": file,
            "line": line if isinstance(line, int) and line > 0 else None,
            "confidence": confidence,
            "status": normalized_state,
        }
        findings.append(finding)
        text_for_screening.extend((finding["evidence"], finding["file"]))

    allowed = {
        str(item.get("name")): item for item in context.relevant_operations
    }
    raw_operations = payload.get("recommended_operations", [])
    if not isinstance(raw_operations, list):
        raw_operations = []
    operations: list[dict[str, object]] = []
    abstentions = _string_list(payload.get("abstentions", []), "abstentions")
    for item in raw_operations[:6]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("operation", "")).strip().casefold()
        if name in context.completed_operations:
            flags.append("redundant_operation")
            abstentions.append(
                f"'{name}' was not recommended because that evidence is already grounded."
            )
            continue
        contract = allowed.get(name)
        if contract is None:
            flags.append("unsupported_operation")
            abstentions.append(
                f"Unsupported or unretrieved operation '{name or '<empty>'}' was rejected."
            )
            continue
        arguments = item.get("arguments", [])
        if (
            not isinstance(arguments, list) or len(arguments) > 128
            or any(not isinstance(value, str) or "\0" in value for value in arguments)
        ):
            flags.append("invalid_operation_arguments")
            abstentions.append(f"Invalid arguments for '{name}' were rejected.")
            continue
        risk = str(contract.get("risk", "read_only"))
        expected = str(item.get("expected_result", "")).strip()
        rationale = str(item.get("rationale", "")).strip()
        if not rationale:
            flags.append("missing_operation_rationale")
            abstentions.append(f"'{name}' was rejected because no fit rationale was supplied.")
            continue
        grounded_arguments, blocked_reason = _ground_package_arguments(name, context)
        if grounded_arguments is not None:
            arguments = grounded_arguments
            if blocked_reason:
                flags.append("ungrounded_operation_arguments")
                abstention = f"'{name}' is blocked: {blocked_reason}."
                if abstention not in abstentions:
                    abstentions.append(abstention)
        operations.append({
            "operation": name, "arguments": list(arguments), "risk": risk,
            "mutating": risk != "read_only",
            "acknowledgement_required": risk == "game_write",
            "rationale": rationale, "expected_result": expected,
            "arguments_grounded": not bool(blocked_reason),
            "blocked_reason": blocked_reason,
            "executed": False,
        })
        text_for_screening.append(expected)

    raw_changes = payload.get("proposed_changes", [])
    if not isinstance(raw_changes, list):
        raw_changes = []
    selected_sources = {
        os.path.normcase(str(Path(str(item.get("path", ""))).resolve())): item
        for item in context.selected_grounding
        if item.get("kind") == "source" and item.get("path")
    }
    proposed_changes: list[dict[str, object]] = []
    for item in raw_changes[:6]:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file", "")).strip()
        symbol = str(item.get("symbol", "")).strip()
        summary_value = str(item.get("summary", "")).strip()
        rationale = str(item.get("rationale", "")).strip()
        try:
            source_key = os.path.normcase(str(Path(file).resolve())) if file else ""
        except (OSError, ValueError):
            source_key = ""
        source = selected_sources.get(source_key)
        if source is None:
            flags.append("ungrounded_proposed_change")
            abstentions.append(
                "A proposed code change was withheld because its file was not selected grounding."
            )
            continue
        grounded_symbols = {
            str(value).casefold(): str(value) for value in source.get("symbols", [])
        }
        if symbol and symbol.casefold() not in grounded_symbols:
            flags.append("ungrounded_proposed_change_symbol")
            abstentions.append(
                f"A proposed change for ungrounded symbol '{symbol}' was withheld."
            )
            continue
        if symbol:
            symbol = grounded_symbols[symbol.casefold()]
        file = str(source.get("path", file))
        if not summary_value or not rationale:
            flags.append("incomplete_proposed_change")
            abstentions.append("An incomplete proposed code change was withheld.")
            continue
        engineering_severity = str(
            item.get("engineering_severity", "medium")
        ).casefold()
        if engineering_severity not in {"info", "low", "medium", "high", "blocker"}:
            engineering_severity = "medium"
        proposal = {
            "file": file, "symbol": symbol, "summary": summary_value,
            "rationale": rationale, "engineering_severity": engineering_severity,
            "advisory_only": True, "execution_authorized": False,
            "executed": False,
        }
        proposed_changes.append(proposal)
        text_for_screening.extend((summary_value, rationale))

    missing = _string_list(payload.get("missing_context", []), "missing_context")
    for item in context.missing_context:
        if item not in missing:
            missing.append(item)
    if not preflight["explicit_symbols_preserved"]:
        limitation = (
            "Requested symbol definitions were compacted before inference; omitted source "
            "lines are missing context and cannot support confirmed findings."
        )
        if limitation not in missing:
            missing.append(limitation)
    if confirmation_limited:
        abstention = (
            "A confirmed finding was downgraded because its requested source definition "
            "contained omitted lines."
        )
        if abstention not in abstentions:
            abstentions.append(abstention)
    text_for_screening.extend((*missing, *abstentions))
    unsafe = _unsafe_fragments(str(value) for value in text_for_screening)
    if unsafe:
        flags.append("unsafe_guidance_withheld")
        return ({
            "summary": "Unsafe or bypassing guidance was withheld by ALLIN1 policy.",
            "findings": [{
                "severity_domain": "security",
                "severity": "critical",
                "evidence": "The response proposed a manual, destructive, or unmanaged action.",
                "file": "", "line": None, "confidence": 1.0,
                "status": "confirmed",
            }],
            "recommended_operations": [], "proposed_changes": [],
            "missing_context": missing,
            "abstentions": [
                "The model output was rejected; use a retrieved typed SDK operation only."
            ],
        }, tuple(dict.fromkeys(flags)))
    return ({
        "summary": summary, "findings": findings,
        "recommended_operations": operations, "proposed_changes": proposed_changes,
        "missing_context": missing,
        "abstentions": abstentions,
    }, tuple(dict.fromkeys(flags)))


def format_advisory(advisory: Mapping[str, object]) -> str:
    lines = ["Summary", str(advisory.get("summary", "")), "", "Findings"]
    findings = advisory.get("findings", [])
    if isinstance(findings, list) and findings:
        for item in findings:
            if not isinstance(item, dict):
                continue
            location = str(item.get("file", ""))
            if item.get("line"):
                location += f":{item['line']}"
            suffix = f" | {location}" if location else ""
            domain = str(item.get("severity_domain", "engineering")).upper()
            lines.append(
                f"- [{domain} {str(item.get('severity', 'info')).upper()}] "
                f"{item.get('evidence', '')} "
                f"({item.get('status', 'speculative')}, "
                f"{float(item.get('confidence', 0.0)):.0%}){suffix}"
            )
    else:
        lines.append("- None")
    lines.extend(("", "Recommended operations"))
    operations = advisory.get("recommended_operations", [])
    if isinstance(operations, list) and operations:
        for item in operations:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('operation')} | {item.get('risk')} | "
                    f"acknowledgement: {'yes' if item.get('acknowledgement_required') else 'no'}"
                    + (
                        f" | blocked: {item.get('blocked_reason')}"
                        if item.get("blocked_reason") else ""
                    )
                )
    else:
        lines.append("- None")
    lines.extend(("", "Proposed code changes"))
    changes = advisory.get("proposed_changes", [])
    if isinstance(changes, list) and changes:
        for item in changes:
            if not isinstance(item, dict):
                continue
            location = str(item.get("file", ""))
            if item.get("symbol"):
                location += f"::{item['symbol']}"
            lines.append(
                f"- [{str(item.get('engineering_severity', 'medium')).upper()}] "
                f"{item.get('summary', '')} | {location} | advisory only; not executed"
            )
            if item.get("rationale"):
                lines.append(f"  Rationale: {item['rationale']}")
    else:
        lines.append("- None")
    for heading, key in (("Missing context", "missing_context"), ("Abstentions", "abstentions")):
        lines.extend(("", heading))
        values = advisory.get(key, [])
        if isinstance(values, list) and values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- None")
    return "\n".join(lines)


def default_assistant_root(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    base = values.get("LOCALAPPDATA") or values.get("XDG_DATA_HOME")
    if base:
        return Path(base).expanduser().resolve() / "ALLIN1" / "Assistant"
    return Path.home().resolve() / ".allin1" / "Assistant"


def _configured_capabilities(payload: Mapping[str, object], mode: str) -> tuple[str, ...]:
    if "capabilities" not in payload:
        if mode in {"managed_local", "custom_local"}:
            return (
                CAPABILITY_JSON_SCHEMA,
                CAPABILITY_THINKING_TEMPLATE,
                CAPABILITY_PROMPT_CACHE,
            )
        if mode == "compatible_api":
            # "Compatible" APIs vary materially. Never infer schema support or
            # non-standard Qwen thinking controls from an OpenAI-shaped URL.
            return ()
        return ()
    raw = payload.get("capabilities")
    if (
        not isinstance(raw, list)
        or not all(isinstance(item, str) and item.strip() for item in raw)
    ):
        raise ValueError("Assistant provider capabilities must be an array of names")
    normalized = tuple(dict.fromkeys(item.strip() for item in raw))
    unknown = sorted(set(normalized) - PROVIDER_CAPABILITIES)
    if unknown:
        raise ValueError(
            "Unsupported assistant provider capability: " + ", ".join(unknown)
        )
    thinking_controls = set(normalized) & _THINKING_CAPABILITIES
    if len(thinking_controls) > 1:
        raise ValueError("Assistant provider declares ambiguous thinking controls")
    return normalized


def _configured_thinking(
    payload: Mapping[str, object], mode: str, capabilities: tuple[str, ...],
) -> str:
    # An explicit capability list is authoritative even for a local mode. Disable
    # thinking by default only when a supported control is actually available.
    default = (
        "disabled" if set(capabilities) & _THINKING_CAPABILITIES
        else "provider_default"
    )
    thinking = str(payload.get("thinking", default)).strip().casefold()
    if thinking not in {"disabled", "enabled", "provider_default"}:
        raise ValueError(f"Unsupported assistant thinking setting: {thinking}")
    if thinking != "provider_default" and not set(capabilities) & _THINKING_CAPABILITIES:
        raise ValueError(
            f"Assistant provider cannot set thinking={thinking}; no supported control was declared"
        )
    return thinking


def _optional_sha256(value: object, label: str) -> str:
    digest = str(value or "").strip().casefold()
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"Assistant {label} must be a SHA-256 digest")
    return digest


def load_assistant_settings(root: Path | None = None) -> AssistantSettings:
    target = (root or default_assistant_root()).expanduser().resolve()
    path = target / ASSISTANT_CONFIG
    if not path.is_file():
        raise ValueError(
            "The optional assistant is not configured. Use the launcher's SDK Manager first."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Assistant configuration is invalid: {exc}") from exc
    if not isinstance(payload, dict) or int(payload.get("schema", 0)) != 1:
        raise ValueError("Assistant configuration uses an unsupported schema")
    mode = str(payload.get("mode", "disabled"))
    if mode not in ASSISTANT_MODES:
        raise ValueError(f"Unsupported assistant mode: {mode}")
    context = int(payload.get("context_tokens", 8192))
    temperature = float(payload.get("temperature", 0.1))
    if not 2048 <= context <= 32768:
        raise ValueError("Assistant context must be between 2,048 and 32,768 tokens")
    if not 0 <= temperature <= 1:
        raise ValueError("Assistant temperature must be between 0.0 and 1.0")
    capabilities = _configured_capabilities(payload, mode)
    thinking = _configured_thinking(payload, mode, capabilities)
    return AssistantSettings(
        root=target,
        mode=mode,
        workflow=str(payload.get("workflow", "installer")),
        profile=str(payload.get("profile", "recommended")),
        endpoint=str(payload.get("endpoint", "")),
        model_name=str(payload.get("model_name", "")),
        api_key_env=str(payload.get("api_key_env", "")),
        runtime_path=str(payload.get("runtime_path", "")),
        model_path=str(payload.get("model_path", "")),
        context_tokens=context,
        temperature=temperature,
        provider_capabilities=capabilities,
        thinking=thinking,
        model_sha256=_optional_sha256(payload.get("model_sha256"), "model_sha256"),
        llama_cpp_revision=str(payload.get("llama_cpp_revision", "")).strip(),
    )


def _validate_runtime(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Assistant runtime was not found: {resolved}")
    with resolved.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Assistant runtime is not a Windows executable")
    return resolved


def _validate_model(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Assistant model was not found: {resolved}")
    with resolved.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise ValueError("Assistant model is not a GGUF file")
    return resolved


_FILE_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_FILE_HASH_LOCK = threading.Lock()


def _file_sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _FILE_HASH_LOCK:
        cached = _FILE_HASH_CACHE.get(key)
    if cached:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    value = digest.hexdigest()
    with _FILE_HASH_LOCK:
        _FILE_HASH_CACHE.clear()
        _FILE_HASH_CACHE[key] = value
    return value


def _git_commit(root: Path) -> str:
    git = root / ".git"
    if git.is_file():
        try:
            marker = git.read_text(encoding="utf-8").strip()
            if marker.casefold().startswith("gitdir:"):
                git = (root / marker.split(":", 1)[1].strip()).resolve()
        except OSError:
            return ""
    try:
        head = (git / "HEAD").read_text(encoding="ascii").strip()
        if head.startswith("ref: "):
            reference = head[5:]
            ref_path = git / PurePosixPath(reference)
            if ref_path.is_file():
                head = ref_path.read_text(encoding="ascii").strip()
            else:
                for line in (git / "packed-refs").read_text(encoding="ascii").splitlines():
                    if line.endswith(" " + reference):
                        head = line.split(" ", 1)[0]
                        break
    except OSError:
        return ""
    return head.casefold() if re.fullmatch(r"[0-9a-fA-F]{7,64}", head) else ""


def _sdk_build_id() -> str:
    configured = os.environ.get("ALLIN1_SDK_BUILD_ID", "").strip()
    if configured:
        return configured[:128]
    root = project_root()
    release = root / "release.json"
    try:
        payload = json.loads(release.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, Mapping):
        for key in ("build_id", "commit"):
            value = str(payload.get(key, "")).strip()
            if value:
                return value[:128]
    commit = _git_commit(root)
    return commit or f"version:{__version__}"


def _component_child(component: Path, value: object, label: str) -> Path:
    raw = str(value)
    relative = PurePosixPath(raw)
    if (
        "\\" in raw or any(":" in part for part in relative.parts)
        or relative.is_absolute() or not relative.parts or ".." in relative.parts
    ):
        raise ValueError(f"Managed assistant {label} path is unsafe")
    resolved = component.joinpath(*relative.parts).resolve()
    if not resolved.is_relative_to(component.resolve()):
        raise ValueError(f"Managed assistant {label} path escapes its component")
    return resolved


def local_runtime_spec(settings: AssistantSettings) -> LocalRuntimeSpec:
    if settings.mode == "custom_local":
        model = _validate_model(Path(settings.model_path))
        model_sha256 = _file_sha256(model)
        if settings.model_sha256 and settings.model_sha256 != model_sha256:
            raise ValueError("Custom assistant model SHA-256 does not match its configuration")
        return LocalRuntimeSpec(
            _validate_runtime(Path(settings.runtime_path)), model,
            settings.model_name.strip() or model.stem, settings.context_tokens,
            model_sha256, settings.llama_cpp_revision,
        )
    if settings.mode != "managed_local":
        raise ValueError("The configured assistant mode does not use a local runtime")
    component = settings.root / ASSISTANT_COMPONENT
    manifest_path = component / ASSISTANT_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Managed assistant metadata is invalid: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("product") != "ALLIN1-Assistant":
        raise ValueError("Managed assistant metadata names the wrong product")
    runtime_data = manifest.get("runtime")
    model_data = manifest.get("model")
    if not isinstance(runtime_data, dict) or not isinstance(model_data, dict):
        raise ValueError("Managed assistant runtime/model metadata is invalid")
    runtime = _validate_runtime(_component_child(component, runtime_data.get("path"), "runtime"))
    model = _validate_model(_component_child(component, model_data.get("path"), "model"))
    model_sha256 = _file_sha256(model)
    declared_sha256 = _optional_sha256(model_data.get("sha256"), "managed model sha256")
    if declared_sha256 and declared_sha256 != model_sha256:
        raise ValueError("Managed assistant model SHA-256 does not match its metadata")
    return LocalRuntimeSpec(
        runtime, model, str(model_data.get("name") or model.stem), settings.context_tokens,
        model_sha256, str(
            runtime_data.get("revision") or runtime_data.get("version")
            or settings.llama_cpp_revision
        ).strip(),
    )


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_limited(response, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    output = bytearray()
    while True:
        chunk = response.read(min(64 * 1024, limit + 1 - len(output)))
        if not chunk:
            return bytes(output)
        output.extend(chunk)
        if len(output) > limit:
            raise ValueError("Assistant response exceeds the output limit")


class LocalAssistantServer:
    """Own one loopback-only llama.cpp server for the current SDK process."""

    def __init__(self, popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self._popen = popen_factory
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._identity: tuple[str, str, int] | None = None
        self._endpoint = ""
        self._api_key = ""
        self._api_key_path: Path | None = None
        self._idle_timer: threading.Timer | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def api_key(self) -> str:
        with self._lock:
            return self._api_key

    def ensure(
        self, spec: LocalRuntimeSpec, root: Path, *, startup_timeout: float,
        opener=urlopen,
    ) -> str:
        with self._lock:
            self._cancel_idle_locked()
            if self.running and self._identity == spec.identity:
                return self._endpoint
            self._stop_locked()
            port = _available_port()
            endpoint = f"http://127.0.0.1:{port}/v1"
            root.mkdir(parents=True, exist_ok=True)
            log_path = root / "runtime.log"
            api_key = secrets.token_urlsafe(32)
            api_key_path = root / f".runtime-api-key-{os.getpid()}-{secrets.token_hex(4)}.txt"
            api_key_path.write_text(api_key + "\n", encoding="utf-8")
            api_key_path.chmod(0o600)
            with log_path.open("ab", buffering=0) as log_stream:
                command = [
                    str(spec.runtime), "--model", str(spec.model),
                    "--host", "127.0.0.1", "--port", str(port),
                    "--ctx-size", str(spec.context_tokens), "--no-ui",
                    "--api-key-file", str(api_key_path),
                ]
                try:
                    process = self._popen(
                        command, cwd=spec.runtime.parent, stdin=subprocess.DEVNULL,
                        stdout=log_stream, stderr=subprocess.STDOUT,
                        **hidden_process_options(),
                    )
                except OSError as exc:
                    api_key_path.unlink(missing_ok=True)
                    raise ValueError(f"Could not start the local assistant runtime: {exc}") from exc
            self._process = process
            self._identity = spec.identity
            self._endpoint = endpoint
            self._api_key = api_key
            self._api_key_path = api_key_path
            deadline = time.monotonic() + startup_timeout
            health = Request(f"http://127.0.0.1:{port}/health", headers={
                "Accept": "application/json", "Authorization": f"Bearer {api_key}",
            })
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self._stop_locked()
                    raise ValueError(
                        f"Local assistant runtime exited during startup. Review {log_path}."
                    )
                try:
                    with opener(health, timeout=1.0) as response:
                        _read_limited(response, 64 * 1024)
                    return endpoint
                except (OSError, URLError, ValueError):
                    time.sleep(0.1)
            self._stop_locked()
            raise ValueError(
                f"Local assistant runtime did not become ready within "
                f"{startup_timeout:.0f} seconds. Review {log_path}."
            )

    def _cancel_idle_locked(self) -> None:
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()

    def _stop_locked(self) -> bool:
        self._cancel_idle_locked()
        process = self._process
        self._process = None
        self._identity = None
        self._endpoint = ""
        self._api_key = ""
        api_key_path = self._api_key_path
        self._api_key_path = None
        if api_key_path is not None:
            api_key_path.unlink(missing_ok=True)
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return True

    def stop(self) -> bool:
        with self._lock:
            return self._stop_locked()

    def schedule_stop(self, seconds: float = LOCAL_RUNTIME_KEEPALIVE_SECONDS) -> None:
        """Keep a healthy model warm briefly, then release it after idle time."""
        if seconds <= 0:
            self.stop()
            return
        with self._lock:
            self._cancel_idle_locked()
            if not self.running:
                return
            timer = threading.Timer(seconds, self.stop)
            timer.daemon = True
            self._idle_timer = timer
            timer.start()


_LOCAL_SERVER = LocalAssistantServer()
atexit.register(_LOCAL_SERVER.stop)


def _chat_url(endpoint: str) -> str:
    value = endpoint.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Assistant endpoint must be an HTTP(S) URL")
    if value.casefold().endswith("/chat/completions"):
        return value
    return value + "/chat/completions" if value.casefold().endswith("/v1") else value + "/v1/chat/completions"


def _response_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Assistant API returned a non-object response")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("Assistant API response does not contain a completion choice")
    choice = choices[0]
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else choice.get("text")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Assistant API returned an empty response")
    return content.strip()


def _finish_reason(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    return str(choices[0].get("finish_reason") or "").casefold()


def _usage(payload: object) -> tuple[int | None, int | None]:
    values = payload.get("usage", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(values, Mapping):
        return None, None
    prompt = values.get("prompt_tokens")
    completion = values.get("completion_tokens")
    return (
        int(prompt) if isinstance(prompt, (int, float)) else None,
        int(completion) if isinstance(completion, (int, float)) else None,
    )


def _repair_envelope(
    context: AssistantContextBundle, question: str, draft: str, *,
    grounding_context: Mapping[str, object] | None = None,
) -> str:
    """Give a repair pass only the authority needed to structure its own draft."""
    selected = []
    planned = grounding_context or context.to_dict()
    for record in planned.get("selected_grounding", []):
        if not isinstance(record, Mapping):
            continue
        selected.append({
            "kind": record.get("kind"), "path": record.get("path"),
            "symbols": record.get("symbols", []),
            "missing_symbols": record.get("missing_symbols", []),
            "patterns": record.get("patterns", []),
            "explicit_symbols_preserved": record.get(
                "explicit_symbols_preserved", True,
            ),
            "compacted_symbols": record.get("compacted_symbols", []),
        })
    constraints = {
        "operation_mode": context.operation_mode,
        "allowed_operations": [
            {"name": item.get("name"), "risk": item.get("risk")}
            for item in context.relevant_operations
        ],
        "completed_operations": list(context.completed_operations),
        "selected_grounding": selected,
        "grounding_preflight": _grounding_preflight(planned),
        "missing_context": list(context.missing_context),
        "execution_authorized": False,
    }
    # The second pass repairs/finishes the model's own answer; it does not need
    # another copy of large source or telemetry excerpts.
    bounded_draft = draft
    if len(bounded_draft) > 16_000:
        bounded_draft = (
            bounded_draft[:10_000]
            + "\n...[middle of draft omitted for repair budget]...\n"
            + bounded_draft[-5_900:]
        )
    return json.dumps({
        "task": (
            "Repair or complete the draft as exactly one JSON object matching the response "
            "schema. Preserve supported conclusions, remove prose/fences, use empty arrays "
            "where information is absent, and never invent evidence or operations."
        ),
        "question": question[:4_000],
        "host_constraints": constraints,
        "draft": bounded_draft,
    }, ensure_ascii=False, separators=(",", ":"))


def _recover_read_only_prose(
    draft: str, context: AssistantContextBundle,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Retain bounded observations after schema failure without accepting authority."""
    candidates: list[str] = []
    for field in ("summary", "evidence", "rationale"):
        expression = re.compile(
            rf'"{field}"\s*:\s*("(?:\\.|[^"\\])*")', re.IGNORECASE,
        )
        for match in expression.finditer(draft):
            try:
                value = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    if not candidates:
        for line in draft.replace("```json", "").replace("```", "").splitlines():
            cleaned = re.sub(r"^[\s*#>-]+", "", line).strip().strip(",")
            if (
                cleaned and not cleaned.startswith(("{", "[", "}"))
                and len(cleaned) >= 12
            ):
                candidates.append(cleaned)
    bounded: list[str] = []
    for value in candidates:
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()[:600]
        if value and value not in bounded:
            bounded.append(value)
        if len(bounded) >= 5:
            break
    if not bounded or _unsafe_fragments(bounded):
        return ({
            "summary": "Malformed assistant output was withheld after structured repair failed.",
            "findings": [], "recommended_operations": [], "proposed_changes": [],
            "missing_context": list(context.missing_context),
            "abstentions": [
                "No prose, operation, or mutation was accepted from the malformed output."
            ],
        }, ("prose_recovery_unavailable",))
    summary = bounded[0][:480]
    evidence = bounded[1:] or [summary]
    return ({
        "summary": (
            "Structured parsing failed; the following read-only observations were "
            f"preserved without operation authority: {summary}"
        )[:800],
        "findings": [{
            "severity_domain": "engineering", "severity": "info",
            "evidence": item, "file": "", "line": None,
            "confidence": 0.25, "status": "speculative",
        } for item in evidence[:4]],
        "recommended_operations": [], "proposed_changes": [],
        "missing_context": list(context.missing_context),
        "abstentions": [
            "Structured operation parsing failed; no operation or mutation was accepted."
        ],
    }, ("prose_findings_preserved_after_schema_failure",))


def _structured_response_format(
    schema: Mapping[str, object], *, name: str,
) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("Assistant response schema name is invalid")
    encoded = json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("Assistant response schema exceeds the 64 KiB limit")
    _validate_bounded_schema_definition(schema)
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": dict(schema)},
    }


def _validate_bounded_schema_definition(
    schema: Mapping[str, object], *, path: str = "$", depth: int = 0,
) -> None:
    if depth > 12:
        raise ValueError("Assistant response schema nesting exceeds 12 levels")
    expected = schema.get("type")
    kinds = expected if isinstance(expected, list) else [expected]
    allowed = {"object", "array", "string", "integer", "number", "boolean", "null"}
    if not kinds or any(not isinstance(kind, str) or kind not in allowed for kind in kinds):
        raise ValueError(f"Assistant response schema has an unsupported type at {path}")
    if "object" in kinds:
        if schema.get("additionalProperties") is not False:
            raise ValueError(
                f"Assistant response object must set additionalProperties=false at {path}"
            )
        properties = schema.get("properties")
        if not isinstance(properties, Mapping) or len(properties) > 64:
            raise ValueError(f"Assistant response object properties are invalid at {path}")
        for key, child in properties.items():
            if not isinstance(key, str) or not isinstance(child, Mapping):
                raise ValueError(f"Assistant response property is invalid at {path}")
            _validate_bounded_schema_definition(
                child, path=f"{path}.{key}", depth=depth + 1,
            )
    if "array" in kinds:
        maximum = schema.get("maxItems")
        items = schema.get("items")
        if not isinstance(maximum, int) or not 0 <= maximum <= 64:
            raise ValueError(f"Assistant response array is not bounded at {path}")
        if not isinstance(items, Mapping):
            raise ValueError(f"Assistant response array items are invalid at {path}")
        _validate_bounded_schema_definition(items, path=f"{path}[]", depth=depth + 1)
    if "string" in kinds:
        maximum = schema.get("maxLength")
        if not isinstance(maximum, int) or not 0 <= maximum <= 8192:
            raise ValueError(f"Assistant response string is not bounded at {path}")


def _provider_request_fields(
    settings: AssistantSettings, response_format: Mapping[str, object],
) -> dict[str, object]:
    capabilities = set(settings.provider_capabilities)
    if CAPABILITY_JSON_SCHEMA not in capabilities:
        raise ValueError(
            "Assistant provider does not declare schema-constrained structured output"
        )
    fields: dict[str, object] = {"response_format": dict(response_format)}
    if settings.thinking != "provider_default":
        enabled = settings.thinking == "enabled"
        if CAPABILITY_THINKING_REASONING in capabilities:
            fields["reasoning_effort"] = "medium" if enabled else "none"
        elif CAPABILITY_THINKING_TEMPLATE in capabilities:
            fields["chat_template_kwargs"] = {"enable_thinking": enabled}
        elif CAPABILITY_THINKING_QWEN in capabilities:
            fields["enable_thinking"] = enabled
        else:
            raise ValueError("Assistant provider thinking control is unavailable")
    if CAPABILITY_PROMPT_CACHE in capabilities:
        fields["cache_prompt"] = True
    return fields


def prompt_structured_assistant(
    prompt: str, *, response_schema: Mapping[str, object], schema_name: str,
    root: Path | None = None, system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    timeout: float = 180.0, startup_timeout: float = 90.0,
    max_tokens: int = 640, opener=urlopen,
    server: LocalAssistantServer | None = None,
    progress: Callable[[str], None] | None = None,
) -> StructuredPromptResult:
    """Generate one bounded, validated JSON object with no tool/write authority."""
    question = prompt.strip()
    if not question:
        raise ValueError("Assistant prompt cannot be empty")
    if len(question.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Assistant prompt exceeds the 64 KiB limit")
    if not 1 <= max_tokens <= 4096:
        raise ValueError("Structured assistant max tokens must be between 1 and 4,096")
    if not 1 <= timeout <= 600 or not 1 <= startup_timeout <= 300:
        raise ValueError("Assistant timeouts are outside the supported range")
    response_format = _structured_response_format(response_schema, name=schema_name)
    if response_schema.get("type") != "object":
        raise ValueError("Structured assistant response schema root must be an object")
    settings = load_assistant_settings(root)
    if not settings.enabled:
        raise ValueError("The optional assistant is disabled in the launcher SDK Manager")
    request_fields = _provider_request_fields(settings, response_format)
    guidance = system_prompt.strip()
    if guidance and guidance != DEFAULT_SYSTEM_PROMPT:
        guidance = DEFAULT_SYSTEM_PROMPT + "\n\nAdditional read-only guidance:\n" + guidance
    elif not guidance:
        guidance = DEFAULT_SYSTEM_PROMPT
    schema_text = json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
    structured_system = (
        guidance
        + "\n\nReturn exactly one JSON object matching this JSON Schema. The schema is a "
        "generation and validation contract, not authority to execute or write anything:\n"
        + schema_text
    )
    if len(structured_system.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Structured assistant system prompt exceeds the 64 KiB limit")
    estimated_input = estimate_tokens(structured_system) + estimate_tokens(question)
    reserved_tokens = _context_safety_tokens(settings.context_tokens)
    if estimated_input + max_tokens + reserved_tokens > settings.context_tokens:
        raise AssistantContextOverflow({
            "error": "assistant_context_overflow",
            "message": "Structured assistant prompt does not fit the configured context.",
            "estimated_input_tokens": estimated_input,
            "configured_context_tokens": settings.context_tokens,
            "requested_output_tokens": max_tokens,
            "reserved_tokens": reserved_tokens,
        })

    notify = progress or (lambda _state: None)
    active_server = server or _LOCAL_SERVER
    startup_started = time.monotonic()
    model_sha256 = settings.model_sha256
    llama_cpp_revision = settings.llama_cpp_revision
    if settings.mode in {"managed_local", "custom_local"}:
        spec = local_runtime_spec(settings)
        notify("starting runtime")
        with _ProgressHeartbeat(notify, "starting runtime"):
            endpoint = active_server.ensure(
                spec, settings.root, startup_timeout=startup_timeout, opener=opener,
            )
        model_name = spec.model_name
        model_sha256 = spec.model_sha256
        llama_cpp_revision = spec.llama_cpp_revision
        local_api_key = active_server.api_key
    else:
        endpoint = settings.endpoint
        model_name = settings.model_name.strip()
        if not model_name:
            raise ValueError("Compatible API mode requires a model name")
        local_api_key = ""
    startup_seconds = time.monotonic() - startup_started
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if local_api_key:
        headers["Authorization"] = f"Bearer {local_api_key}"
    if settings.mode == "compatible_api" and settings.api_key_env:
        secret = os.environ.get(settings.api_key_env, "")
        if not secret:
            raise ValueError(
                f"Assistant API key environment variable is not set: {settings.api_key_env}"
            )
        headers["Authorization"] = f"Bearer {secret}"

    def generate(messages: list[dict[str, str]], tokens: int, temperature: float) -> object:
        body: dict[str, object] = {
            "model": model_name, "messages": messages, "temperature": temperature,
            "max_tokens": tokens, "stream": False, **request_fields,
        }
        request = Request(
            _chat_url(endpoint), method="POST", headers=headers,
            data=json.dumps(body).encode("utf-8"),
        )
        try:
            with opener(request, timeout=timeout) as response:
                return json.loads(_read_limited(response).decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = _read_limited(exc, 16 * 1024).decode(
                    "utf-8", errors="replace",
                ).strip()
            except ValueError:
                detail = "response body exceeded the error limit"
            raise ValueError(f"Assistant API returned HTTP {exc.code}: {detail}") from exc
        except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Assistant request failed: {exc}") from exc

    started = time.monotonic()
    repaired = False
    truncated = False
    actual_input: int | None = None
    actual_output: int | None = None
    try:
        notify("generating")
        with _ProgressHeartbeat(notify, "generating"):
            api_payload = generate([
                {"role": "system", "content": structured_system},
                {"role": "user", "content": question},
            ], max_tokens, settings.temperature)
        primary_text = _response_text(api_payload)
        finish_reason = _finish_reason(api_payload)
        truncated = finish_reason == "length"
        actual_input, actual_output = _usage(api_payload)
        try:
            structured_payload = _json_object(primary_text)
            errors = _schema_errors(structured_payload, response_schema)
        except ValueError as exc:
            structured_payload = {}
            errors = [str(exc)]
        if truncated or errors:
            repair_user = json.dumps({
                "task": (
                    "Repair or complete the draft as exactly one JSON object matching "
                    "the supplied schema. Do not add facts or perform any action."
                ),
                "original_request": question[:4000],
                "draft": primary_text[:16000],
                "validation_errors": errors[:12],
            }, ensure_ascii=False, separators=(",", ":"))
            available = (
                settings.context_tokens - estimate_tokens(structured_system)
                - estimate_tokens(repair_user) - reserved_tokens
            )
            repair_tokens = min(max_tokens, available)
            if repair_tokens < 256:
                raise ValueError("Structured assistant repair does not fit the context")
            notify("repairing structured response")
            with _ProgressHeartbeat(notify, "repairing structured response"):
                repair_api_payload = generate([
                    {"role": "system", "content": structured_system},
                    {"role": "user", "content": repair_user},
                ], repair_tokens, 0)
            repair_text = _response_text(repair_api_payload)
            try:
                structured_payload = _json_object(repair_text)
            except ValueError as exc:
                raise ValueError("Structured assistant repair was malformed") from exc
            repair_errors = _schema_errors(structured_payload, response_schema)
            repair_input, repair_output = _usage(repair_api_payload)
            if repair_output is not None and repair_output > repair_tokens:
                raise ValueError(
                    "Structured assistant repair exceeded the requested output budget"
                )
            if _finish_reason(repair_api_payload) == "length" or repair_errors:
                raise ValueError(
                    "Structured assistant response was withheld after one failed repair: "
                    + "; ".join(repair_errors[:4] or ["repair output was truncated"])
                )
            repaired = True
            if repair_input is not None:
                actual_input = (actual_input or 0) + repair_input
            if repair_output is not None:
                actual_output = (actual_output or 0) + repair_output
        notify("complete")
        return StructuredPromptResult(
            payload=structured_payload, model=model_name, mode=settings.mode,
            elapsed_seconds=round(startup_seconds + time.monotonic() - started, 3),
            repaired=repaired, truncated=truncated,
            actual_input_tokens=actual_input, actual_output_tokens=actual_output,
            sdk_build_id=_sdk_build_id(), model_sha256=model_sha256,
            llama_cpp_revision=llama_cpp_revision,
            provider_capabilities=settings.provider_capabilities,
            thinking=settings.thinking,
        )
    finally:
        if settings.mode in {"managed_local", "custom_local"}:
            active_server.schedule_stop(LOCAL_RUNTIME_KEEPALIVE_SECONDS)


def prompt_assistant(
    prompt: str, *, root: Path | None = None,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    timeout: float = 180.0, startup_timeout: float = 90.0,
    max_tokens: int = 640, opener=urlopen,
    server: LocalAssistantServer | None = None,
    repository_root: Path | None = None,
    workspace_roots: Iterable[Path] = (), manifest: Path | None = None,
    gta_path: Path | None = None, operation_mode: str = "advisory",
    sources: Iterable[Path] = (), symbols: Iterable[str] = (),
    telemetry_files: Iterable[Path] = (), telemetry_patterns: Iterable[str] = (),
    source_priorities: Iterable[str] = (),
    preserve_findings_on_schema_failure: bool = False,
    compact_response: bool = False,
    require_explicit_symbols_preserved: bool = False,
    progress: Callable[[str], None] | None = None,
    context_builder: Callable[..., AssistantContextBundle] = build_assistant_context,
) -> PromptResult:
    """Send one read-only prompt using the configured assistant provider."""
    question = prompt.strip()
    if not question:
        raise ValueError("Assistant prompt cannot be empty")
    if len(question.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Assistant prompt exceeds the 64 KiB limit")
    if len(system_prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Assistant system prompt guidance exceeds the 64 KiB limit")
    if not 1 <= max_tokens <= 8192:
        raise ValueError("Assistant max tokens must be between 1 and 8,192")
    if not 1 <= timeout <= 600 or not 1 <= startup_timeout <= 300:
        raise ValueError("Assistant timeouts are outside the supported range")
    settings = load_assistant_settings(root)
    if not settings.enabled:
        raise ValueError("The optional assistant is disabled in the launcher SDK Manager")
    sdk_build_id = _sdk_build_id()
    model_sha256 = settings.model_sha256
    llama_cpp_revision = settings.llama_cpp_revision
    notify = progress or (lambda _state: None)
    prompt_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    notify("building grounding")
    grounding_started = time.monotonic()
    context = context_builder(
        question, repository_root=repository_root,
        workspace_roots=workspace_roots, manifest=manifest, gta_path=gta_path,
        operation_mode=operation_mode, sources=sources, symbols=symbols,
        telemetry_files=telemetry_files, telemetry_patterns=telemetry_patterns,
        source_priorities=source_priorities,
    )
    try:
        plan = plan_grounding(
            context, question, system_prompt,
            context_tokens=settings.context_tokens, max_tokens=max_tokens,
        )
    except AssistantContextOverflow as exc:
        grounding_seconds = time.monotonic() - grounding_started
        try:
            receipt = write_assistant_receipt(
                settings.root, prompt_hash=prompt_hash,
                model=settings.model_name, mode=settings.mode,
                context=context.to_dict(), omitted_context=context.omitted_context_summary,
                failure_reason=str(exc), sdk_build_id=sdk_build_id,
                model_sha256=model_sha256, llama_cpp_revision=llama_cpp_revision,
                provider_capabilities=settings.provider_capabilities,
                thinking=settings.thinking,
                configured_context_tokens=settings.context_tokens,
                reserved_output_tokens=max_tokens,
                grounding_seconds=grounding_seconds,
            )
            exc.details.setdefault("receipt_path", str(receipt))
        except (OSError, TypeError, ValueError):
            exc.details.setdefault("receipt_path", "")
        exc.details.setdefault("inference_started", False)
        raise
    grounding_seconds = time.monotonic() - grounding_started
    preflight = _grounding_preflight(plan.context)
    if (
        require_explicit_symbols_preserved
        and preflight["explicit_symbol_count"]
        and not preflight["explicit_symbols_preserved"]
    ):
        details: dict[str, object] = {
            "error": "assistant_context_overflow",
            "reason": "explicit_symbol_compaction",
            "message": (
                "This review chunk would compact an explicitly requested definition; "
                "the review abstained before inference."
            ),
            "estimated_input_tokens": plan.estimated_input_tokens,
            "input_budget_tokens": plan.input_budget_tokens,
            "symbols": list(symbols),
            "explicit_symbols_preserved": False,
            "inference_started": False,
        }
        try:
            receipt = write_assistant_receipt(
                settings.root, prompt_hash=prompt_hash,
                model=settings.model_name, mode=settings.mode,
                context=plan.context, omitted_context=plan.omitted_context,
                estimated_input_tokens=plan.estimated_input_tokens,
                configured_context_tokens=settings.context_tokens,
                reserved_output_tokens=plan.reserved_output_tokens,
                grounding_seconds=grounding_seconds, truncated=plan.truncated,
                failure_reason=str(details["message"]),
                safety_flags=("review_abstained_explicit_symbol_compaction",),
                sdk_build_id=sdk_build_id, model_sha256=model_sha256,
                llama_cpp_revision=llama_cpp_revision,
                provider_capabilities=settings.provider_capabilities,
                thinking=settings.thinking,
            )
            details["receipt_path"] = str(receipt)
        except (OSError, TypeError, ValueError):
            details["receipt_path"] = ""
        raise AssistantContextOverflow(details)
    if (
        preflight["explicit_symbol_count"]
        and not preflight["explicit_symbols_preserved"]
    ):
        notify(
            "warning: explicit_symbols_preserved=false; omitted source lines "
            "cannot support confirmed findings"
        )
    grounded_system_prompt = plan.system_prompt
    if len(grounded_system_prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise AssistantContextOverflow({
            "error": "assistant_context_overflow",
            "message": "Grounded assistant context exceeds the 64 KiB transport limit.",
            "estimated_input_tokens": plan.estimated_input_tokens,
            "input_budget_tokens": plan.input_budget_tokens,
            "omitted_context_summary": list(plan.omitted_context),
        })
    active_server = server or _LOCAL_SERVER
    startup_seconds = 0.0
    if settings.mode in {"managed_local", "custom_local"}:
        spec = local_runtime_spec(settings)
        notify("starting runtime")
        startup_started = time.monotonic()
        with _ProgressHeartbeat(notify, "starting runtime"):
            endpoint = active_server.ensure(
                spec, settings.root, startup_timeout=startup_timeout, opener=opener,
            )
        startup_seconds = time.monotonic() - startup_started
        model_name = spec.model_name
        model_sha256 = spec.model_sha256
        llama_cpp_revision = spec.llama_cpp_revision
        local_api_key = active_server.api_key
    else:
        endpoint = settings.endpoint
        model_name = settings.model_name.strip()
        if not model_name:
            raise ValueError("Compatible API mode requires a model name")
        local_api_key = ""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if local_api_key:
        headers["Authorization"] = f"Bearer {local_api_key}"
    if settings.mode == "compatible_api" and settings.api_key_env:
        secret = os.environ.get(settings.api_key_env, "")
        if not secret:
            raise ValueError(
                f"Assistant API key environment variable is not set: {settings.api_key_env}"
            )
        headers["Authorization"] = f"Bearer {secret}"
    body: dict[str, object] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": grounded_system_prompt},
            {"role": "user", "content": question},
        ],
        "temperature": settings.temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    response_format = (
        ASSISTANT_REVIEW_RESPONSE_FORMAT if compact_response
        else ASSISTANT_RESPONSE_FORMAT
    )
    body.update(_provider_request_fields(settings, response_format))
    request = Request(
        _chat_url(endpoint), method="POST", headers=headers,
        data=json.dumps(body).encode("utf-8"),
    )
    notify("prefill")
    started = time.monotonic()
    notify("generating")
    try:
        with _ProgressHeartbeat(notify, "generating"):
            with opener(request, timeout=timeout) as response:
                payload = json.loads(_read_limited(response).decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = _read_limited(exc, 16 * 1024).decode("utf-8", errors="replace").strip()
        except ValueError:
            detail = "response body exceeded the error limit"
        error = ValueError(f"Assistant API returned HTTP {exc.code}: {detail}")
        try:
            write_assistant_receipt(
                settings.root, prompt_hash=prompt_hash, model=model_name, mode=settings.mode,
                context=plan.context, omitted_context=plan.omitted_context,
                estimated_input_tokens=plan.estimated_input_tokens,
                startup_seconds=startup_seconds,
                grounding_seconds=grounding_seconds,
                primary_inference_seconds=time.monotonic() - started,
                inference_seconds=time.monotonic() - started,
                configured_context_tokens=settings.context_tokens,
                reserved_output_tokens=plan.reserved_output_tokens,
                truncated=plan.truncated, failure_reason=str(error),
                sdk_build_id=sdk_build_id, model_sha256=model_sha256,
                llama_cpp_revision=llama_cpp_revision,
                provider_capabilities=settings.provider_capabilities,
                thinking=settings.thinking,
            )
        except OSError:
            pass
        raise error from exc
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = ValueError(f"Assistant request failed: {exc}")
        try:
            write_assistant_receipt(
                settings.root, prompt_hash=prompt_hash, model=model_name, mode=settings.mode,
                context=plan.context, omitted_context=plan.omitted_context,
                estimated_input_tokens=plan.estimated_input_tokens,
                startup_seconds=startup_seconds,
                grounding_seconds=grounding_seconds,
                primary_inference_seconds=time.monotonic() - started,
                inference_seconds=time.monotonic() - started,
                configured_context_tokens=settings.context_tokens,
                reserved_output_tokens=plan.reserved_output_tokens,
                truncated=plan.truncated, failure_reason=str(error),
                sdk_build_id=sdk_build_id, model_sha256=model_sha256,
                llama_cpp_revision=llama_cpp_revision,
                provider_capabilities=settings.provider_capabilities,
                thinking=settings.thinking,
            )
        except OSError:
            pass
        raise error from exc
    finally:
        if settings.mode in {"managed_local", "custom_local"}:
            active_server.schedule_stop(LOCAL_RUNTIME_KEEPALIVE_SECONDS)
    primary_inference_seconds = time.monotonic() - started
    primary_text = _response_text(payload)
    advisory, safety_flags = validate_advisory(
        primary_text, context, grounding_context=plan.context,
    )
    finish_reason = _finish_reason(payload)
    actual_input, actual_output = _usage(payload)
    repair_needed = finish_reason == "length" or "unstructured_response" in safety_flags
    repair_reasons = tuple(
        reason for condition, reason in (
            (finish_reason == "length", "initial_response_truncated"),
            ("unstructured_response" in safety_flags, "initial_response_unstructured"),
        ) if condition
    )
    repair_attempts: list[dict[str, object]] = []
    repair_seconds = 0.0
    if repair_needed:
        repair_system = (
            DEFAULT_SYSTEM_PROMPT
            + "\n\nThis is a structure-repair pass. Do not perform new research or "
            "execute anything.\n"
            + RESPONSE_SCHEMA_PROMPT
        )
        repair_user = _repair_envelope(
            context, question, primary_text, grounding_context=plan.context,
        )
        reserved_tokens = _context_safety_tokens(settings.context_tokens)
        available_output = max(
            0,
            settings.context_tokens
            - estimate_tokens(repair_system)
            - estimate_tokens(repair_user)
            - reserved_tokens,
        )
        repair_tokens = min(max_tokens, available_output)
        if repair_tokens >= 256:
            repair_attempt = {
                "attempt": 1, "reason": list(repair_reasons),
                "output_budget_tokens": repair_tokens,
                "schema": "compact_advisory_repair",
            }
            repair_started = time.monotonic()
            if settings.mode in {"managed_local", "custom_local"}:
                # The primary-request cleanup schedules an idle stop. Reuse the
                # healthy server here to cancel that timer before a potentially
                # long structure-repair generation begins.
                endpoint = active_server.ensure(
                    spec, settings.root, startup_timeout=startup_timeout, opener=opener,
                )
            repair_body: dict[str, object] = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": repair_system},
                    {"role": "user", "content": repair_user},
                ],
                "temperature": 0,
                "max_tokens": repair_tokens,
                "stream": False,
            }
            repair_body.update(
                _provider_request_fields(settings, ASSISTANT_REPAIR_RESPONSE_FORMAT)
            )
            repair_request = Request(
                _chat_url(endpoint), method="POST", headers=headers,
                data=json.dumps(repair_body).encode("utf-8"),
            )
            notify("repairing structured response")
            try:
                with _ProgressHeartbeat(notify, "repairing structured response"):
                    with opener(repair_request, timeout=timeout) as response:
                        repair_payload = json.loads(
                            _read_limited(response).decode("utf-8")
                        )
                repair_input, repair_output = _usage(repair_payload)
                if repair_output is not None and repair_output > repair_tokens:
                    raise ValueError(
                        "Structured assistant repair exceeded the requested output budget"
                    )
                candidate, candidate_flags = validate_advisory(
                    _response_text(repair_payload), context,
                    grounding_context=plan.context,
                )
                if "unstructured_response" not in candidate_flags:
                    advisory = candidate
                    safety_flags = tuple(dict.fromkeys((
                        *candidate_flags, *repair_reasons,
                        "structured_response_repaired",
                    )))
                    if repair_input is not None:
                        actual_input = (actual_input or 0) + repair_input
                    if repair_output is not None:
                        actual_output = (actual_output or 0) + repair_output
                    repair_attempt["result"] = "accepted"
                else:
                    safety_flags = tuple(dict.fromkeys((
                        *safety_flags, *candidate_flags, "structured_repair_failed",
                    )))
                    repair_attempt["result"] = "invalid_schema"
            except (
                HTTPError, OSError, URLError, UnicodeDecodeError,
                json.JSONDecodeError, TypeError, ValueError,
            ):
                # Keep the deterministic withheld advisory from the first pass.
                # A formatting repair is never allowed to turn a safe prompt into
                # a transport failure or a less constrained answer.
                safety_flags = tuple(dict.fromkeys((
                    *safety_flags, "structured_repair_failed",
                )))
                repair_attempt["result"] = "request_or_parse_failed"
            finally:
                repair_seconds += time.monotonic() - repair_started
                repair_attempt["latency_seconds"] = round(
                    time.monotonic() - repair_started, 3,
                )
                repair_attempts.append(repair_attempt)
        else:
            safety_flags = tuple(dict.fromkeys((
                *safety_flags, "structured_repair_context_unavailable",
            )))
            repair_attempts.append({
                "attempt": 1, "reason": list(repair_reasons),
                "output_budget_tokens": repair_tokens,
                "schema": "compact_advisory_repair",
                "result": "insufficient_context",
            })
    if preserve_findings_on_schema_failure and any(
        flag in safety_flags for flag in (
            "structured_repair_failed", "structured_repair_context_unavailable",
        )
    ):
        recovered, recovery_flags = _recover_read_only_prose(primary_text, context)
        advisory = recovered
        safety_flags = tuple(dict.fromkeys((*safety_flags, *recovery_flags)))
    if settings.mode in {"managed_local", "custom_local"} and repair_needed:
        active_server.schedule_stop(LOCAL_RUNTIME_KEEPALIVE_SECONDS)
    inference_seconds = time.monotonic() - started
    truncated = plan.truncated or finish_reason == "length"
    try:
        receipt = write_assistant_receipt(
            settings.root, prompt_hash=prompt_hash, model=model_name, mode=settings.mode,
            context=plan.context, omitted_context=plan.omitted_context,
            estimated_input_tokens=plan.estimated_input_tokens,
            actual_input_tokens=(int(actual_input) if actual_input is not None else None),
            actual_output_tokens=(int(actual_output) if actual_output is not None else None),
            startup_seconds=startup_seconds, inference_seconds=inference_seconds,
            grounding_seconds=grounding_seconds,
            primary_inference_seconds=primary_inference_seconds,
            repair_seconds=repair_seconds,
            configured_context_tokens=settings.context_tokens,
            reserved_output_tokens=plan.reserved_output_tokens,
            repair_attempts=repair_attempts,
            truncated=truncated, advisory=advisory, safety_flags=safety_flags,
            sdk_build_id=sdk_build_id, model_sha256=model_sha256,
            llama_cpp_revision=llama_cpp_revision,
            provider_capabilities=settings.provider_capabilities,
            thinking=settings.thinking,
        )
        receipt_path = str(receipt)
    except (OSError, TypeError, ValueError):
        receipt_path = ""
    notify("complete")
    return PromptResult(
        text=format_advisory(advisory), model=model_name, mode=settings.mode,
        elapsed_seconds=round(grounding_seconds + startup_seconds + inference_seconds, 3),
        advisory=advisory, context=plan.context, safety_flags=safety_flags,
        estimated_input_tokens=plan.estimated_input_tokens,
        actual_input_tokens=(int(actual_input) if actual_input is not None else None),
        actual_output_tokens=(int(actual_output) if actual_output is not None else None),
        reserved_output_tokens=plan.reserved_output_tokens,
        startup_seconds=round(startup_seconds, 3),
        grounding_seconds=round(grounding_seconds, 3),
        primary_inference_seconds=round(primary_inference_seconds, 3),
        repair_seconds=round(repair_seconds, 3),
        inference_seconds=round(inference_seconds, 3), truncated=truncated,
        repair_attempts=tuple(repair_attempts),
        omitted_context=plan.omitted_context, receipt_path=receipt_path,
        sdk_build_id=sdk_build_id, model_sha256=model_sha256,
        llama_cpp_revision=llama_cpp_revision,
        provider_capabilities=settings.provider_capabilities,
        thinking=settings.thinking,
    )


def _merge_review_advisories(
    advisories: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    selected = list(advisories)
    blocked_operation_abstentions: list[str] = []

    def complete_summary(value: str, *, limit: int = 800) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            return "Grounded review completed."
        if len(normalized) <= limit:
            if normalized[-1:] in ".!?":
                return normalized
            return (normalized + ".") if len(normalized) < limit else normalized[:-1] + "."
        candidate = normalized[:limit]
        endings = [match.end() for match in re.finditer(r"[.!?](?=\s|$)", candidate)]
        if endings and endings[-1] >= limit // 3:
            return candidate[:endings[-1]]
        return (
            "The model summary exceeded the deterministic merge limit and was omitted; "
            "use the complete grounded findings and abstentions below."
        )

    def unique_records(key: str, *, limit: int) -> list[dict[str, object]]:
        output: list[dict[str, object]] = []
        seen: set[str] = set()
        for advisory in selected:
            values = advisory.get(key, [])
            if not isinstance(values, list):
                continue
            for raw in values:
                if not isinstance(raw, Mapping):
                    continue
                item = dict(raw)
                if key == "recommended_operations":
                    name = str(item.get("operation", "")).strip() or "unknown operation"
                    blocked_reason = str(item.get("blocked_reason", "")).strip()
                    if item.get("executed") is not False:
                        continue
                    if item.get("arguments_grounded") is not True or blocked_reason:
                        explanation = blocked_reason or "its arguments were not grounded"
                        abstention = (
                            f"'{name}' was omitted from the merged recommendations because "
                            f"{explanation}."
                        )
                        if abstention not in blocked_operation_abstentions:
                            blocked_operation_abstentions.append(abstention)
                        continue
                if key == "proposed_changes" and (
                    item.get("executed") is not False
                    or item.get("execution_authorized") is not False
                ):
                    continue
                identity = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if identity in seen:
                    continue
                seen.add(identity)
                output.append(item)
                if len(output) >= limit:
                    return output
        return output

    def unique_strings(key: str, *, limit: int) -> list[str]:
        output: list[str] = []
        for advisory in selected:
            values = advisory.get(key, [])
            if not isinstance(values, list):
                continue
            for value in values:
                text = str(value).strip()
                if text and text not in output:
                    output.append(text)
                    if len(output) >= limit:
                        return output
        return output

    summaries = [
        str(item.get("summary", "")).strip() for item in selected
        if str(item.get("summary", "")).strip()
    ]
    findings = unique_records("findings", limit=8)
    operations = unique_records("recommended_operations", limit=6)
    changes = unique_records("proposed_changes", limit=6)
    missing_context = unique_strings("missing_context", limit=8)
    abstentions = unique_strings("abstentions", limit=8)
    for abstention in blocked_operation_abstentions:
        if abstention not in abstentions and len(abstentions) < 8:
            abstentions.append(abstention)
    preflight_abstentions = sum(
        1 for item in selected
        if item.get("review_status") == "abstained_before_inference"
    )
    if len(selected) > 1:
        completed = len(selected) - preflight_abstentions
        summary = (
            f"Deterministically merged {completed} grounded review chunk(s) and "
            f"{preflight_abstentions} pre-inference abstention(s)."
            if preflight_abstentions else
            f"Deterministically merged {completed} grounded review chunk(s)."
        )
    else:
        summary = complete_summary(summaries[0] if summaries else "")
    return {
        "summary": summary,
        "findings": findings,
        "recommended_operations": operations,
        "proposed_changes": changes,
        "missing_context": missing_context,
        "abstentions": abstentions,
    }


def review_assistant(
    symbols: Iterable[str], *, root: Path | None = None,
    repository_root: Path | None = None, sources: Iterable[Path] = (),
    priorities: Iterable[str] = ("callers", "tests", "state-transitions"),
    question: str = "", timeout: float = 180.0, startup_timeout: float = 90.0,
    max_tokens: int = 1024, chunk_size: int = 3,
    preserve_findings_on_schema_failure: bool = True,
    progress: Callable[[str], None] | None = None, opener=urlopen,
    server: LocalAssistantServer | None = None,
) -> ReviewResult:
    """Run a repository-grounded, automatically chunked multi-symbol audit."""
    from allin1_sdk.assistant_evidence import (
        REVIEW_PRIORITIES, discover_symbol_sources,
    )

    selected_symbols = tuple(dict.fromkeys(
        item.strip() for item in symbols if item.strip()
    ))
    if not selected_symbols:
        raise ValueError("Assistant review requires at least one symbol")
    if len(selected_symbols) > 32:
        raise ValueError("Assistant review is limited to 32 symbols")
    if not 1 <= chunk_size <= 8:
        raise ValueError("Assistant review chunk size must be between 1 and 8")
    selected_priorities = tuple(dict.fromkeys(
        item.strip().casefold() for item in priorities if item.strip()
    ))
    unknown = [item for item in selected_priorities if item not in REVIEW_PRIORITIES]
    if unknown:
        raise ValueError("Unknown assistant review priorities: " + ", ".join(unknown))
    repository = (repository_root or Path.cwd()).expanduser().resolve(strict=True)
    explicit_sources = tuple(
        path.expanduser().resolve(strict=True) for path in sources
    )
    if explicit_sources:
        discovery: dict[str, object] = {
            "repository_root": str(repository), "symbols": list(selected_symbols),
            "sources": [str(path) for path in explicit_sources],
            "source_selection": "explicit",
        }
    else:
        discovery = discover_symbol_sources(
            repository, selected_symbols, allow_missing=True,
        )

    missing_discovery = [
        str(item) for item in discovery.get("missing_symbols", []) if str(item)
    ]
    if missing_discovery:
        reason = (
            "Automatic discovery could not locate complete definitions for: "
            + ", ".join(missing_discovery)
        )
        return ReviewResult(
            symbols=selected_symbols, priorities=selected_priorities,
            source_discovery=discovery,
            chunks=({
                "index": 1, "symbols": list(selected_symbols),
                "sources": list(discovery.get("sources", [])),
                "status": "abstained_before_inference",
                "inference_started": False,
                "explicit_symbols_preserved": False,
                "receipt": "", "estimated_input_tokens": 0,
                "reserved_output_tokens": max_tokens,
                "actual_input_tokens": None, "actual_output_tokens": None,
                "truncated": bool(discovery.get("scan_truncated", False)),
                "omitted_context_summary": [reason], "repair_attempts": [],
                "latency_seconds": 0.0,
                "safety_flags": ["review_abstained_missing_definition"],
            },),
            advisory={
                "summary": "Review abstained before inference because requested definitions were missing.",
                "findings": [], "recommended_operations": [],
                "proposed_changes": [], "missing_context": [reason],
                "abstentions": [
                    "No model inference ran without every requested definition."
                ],
            },
        )

    discovered_by_symbol = discovery.get("symbol_sources", {})

    def chunk_sources(chunk: tuple[str, ...]) -> tuple[Path, ...]:
        if explicit_sources:
            return explicit_sources
        paths: list[Path] = []
        if isinstance(discovered_by_symbol, Mapping):
            for symbol in chunk:
                records = discovered_by_symbol.get(symbol, [])
                if not isinstance(records, list):
                    continue
                for record in records:
                    if isinstance(record, Mapping) and record.get("path"):
                        path = Path(str(record["path"])).resolve()
                        if path not in paths:
                            paths.append(path)
        return tuple(paths)

    pending = [
        selected_symbols[index:index + chunk_size]
        for index in range(0, len(selected_symbols), chunk_size)
    ]
    merged_advisories: list[Mapping[str, object]] = []
    chunk_records: list[dict[str, object]] = []
    while pending:
        chunk = pending.pop(0)
        selected_chunk_sources = chunk_sources(chunk)
        audit_question = question.strip() or (
            "Audit the requested C++ lifecycle/concurrency symbols. Identify grounded "
            "correctness risks, state-transition gaps, and a specific advisory-only fix."
        )
        audit_question += " Requested symbols: " + ", ".join(chunk) + "."
        try:
            result = prompt_assistant(
                audit_question, root=root, repository_root=repository,
                sources=selected_chunk_sources, symbols=chunk,
                source_priorities=selected_priorities,
                preserve_findings_on_schema_failure=(
                    preserve_findings_on_schema_failure
                ),
                compact_response=True, timeout=timeout,
                require_explicit_symbols_preserved=True,
                startup_timeout=startup_timeout, max_tokens=max_tokens,
                progress=progress, opener=opener, server=server,
            )
        except (AssistantContextOverflow, ValueError) as error:
            if isinstance(error, ValueError) and not isinstance(
                error, AssistantContextOverflow,
            ):
                if not str(error).startswith(
                    "Requested symbol definition exceeds the "
                ):
                    raise
                details: Mapping[str, object] = {
                    "message": str(error), "receipt_path": "",
                    "estimated_input_tokens": 0,
                    "inference_started": False,
                }
            else:
                details = error.details
            if len(chunk) > 1:
                midpoint = max(1, len(chunk) // 2)
                pending[0:0] = [chunk[:midpoint], chunk[midpoint:]]
                continue
            symbol = chunk[0]
            reason = str(details.get("message", str(error))).strip()
            abstention = {
                "review_status": "abstained_before_inference",
                "summary": (
                    f"Review abstained before inference for '{symbol}' because its "
                    "requested definition could not be preserved in full."
                ),
                "findings": [], "recommended_operations": [],
                "proposed_changes": [],
                "missing_context": [
                    "Use a larger assistant context or narrow the selected source while "
                    "keeping the requested definition complete."
                ],
                "abstentions": [reason],
            }
            merged_advisories.append(abstention)
            chunk_records.append({
                "index": len(chunk_records) + 1, "symbols": [symbol],
                "sources": [str(path) for path in selected_chunk_sources],
                "status": "abstained_before_inference",
                "inference_started": False,
                "explicit_symbols_preserved": False,
                "receipt": str(details.get("receipt_path", "")),
                "estimated_input_tokens": int(
                    details.get("estimated_input_tokens", 0) or 0
                ),
                "reserved_output_tokens": max_tokens,
                "actual_input_tokens": None, "actual_output_tokens": None,
                "truncated": True, "omitted_context_summary": [reason],
                "repair_attempts": [], "latency_seconds": 0.0,
                "safety_flags": ["review_abstained_before_inference"],
            })
            continue
        merged_advisories.append(result.advisory or {})
        preflight = _grounding_preflight(result.context or {})
        chunk_records.append({
            "index": len(chunk_records) + 1, "symbols": list(chunk),
            "sources": [str(path) for path in selected_chunk_sources],
            "status": "completed", "inference_started": True,
            "explicit_symbols_preserved": bool(
                preflight["explicit_symbols_preserved"]
            ),
            "receipt": result.receipt_path,
            "estimated_input_tokens": result.estimated_input_tokens,
            "reserved_output_tokens": result.reserved_output_tokens,
            "actual_input_tokens": result.actual_input_tokens,
            "actual_output_tokens": result.actual_output_tokens,
            "truncated": result.truncated,
            "omitted_context_summary": list(result.omitted_context),
            "repair_attempts": [dict(item) for item in result.repair_attempts],
            "latency_seconds": result.elapsed_seconds,
            "safety_flags": list(result.safety_flags),
        })
    advisory = _merge_review_advisories(merged_advisories)
    return ReviewResult(
        symbols=selected_symbols, priorities=selected_priorities,
        source_discovery=discovery, chunks=tuple(chunk_records),
        advisory=advisory,
    )


def assistant_status(root: Path | None = None) -> dict[str, object]:
    settings = load_assistant_settings(root)
    payload: dict[str, object] = {
        "root": str(settings.root), "mode": settings.mode,
        "enabled": settings.enabled, "workflow": settings.workflow,
        "profile": settings.profile, "local_runtime_running": _LOCAL_SERVER.running,
        "local_runtime_keepalive_seconds": LOCAL_RUNTIME_KEEPALIVE_SECONDS,
        "grounding_cache_scope": "current SDK or Agent process",
        "sdk_version": __version__, "sdk_build_id": _sdk_build_id(),
        "assistant_schema_version": ASSISTANT_RESPONSE_SCHEMA_VERSION,
        "provider_capabilities": list(settings.provider_capabilities),
        "thinking": settings.thinking,
        "structured_output_ready": (
            CAPABILITY_JSON_SCHEMA in settings.provider_capabilities
        ),
    }
    if settings.mode in {"managed_local", "custom_local"} and settings.enabled:
        spec = local_runtime_spec(settings)
        payload.update({
            "model": spec.model_name, "runtime": str(spec.runtime),
            "model_sha256": spec.model_sha256,
            "llama_cpp_revision": spec.llama_cpp_revision or "unknown",
        })
    elif settings.mode == "compatible_api":
        payload.update({
            "model": settings.model_name, "endpoint": settings.endpoint,
            "model_sha256": settings.model_sha256 or "unknown",
            "llama_cpp_revision": settings.llama_cpp_revision or "not_applicable_or_unknown",
        })
    else:
        payload.update({
            "model_sha256": "not_applicable",
            "llama_cpp_revision": "not_applicable",
        })
    return payload


def stop_local_assistant() -> bool:
    return _LOCAL_SERVER.stop()

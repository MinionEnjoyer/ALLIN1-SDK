from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk import cli
from allin1_sdk.agent_api import command_catalog
from allin1_sdk.assistant_client import (
    AssistantContextOverflow, plan_grounding, prompt_assistant,
    prompt_structured_assistant, validate_advisory,
)
from allin1_sdk.assistant_context import build_assistant_context
from allin1_sdk.assistant_evidence import (
    cached_inspect_source, clear_evidence_cache, compact_grounding,
    compare_telemetry, inspect_log, inspect_source,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _assistant_config(root: Path, *, context_tokens: int = 8192) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({
        "schema": 1, "mode": "compatible_api", "workflow": "diagnostic",
        "profile": "custom", "endpoint": "http://127.0.0.1:9000/v1",
        "model_name": "qwen-test", "api_key_env": "", "runtime_path": "",
        "model_path": "", "context_tokens": context_tokens, "temperature": 0.1,
        "capabilities": ["structured_output.json_schema"],
    }), encoding="utf-8")


def _repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://example.invalid/EZ-GTA-V-R.git\n',
        encoding="utf-8",
    )
    return root


def test_read_only_source_log_and_telemetry_commands(tmp_path: Path) -> None:
    source = tmp_path / "renderer.cpp"
    source.write_text(
        "void ScanPromotedRoot() {\n  // bloom-positive\n  ResolveCandidate();\n}\n",
        encoding="utf-8",
    )
    log = tmp_path / "runtime.log"
    log.write_text("scans=203944\ncandidates=0\nstate=stable\n", encoding="utf-8")
    newer = tmp_path / "runtime-new.log"
    newer.write_text("scans=12\ncandidates=2\n", encoding="utf-8")

    inspected = inspect_source(source, symbols=("ScanPromotedRoot",), context_lines=2)
    assert inspected["kind"] == "source"
    assert "ResolveCandidate" in inspected["excerpts"][0]["text"]
    telemetry = inspect_log(log, patterns=("candidates",))
    assert telemetry["matched_lines"] == 1 and "candidates=0" in telemetry["excerpt"]
    comparison = compare_telemetry(log, newer)
    assert {item["metric"]: item["delta"] for item in comparison["changes"]} == {
        "candidates": 2.0, "scans": -203932.0,
    }

    runner = CliRunner()
    output = runner.invoke(cli.main, [
        "inspect-source", str(source), "--symbol", "ScanPromotedRoot",
    ])
    assert output.exit_code == 0
    assert json.loads(output.output)["sha256"] == inspected["sha256"]
    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["inspect-source"]["risk"] == "read_only"
    assert catalog["inspect-log"]["risk"] == "read_only"
    assert catalog["compare-telemetry"]["risk"] == "read_only"


def test_large_log_is_streamed_and_keeps_pattern_centered_evidence(tmp_path: Path) -> None:
    log = tmp_path / "large.log"
    with log.open("wb") as output:
        output.write(b"ordinary telemetry\n" * 500_000)
        output.write(
            b"prefix " + (b"x" * 20_000)
            + b" matrix_promoted_role_expansion_v1 scans=203944 candidates=0 tail\n"
        )

    result = inspect_log(
        log, patterns=("matrix_promoted_role_expansion_v1",), max_lines=10,
    )

    assert result["matched_lines"] == 1
    assert "scans=203944 candidates=0" in result["excerpt"]
    assert result["sha256"]


def test_source_compaction_preserves_each_selected_symbol_window(tmp_path: Path) -> None:
    source = tmp_path / "admission.cpp"
    source.write_text(
        "bool BloomGate() { return maybe_contains(root); }\n"
        + ("// unrelated filler\n" * 100)
        + "bool ExactGate() { return exact_roots.contains(root); }\n",
        encoding="utf-8",
    )
    inspected = inspect_source(
        source, symbols=("BloomGate", "ExactGate"), context_lines=2,
    )

    compacted = compact_grounding(inspected, max_chars=600)
    excerpts = "\n".join(item["text"] for item in compacted["excerpts"])

    assert "BloomGate" in excerpts
    assert "ExactGate" in excerpts


def test_requested_definition_is_complete_and_counter_dependencies_are_retrieved(
    tmp_path: Path,
) -> None:
    source = tmp_path / "renderer.cpp"
    source.write_text(
        "std::atomic<unsigned> g_rootChecks{0};\n"
        "bool DiagnoseRoot(int value)\n{\n"
        "  auto checks = g_rootChecks.load();\n"
        "  if (value) { return checks > 0; }\n"
        "  return false; // crucial final decision\n}\n"
        "void RecordRoot() { g_rootChecks.fetch_add(1); }\n"
        "void ResetRoot() { g_rootChecks.store(0); }\n",
        encoding="utf-8",
    )

    inspected = inspect_source(source, symbols=("DiagnoseRoot",), context_lines=1)
    excerpt = inspected["excerpts"][0]
    assert excerpt["selection"] == "definition"
    assert excerpt["preserve_full"] is True and excerpt["truncated"] is False
    assert "crucial final decision" in excerpt["text"]
    assert excerpt["text"].rstrip().endswith("}")
    assert inspected["dependency_identifiers"] == ["g_rootChecks"]
    assert {item["role"] for item in inspected["dependencies"]} == {"writer", "reset"}

    compacted = compact_grounding(inspected, max_chars=256)
    assert compacted["excerpts"][0]["text"] == excerpt["text"]


def test_csharp_controller_grounding_keeps_package_id_declaration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "RealisticSuppressorController.cs"
    source.write_text(
        "namespace ALLIN1.RealisticSuppressors\n"
        "{\n"
        "  public sealed class RealisticSuppressorController\n"
        "  {\n"
        '    private const string PackageId = "realistic-suppressors";\n'
        "    private const int RetryMs = 2000;\n"
        "\n"
        "    public RealisticSuppressorController()\n"
        "    {\n"
        "      bool enabled = Allin1ExtensionApi.IsPackageEnabled(PackageId);\n"
        "      if (!enabled) { return; }\n"
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    inspected = inspect_source(
        source, symbols=("RealisticSuppressorController",), context_lines=1,
    )
    excerpt = inspected["excerpts"][0]
    assert excerpt["selection"] == "definition"
    assert "IsPackageEnabled(PackageId)" in excerpt["text"]
    assert inspected["declaration_identifiers"] == ["PackageId"]
    declaration = inspected["declarations"][0]
    assert declaration["preserve_full"] is True
    assert declaration["truncated"] is False
    assert 'PackageId = "realistic-suppressors"' in declaration["text"]

    compacted = compact_grounding(inspected, max_chars=256)
    assert compacted["declarations"][0]["text"] == declaration["text"]

    explicit = inspect_source(source, symbols=("PackageId",), context_lines=0)
    requested = explicit["excerpts"][0]
    assert requested["selection"] == "declaration"
    assert requested["line_start"] == requested["line_end"] == 5
    assert 'PackageId = "realistic-suppressors"' in requested["text"]
    assert requested["preserve_full"] is True and requested["truncated"] is False


def test_named_cpp_lambda_is_selected_instead_of_multiline_call_site(tmp_path: Path) -> None:
    source = tmp_path / "renderer.cpp"
    source.write_text(
        "void Render() {\n"
        "  auto consume_exact_candidate = [&](int candidate) noexcept -> bool {\n"
        "    if (candidate == 7) { return true; }\n"
        "    return false; // lambda final line\n"
        "  };\n"
        "  if (ready() &&\n"
        "      consume_exact_candidate(\n"
        "          current_candidate)) {\n"
        "    publish();\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    inspected = inspect_source(source, symbols=("consume_exact_candidate",))
    excerpt = inspected["excerpts"][0]
    assert excerpt["selection"] == "definition"
    assert excerpt["line_start"] == 2 and excerpt["line_end"] == 5
    assert "lambda final line" in excerpt["text"]
    assert "publish" not in excerpt["text"]


def test_session_telemetry_aggregates_every_match_not_only_newest(tmp_path: Path) -> None:
    log = tmp_path / "session.log"
    log.write_text(
        "renderer_lane=(checks=76270 candidates=2)\n"
        "renderer_lane=(checks=0 candidates=0)\n",
        encoding="utf-8",
    )

    result = inspect_log(log, patterns=("renderer_lane",), max_lines=1)
    aggregate = result["session_aggregates"][0]
    checks = aggregate["metrics"]["checks"]
    assert aggregate["matched_records"] == 2
    assert checks["sum"] == 76270 and checks["max"] == 76270
    assert checks["last"] == 0 and checks["nonzero_samples"] == 1
    assert checks["observed_counter_total"] == 76270
    assert checks["counter_segments"] == 2
    assert result["aggregation_scope"] == "entire_selected_file"

    compacted = compact_grounding(result, max_chars=256)
    compact_checks = compacted["session_aggregates"][0]["active_metrics"]["checks"]
    assert compact_checks["observed_counter_total"] == 76270
    assert compacted["session_aggregates"][0]["active_metrics"]["candidates"]["max"] == 2
    twice = compact_grounding(compacted, max_chars=128)
    assert twice["session_aggregates"] == compacted["session_aggregates"]


def test_unchanged_source_grounding_is_cached_and_file_change_invalidates_it(
    tmp_path: Path,
) -> None:
    clear_evidence_cache()
    source = tmp_path / "renderer.cpp"
    source.write_text("bool Gate() { return true; }\n", encoding="utf-8")
    first = cached_inspect_source(source, symbols=("Gate",))
    second = cached_inspect_source(source, symbols=("Gate",))
    assert first["cache_hit"] is False and second["cache_hit"] is True

    source.write_text("bool Gate() { return false; } // changed\n", encoding="utf-8")
    changed = cached_inspect_source(source, symbols=("Gate",))
    assert changed["cache_hit"] is False


def test_context_retrieves_selected_symbols_and_rejects_vague_native_operation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "renderer.cpp"
    source.write_text("bool AdmitRoot() { return exactRoots.contains(root); }\n", encoding="utf-8")
    log = repository / "session.log"
    log.write_text("bloom_positive=203944\ncandidates=0\n", encoding="utf-8")
    context = build_assistant_context(
        "Compare renderer source and runtime telemetry for Bloom root admission",
        repository_root=repository, workspace_roots=(repository,),
        sources=(source,), symbols=("AdmitRoot",), telemetry_files=(log,),
        telemetry_patterns=("candidates",),
    )
    operations = {item["name"]: item for item in context.relevant_operations}
    assert "inspect-native-asset" not in operations
    assert "inspect-package-receipt" not in operations
    assert set(operations) == {"compare-telemetry"}
    assert set(context.completed_operations) == {"inspect-source", "inspect-log"}
    assert {item["kind"] for item in context.selected_grounding} == {"source", "telemetry"}
    assert context.missing_context == ()
    assert "allin1-sdk validate-package <mod.toml>" not in context.validation_commands


def test_root_cause_wording_does_not_misclassify_package_installation_as_source_code() -> None:
    from allin1_sdk.agent_api import command_catalog
    from allin1_sdk.assistant_context import retrieve_operations

    operations = {
        item["name"] for item in retrieve_operations(
            "Find the root cause of this package installation failure",
            command_catalog(),
        )
    }

    assert {"validate-package", "install-package"} <= operations
    assert "inspect-source" not in operations

    renderer_operations = {
        item["name"] for item in retrieve_operations(
            "Diagnose a circular renderer source dependency and validate the fix",
            command_catalog(),
        )
    }
    assert "inspect-source" in renderer_operations
    assert not renderer_operations.intersection({
        "validate-package", "install-package", "inspect-package-receipt",
    })


def test_failed_package_validation_keeps_raw_manifest_grounded(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "ALLIN1")
    package = repository / "mods" / "realistic-suppressors"
    package.mkdir(parents=True)
    manifest = package / "mod.toml"
    manifest_text = (
        "schema_version = 99\n"
        'id = "realistic-suppressors"\n'
        'name = "Realistic Suppressors"\n'
        'version = "1.0.0"\n'
        'type = "script"\n'
        'editions = ["legacy", "enhanced"]\n'
        'dependencies = ["shvdn"]\n'
        "\n[allin1]\n"
        "api_version = 1\n"
        'content = "allin1.content.json"\n'
        "\n[[files]]\n"
        'source = "payload/RealisticSuppressors.dll"\n'
        'destination = "scripts/RealisticSuppressors/RealisticSuppressors.dll"\n'
    )
    manifest.write_bytes(manifest_text.encode("utf-8"))

    context = build_assistant_context(
        "Review this package integration", repository_root=repository,
        workspace_roots=(repository,), manifest=manifest,
    )

    assert context.package["validated"] is False
    assert "schema_version" in str(context.package["validation_error"])
    # A syntactically declared id remains evidence, but it is deliberately not
    # promoted to the validated lifecycle-operation id.
    assert "id" not in context.package
    raw = context.package["raw_manifest"]
    assert raw["text"] == manifest_text
    assert raw["truncated"] is False
    assert raw["declared_fields"] == {
        "schema_version": 99,
        "id": "realistic-suppressors",
        "name": "Realistic Suppressors",
        "version": "1.0.0",
        "type": "script",
        "editions": ["legacy", "enhanced"],
    }

    plan = plan_grounding(
        context, "Review this package integration", "",
        context_tokens=8192, max_tokens=640,
    )
    planned_raw = plan.context["package"]["raw_manifest"]
    assert planned_raw["text"] == manifest_text
    assert planned_raw["declared_fields"]["id"] == "realistic-suppressors"


def test_explicit_verified_gta_path_grants_read_only_telemetry_scope(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "workspace" / "GTAV-ALLIN1-VR")
    game = tmp_path / "installed-game"
    logs = game / "scripts" / "ALLIN1" / "logs"
    logs.mkdir(parents=True)
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    telemetry = logs / "combat.log"
    telemetry.write_text("containment=(checks=8 holds=3)\n", encoding="utf-8")

    context = build_assistant_context(
        "Review GTA telemetry", repository_root=repository, gta_path=game,
        telemetry_files=(telemetry,), telemetry_patterns=("containment",),
    )

    record = context.selected_grounding[0]
    assert record["kind"] == "telemetry"
    assert record["path"] == str(telemetry.resolve())
    assert record["access_scope"] == "explicit_verified_gta_path_read_only"
    assert context.gta_installation["source"] == "explicit"
    assert context.gta_installation["verified"] is True

    outside = tmp_path / "untrusted.log"
    outside.write_text("containment=99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="explicit verified --gta-path"):
        build_assistant_context(
            "Review GTA telemetry", repository_root=repository, gta_path=game,
            telemetry_files=(outside,), telemetry_patterns=("containment",),
        )


def test_requested_symbols_must_have_explicit_populated_grounding(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "renderer.cpp"
    source.write_text("bool PresentGate() { return true; }\n", encoding="utf-8")

    with pytest.raises(ValueError, match="--symbol requires"):
        build_assistant_context(
            "Review PresentGate", repository_root=repository,
            symbols=("PresentGate",),
        )
    with pytest.raises(ValueError, match="not found in any selected source"):
        build_assistant_context(
            "Review MissingGate", repository_root=repository,
            sources=(source,), symbols=("MissingGate",),
        )

    populated = build_assistant_context(
        "Review PresentGate", repository_root=repository,
        sources=(source,), symbols=("PresentGate",),
    )
    excerpt = populated.selected_grounding[0]["excerpts"][0]
    assert excerpt["symbol"] == "PresentGate" and "PresentGate" in excerpt["text"]


def test_context_budget_prunes_explicit_evidence_and_reports_omissions(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "large.cpp"
    source.write_text(
        "\n".join(
            f"bool Gate_{i:04d}() {{ return bloom[{i}]; }} // " + ("detail " * 24)
            for i in range(2000)
        ),
        encoding="utf-8",
    )
    symbols = tuple(f"Gate_{i:04d}" for i in range(0, 2000, 250))
    context = build_assistant_context(
        "Inspect AdmitRoot admission", repository_root=repository,
        sources=(source,), symbols=symbols,
    )
    plan = plan_grounding(
        context, "Inspect AdmitRoot admission", "", context_tokens=6144, max_tokens=900,
    )
    assert plan.estimated_input_tokens <= plan.input_budget_tokens
    planned_symbols = {
        excerpt["symbol"]
        for record in plan.context["selected_grounding"]
        for excerpt in record.get("excerpts", [])
    }
    assert planned_symbols == set(symbols)
    assert plan.context["context_budget"]["reserved_tokens"] >= 512
    assert (
        plan.context["context_budget"]["estimated_input_tokens"]
        == plan.estimated_input_tokens
    )
    assert plan.context["grounding_preflight"]["explicit_symbols_preserved"] is True
    assert all(
        excerpt["preserve_full"] and not excerpt["truncated"]
        for record in plan.context["selected_grounding"]
        for excerpt in record.get("excerpts", [])
    )

    with pytest.raises(AssistantContextOverflow) as captured:
        plan_grounding(
            context, "Inspect AdmitRoot admission", "",
            context_tokens=2048, max_tokens=1800,
        )
    assert captured.value.details["error"] == "assistant_context_overflow"
    assert "Reduce --max-tokens" in captured.value.details["message"]


def test_context_budget_compacts_large_requested_definitions_without_dropping_symbols(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "large-definitions.cpp"
    body = "\n".join(f"  total += {index}; // important branch" for index in range(650))
    source.write_text(
        f"int FirstGate() {{\n  int total = 0;\n{body}\n  return total;\n}}\n"
        f"int SecondGate() {{\n  int total = 0;\n{body}\n  return total + 1;\n}}\n",
        encoding="utf-8",
    )
    context = build_assistant_context(
        "Compare FirstGate and SecondGate", repository_root=repository,
        sources=(source,), symbols=("FirstGate", "SecondGate"),
    )

    plan = plan_grounding(
        context, "Compare FirstGate and SecondGate", "",
        context_tokens=6144, max_tokens=700,
    )

    excerpts = plan.context["selected_grounding"][0]["excerpts"]
    assert {item["symbol"] for item in excerpts} == {"FirstGate", "SecondGate"}
    assert all(item["truncated"] and not item["preserve_full"] for item in excerpts)
    assert all("not confirmation evidence" in item["text"] for item in excerpts)
    assert plan.estimated_input_tokens <= plan.input_budget_tokens
    assert any(
        "every requested symbol remains represented" in item
        for item in plan.omitted_context
    )


def test_compacted_symbols_prioritize_query_relevant_middle_lines(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "bootstrap.cpp"
    filler = "\n".join(
        f"  total += {index}; // generic bookkeeping" for index in range(300)
    )
    source.write_text(
        "bool AdmitPromotedRoot() {\n"
        "  int total = 0;\n"
        f"{filler}\n"
        "  if (bootstrapCounter == 0) { return false; } // decisive admission guard\n"
        f"{filler}\n"
        "  return total > 0;\n"
        "}\n",
        encoding="utf-8",
    )
    question = (
        "Does the bootstrapCounter admission guard create a circular dependency "
        "inside AdmitPromotedRoot?"
    )
    context = build_assistant_context(
        question, repository_root=repository,
        sources=(source,), symbols=("AdmitPromotedRoot",),
    )

    plan = plan_grounding(
        context, question, "", context_tokens=4096, max_tokens=640,
    )

    excerpt = plan.context["selected_grounding"][0]["excerpts"][0]
    assert excerpt["preserve_full"] is False
    assert excerpt["compaction"] == "query_ranked_numbered_windows"
    assert "bootstrapCounter == 0" in excerpt["text"]
    assert "bootstrapcounter" in excerpt["query_terms_retained"]
    assert plan.context["grounding_preflight"] == {
        "explicit_symbol_count": 1,
        "explicit_symbols_preserved": False,
        "compacted_symbols": ["AdmitPromotedRoot"],
        "confirmed_findings_allowed": False,
        "policy": "omitted_symbol_lines_are_missing_context_and_cannot_confirm_findings",
    }


def test_compacted_symbol_cannot_support_confirmed_finding(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "renderer.cpp"
    body = "\n".join(f"  state += {index};" for index in range(700))
    source.write_text(
        f"bool AdmitRoot() {{\n{body}\n  return state > 0;\n}}\n",
        encoding="utf-8",
    )
    question = "Confirm the renderer admission behavior in AdmitRoot"
    context = build_assistant_context(
        question, repository_root=repository,
        sources=(source,), symbols=("AdmitRoot",),
    )
    plan = plan_grounding(
        context, question, "", context_tokens=4096, max_tokens=640,
    )
    assert plan.context["grounding_preflight"]["explicit_symbols_preserved"] is False
    response = json.dumps({
        "summary": "The behavior is confirmed.",
        "findings": [{
            "severity_domain": "engineering", "severity": "high",
            "evidence": "The requested function confirms the admission behavior.",
            "file": str(source.resolve()), "line": 702, "confidence": 0.99,
            "status": "confirmed",
        }],
        "recommended_operations": [], "proposed_changes": [],
        "missing_context": [], "abstentions": [],
    })

    advisory, flags = validate_advisory(
        response, context, grounding_context=plan.context,
    )

    finding = advisory["findings"][0]
    assert finding["status"] == "inferred" and finding["confidence"] == 0.75
    assert "confirmed_finding_downgraded_for_compacted_symbol" in flags
    assert any("cannot support confirmed findings" in item for item in advisory["missing_context"])
    assert any("downgraded" in item for item in advisory["abstentions"])


def test_advisory_can_propose_grounded_change_and_rejects_redundant_inspection(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "renderer.cpp"
    source.write_text("bool AdmitRoot() { return false; }\n", encoding="utf-8")
    context = build_assistant_context(
        "Recommend a fix for AdmitRoot without executing it",
        repository_root=repository, sources=(source,), symbols=("AdmitRoot",),
    )
    response = json.dumps({
        "summary": "Break the bootstrap cycle without mutating the repository.",
        "findings": [{
            "severity_domain": "engineering", "severity": "critical",
            "evidence": "The grounded definition returns before admission can progress.",
            "file": str(source.resolve()), "line": 1, "confidence": 0.95,
            "status": "inferred",
        }],
        "recommended_operations": [{
            "operation": "inspect-source", "arguments": [str(source)],
            "rationale": "Inspect the definition.", "expected_result": "The same evidence.",
        }],
        "proposed_changes": [{
            "file": str(source.resolve()), "symbol": "AdmitRoot",
            "summary": "Seed admission independently before consulting the derived counter.",
            "rationale": "This removes the circular bootstrap dependency.",
            "engineering_severity": "high",
        }],
        "missing_context": [], "abstentions": [],
    })

    advisory, flags = validate_advisory(response, context)
    assert advisory["findings"][0]["severity"] == "high"
    assert "engineering_critical_downgraded" in flags
    assert advisory["recommended_operations"] == []
    assert "redundant_operation" in flags
    proposal = advisory["proposed_changes"][0]
    assert proposal["advisory_only"] is True
    assert proposal["execution_authorized"] is False and proposal["executed"] is False


def test_prompt_reports_progress_usage_and_bounded_receipt(tmp_path: Path) -> None:
    assistant = tmp_path / "Assistant"
    _assistant_config(assistant)
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    progress: list[str] = []
    bodies: list[dict[str, object]] = []
    question = "Summarize the renderer telemetry without editing anything."

    def opener(request, timeout):
        assert timeout == 20
        bodies.append(json.loads(request.data))
        response = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({
                    "summary": "No mutation is required.", "findings": [],
                    "recommended_operations": [], "proposed_changes": [],
                    "missing_context": [],
                    "abstentions": ["No selected source or telemetry was supplied."],
                })},
            }],
            "usage": {"prompt_tokens": 421, "completion_tokens": 57},
        }
        return Response(json.dumps(response).encode("utf-8"))

    result = prompt_assistant(
        question, root=assistant, repository_root=repository,
        timeout=20, opener=opener, progress=progress.append,
    )
    assert progress == ["building grounding", "prefill", "generating", "complete"]
    assert len(bodies) == 1 and bodies[0]["max_tokens"] == 640
    schema = bodies[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["summary"]["maxLength"] == 800
    assert schema["properties"]["findings"]["maxItems"] == 8
    assert schema["properties"]["recommended_operations"]["maxItems"] == 6
    assert schema["properties"]["proposed_changes"]["maxItems"] == 6
    assert schema["properties"]["missing_context"]["maxItems"] == 8
    assert schema["properties"]["abstentions"]["maxItems"] == 8
    operation = schema["properties"]["recommended_operations"]["items"]
    assert operation["properties"]["arguments"]["maxItems"] == 16
    assert result.actual_output_tokens == 57 < 640
    assert result.actual_input_tokens == 421 and result.actual_output_tokens == 57
    assert result.receipt_path
    receipt = json.loads(Path(result.receipt_path).read_text(encoding="utf-8"))
    assert receipt["prompt_sha256"] and question not in json.dumps(receipt)
    assert receipt["actual_input_tokens"] == 421
    assert receipt["structured_response"]["summary"] == "No mutation is required."


def test_prompt_surfaces_symbol_compaction_before_inference(tmp_path: Path) -> None:
    assistant = tmp_path / "Assistant"
    _assistant_config(assistant, context_tokens=6144)
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    source = repository / "renderer.cpp"
    source.write_text(
        "bool AdmitRoot() {\n"
        + "\n".join(f"  state += {index};" for index in range(900))
        + "\n  return state > 0;\n}\n",
        encoding="utf-8",
    )
    progress: list[str] = []

    def opener(request, timeout):
        assert timeout == 20
        assert any("explicit_symbols_preserved=false" in item for item in progress)
        body = json.loads(request.data)
        system = body["messages"][0]["content"]
        assert '"explicit_symbols_preserved":false' in system
        response = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({
                    "summary": "The omitted lines prevent confirmation.",
                    "findings": [], "recommended_operations": [],
                    "proposed_changes": [],
                    "missing_context": ["The compacted middle is required."],
                    "abstentions": [],
                })},
            }],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 80},
        }
        return Response(json.dumps(response).encode("utf-8"))

    result = prompt_assistant(
        "Confirm AdmitRoot behavior", root=assistant,
        repository_root=repository, sources=(source,), symbols=("AdmitRoot",),
        timeout=20, opener=opener, max_tokens=640, progress=progress.append,
    )

    warning_index = next(
        index for index, item in enumerate(progress)
        if "explicit_symbols_preserved=false" in item
    )
    assert warning_index < progress.index("prefill")
    assert result.context["grounding_preflight"]["explicit_symbols_preserved"] is False


@pytest.mark.parametrize(("first_content", "finish_reason", "was_truncated"), [
    ("```json\n{\"summary\":\"partial", "length", True),
    (json.dumps({"analysis": "Valid JSON, but not the advisory schema."}), "stop", False),
])
def test_prompt_retries_truncated_unstructured_output_as_strict_json(
    tmp_path: Path, first_content: str, finish_reason: str, was_truncated: bool,
) -> None:
    assistant = tmp_path / "Assistant"
    _assistant_config(assistant)
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    progress: list[str] = []
    bodies: list[dict[str, object]] = []

    def opener(request, timeout):
        assert timeout == 20
        bodies.append(json.loads(request.data))
        if len(bodies) == 1:
            payload = {
                "choices": [{
                    "finish_reason": finish_reason,
                    "message": {"content": first_content},
                }],
                "usage": {"prompt_tokens": 400, "completion_tokens": 600},
            }
        else:
            payload = {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({
                        "summary": "The telemetry shows a containment gap.",
                        "findings": [], "recommended_operations": [],
                        "proposed_changes": [], "missing_context": [],
                        "abstentions": [],
                    })},
                }],
                "usage": {"prompt_tokens": 220, "completion_tokens": 90},
            }
        return Response(json.dumps(payload).encode("utf-8"))

    result = prompt_assistant(
        "Review containment telemetry", root=assistant,
        repository_root=repository, timeout=20, opener=opener,
        max_tokens=600, progress=progress.append,
    )

    assert len(bodies) == 2
    for body in bodies:
        response_format = body["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        schema = response_format["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["findings"]["maxItems"] == 8
    assert bodies[1]["temperature"] == 0
    assert bodies[1]["max_tokens"] == 600
    assert result.advisory["summary"] == "The telemetry shows a containment gap."
    assert "structured_response_repaired" in result.safety_flags
    assert "initial_response_unstructured" in result.safety_flags
    assert ("initial_response_truncated" in result.safety_flags) is was_truncated
    assert "unstructured_response" not in result.safety_flags
    # Usage remains cumulative for performance receipts even though each
    # generation request is capped at the user's 600-token output budget.
    assert result.actual_input_tokens == 620 and result.actual_output_tokens == 690
    assert result.truncated is was_truncated
    assert progress == [
        "building grounding", "prefill", "generating",
        "repairing structured response", "complete",
    ]


def test_generic_structured_prompt_validates_and_repairs_once(tmp_path: Path) -> None:
    assistant = tmp_path / "Assistant"
    _assistant_config(assistant)
    bodies: list[dict[str, object]] = []
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "summary": {"type": "string", "maxLength": 80},
            "changes": {
                "type": "array", "maxItems": 4,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "setting_id": {"type": "string", "maxLength": 64},
                        "value": {"type": ["boolean", "integer", "number", "string"],
                                  "maxLength": 64},
                    },
                    "required": ["setting_id", "value"],
                },
            },
        },
        "required": ["summary", "changes"],
    }

    def opener(request, timeout):
        assert timeout == 20
        bodies.append(json.loads(request.data))
        content = (
            '{"summary":"broken"'
            if len(bodies) == 1
            else json.dumps({"summary": "Safe proposal", "changes": []})
        )
        return Response(json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode("utf-8"))

    result = prompt_structured_assistant(
        "Propose settings without applying them.", response_schema=schema,
        schema_name="settings_proposal_v1", root=assistant, timeout=20,
        opener=opener, max_tokens=300,
    )

    assert result.payload == {"summary": "Safe proposal", "changes": []}
    assert result.repaired is True and len(bodies) == 2
    assert bodies[1]["max_tokens"] == 300
    assert all(body["response_format"]["type"] == "json_schema" for body in bodies)
    assert all(body["response_format"]["json_schema"]["strict"] for body in bodies)


def test_generic_structured_prompt_rejects_unbounded_schema_before_http(
    tmp_path: Path,
) -> None:
    assistant = tmp_path / "Assistant"
    _assistant_config(assistant)
    invoked = False

    def opener(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("HTTP must not be called")

    with pytest.raises(ValueError, match="string is not bounded"):
        prompt_structured_assistant(
            "proposal", response_schema={
                "type": "object", "additionalProperties": False,
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            }, schema_name="unbounded", root=assistant, opener=opener,
        )
    assert invoked is False


def test_json_cli_returns_actionable_context_overflow_without_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assistant = tmp_path / "Assistant"
    _assistant_config(assistant, context_tokens=2048)
    repository = _repository(tmp_path / "GTAV-ALLIN1-VR")
    invoked = False

    def opener(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("inference must not start")

    monkeypatch.setattr("allin1_sdk.assistant_client.urlopen", opener)
    result = CliRunner().invoke(cli.main, [
        "assistant", "prompt", "review", "this", "package",
        "--root", str(assistant), "--repository-root", str(repository),
        "--max-tokens", "1800", "--json-output", "--no-progress",
    ])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"] == "assistant_context_overflow"
    assert invoked is False
    receipts = list((assistant / "receipts").glob("assistant-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["failure_reason"]

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk import assistant_client, cli
from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.assistant_client import (
    AssistantSettings,
    LocalAssistantServer,
    PromptResult,
    assistant_status,
    default_assistant_root,
    load_assistant_settings,
    local_runtime_spec,
    prompt_assistant,
)
from allin1_sdk.sdk_console import (
    command_catalog as console_catalog,
    execute_console_command,
    suggestions_for,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeProcess:
    def __init__(self, *, exit_code=None, wait_timeout: bool = False):
        self.exit_code = exit_code
        self.wait_timeout = wait_timeout
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout=None):
        if self.wait_timeout and not self.killed:
            raise subprocess.TimeoutExpired("assistant", timeout)
        self.exit_code = 0
        return 0


def advisory_content(summary: str) -> str:
    return json.dumps({
        "summary": summary,
        "findings": [{
            "severity_domain": "engineering", "severity": "info",
            "evidence": "Retrieved from the SDK command catalog.",
            "file": "", "line": None, "confidence": 1.0, "status": "confirmed",
        }],
        "recommended_operations": [],
        "proposed_changes": [],
        "missing_context": [],
        "abstentions": [],
    })


def write_config(root: Path, **changes) -> Path:
    payload = {
        "schema": 1,
        "mode": "compatible_api",
        "workflow": "installer",
        "profile": "custom",
        "endpoint": "http://127.0.0.1:9000/v1",
        "model_name": "qwen-test",
        "api_key_env": "",
        "runtime_path": "",
        "model_path": "",
        "context_tokens": 4096,
        "temperature": 0.1,
        "capabilities": ["structured_output.json_schema"],
    }
    for key, value in changes.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    root.mkdir(parents=True, exist_ok=True)
    path = root / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def local_files(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "llama-server.exe"
    model = tmp_path / "qwen.gguf"
    runtime.write_bytes(b"MZruntime")
    model.write_bytes(b"GGUFmodel")
    return runtime, model


def test_shared_root_and_configuration_validation(tmp_path: Path) -> None:
    assert default_assistant_root({"LOCALAPPDATA": str(tmp_path)}) == (
        tmp_path / "ALLIN1" / "Assistant"
    ).resolve()
    with pytest.raises(ValueError, match="not configured"):
        load_assistant_settings(tmp_path / "missing")
    path = write_config(tmp_path / "assistant")
    settings = load_assistant_settings(path.parent)
    assert settings.enabled and settings.model_name == "qwen-test"

    path.write_text("not-json")
    with pytest.raises(ValueError, match="configuration is invalid"):
        load_assistant_settings(path.parent)
    path.write_text("[]")
    with pytest.raises(ValueError, match="schema"):
        load_assistant_settings(path.parent)


@pytest.mark.parametrize("changes,match", [
    ({"mode": "bad"}, "Unsupported assistant mode"),
    ({"context_tokens": 100}, "2,048"),
    ({"temperature": 2}, "between 0.0 and 1.0"),
])
def test_configuration_ranges_are_rechecked(
    tmp_path: Path, changes: dict, match: str,
) -> None:
    write_config(tmp_path, **changes)
    with pytest.raises(ValueError, match=match):
        load_assistant_settings(tmp_path)


def test_provider_capabilities_are_explicit_and_fail_closed(tmp_path: Path) -> None:
    path = write_config(tmp_path / "unknown", capabilities=["json-ish"])
    with pytest.raises(ValueError, match="Unsupported assistant provider capability"):
        load_assistant_settings(path.parent)

    path = write_config(tmp_path / "ambiguous", capabilities=[
        "structured_output.json_schema", "thinking.reasoning_effort",
        "thinking.chat_template_kwargs",
    ])
    with pytest.raises(ValueError, match="ambiguous thinking controls"):
        load_assistant_settings(path.parent)

    root = tmp_path / "undeclared"
    write_config(root, capabilities=[])
    invoked = False

    def opener(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("HTTP must not be called")

    with pytest.raises(ValueError, match="does not declare schema-constrained"):
        prompt_assistant("hello", root=root, opener=opener)
    assert invoked is False


@pytest.mark.parametrize(("capability", "field", "expected"), [
    ("thinking.reasoning_effort", "reasoning_effort", "none"),
    ("thinking.enable_thinking", "enable_thinking", False),
])
def test_compatible_thinking_controls_use_only_declared_wire_shape(
    tmp_path: Path, capability: str, field: str, expected: object,
) -> None:
    root = tmp_path / field
    write_config(root, capabilities=["structured_output.json_schema", capability])
    bodies = []

    def opener(request, timeout):
        assert timeout == 180.0
        bodies.append(json.loads(request.data))
        return Response(json.dumps({
            "choices": [{"message": {"content": advisory_content("Bounded answer")}}],
        }).encode("utf-8"))

    prompt_assistant("hello", root=root, opener=opener)
    assert bodies[0][field] == expected
    other = {"reasoning_effort", "enable_thinking", "chat_template_kwargs"} - {field}
    assert all(name not in bodies[0] for name in other)


def test_custom_and_managed_runtime_specs(tmp_path: Path) -> None:
    runtime, model = local_files(tmp_path)
    custom = AssistantSettings(
        tmp_path, "custom_local", "installer", "custom", "", "", "",
        str(runtime), str(model), 4096, 0.1,
    )
    spec = local_runtime_spec(custom)
    assert spec.model_name == "qwen" and spec.identity[2] == 4096
    assert spec.model_sha256 == hashlib.sha256(model.read_bytes()).hexdigest()

    root = tmp_path / "managed"
    component = root / "component"
    (component / "runtime").mkdir(parents=True)
    (component / "models").mkdir()
    managed_runtime = component / "runtime" / "server.exe"
    managed_model = component / "models" / "qwen.gguf"
    managed_runtime.write_bytes(b"MZserver")
    managed_model.write_bytes(b"GGUFqwen")
    (component / "assistant-package.json").write_text(json.dumps({
        "product": "ALLIN1-Assistant",
        "runtime": {"path": "runtime/server.exe"},
        "model": {"path": "models/qwen.gguf", "name": "Qwen managed"},
    }))
    managed = AssistantSettings(
        root, "managed_local", "installer", "low", "", "", "", "", "",
        8192, 0.1,
    )
    assert local_runtime_spec(managed).model_name == "Qwen managed"
    with pytest.raises(ValueError, match="does not use"):
        local_runtime_spec(AssistantSettings(
            root, "compatible_api", "installer", "custom", "http://x", "x", "",
            "", "", 4096, 0.1,
        ))


def test_runtime_specs_reject_bad_files_and_metadata(tmp_path: Path) -> None:
    runtime, model = local_files(tmp_path)
    model.write_bytes(b"bad")
    settings = AssistantSettings(
        tmp_path, "custom_local", "installer", "custom", "", "", "",
        str(runtime), str(model), 4096, 0.1,
    )
    with pytest.raises(ValueError, match="GGUF"):
        local_runtime_spec(settings)
    model.unlink()
    with pytest.raises(ValueError, match="was not found"):
        local_runtime_spec(settings)

    root = tmp_path / "managed"
    component = root / "component"
    component.mkdir(parents=True)
    (component / "assistant-package.json").write_text("not-json")
    managed = AssistantSettings(
        root, "managed_local", "installer", "low", "", "", "", "", "",
        4096, 0.1,
    )
    with pytest.raises(ValueError, match="metadata is invalid"):
        local_runtime_spec(managed)
    (component / "assistant-package.json").write_text(json.dumps({
        "product": "Other", "runtime": {}, "model": {},
    }))
    with pytest.raises(ValueError, match="wrong product"):
        local_runtime_spec(managed)
    (component / "assistant-package.json").write_text(json.dumps({
        "product": "ALLIN1-Assistant",
        "runtime": {"path": "runtime\\server.exe"},
        "model": {"path": "models/qwen.gguf"},
    }))
    with pytest.raises(ValueError, match="unsafe"):
        local_runtime_spec(managed)


def test_compatible_api_prompt_and_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_config(tmp_path, api_key_env="QWEN_TEST_KEY")
    monkeypatch.setenv("QWEN_TEST_KEY", "secret-value")
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["authorization"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return Response(json.dumps({
            "choices": [{"message": {"content": advisory_content(
                "Use the package inspector first."
            )}}],
        }).encode())

    result = prompt_assistant(
        "How do I install this mod?", root=tmp_path, timeout=12,
        max_tokens=333, opener=opener,
    )
    assert result.advisory["summary"] == "Use the package inspector first."
    assert "Use the package inspector first." in result.text
    assert result.model == "qwen-test" and result.mode == "compatible_api"
    assert seen == {
        "url": "http://127.0.0.1:9000/v1/chat/completions",
        "timeout": 12,
        "authorization": "Bearer secret-value",
        "body": seen["body"],
    }
    assert seen["body"]["max_tokens"] == 333
    assert seen["body"]["messages"][1]["content"] == "How do I install this mod?"
    system = seen["body"]["messages"][0]["content"]
    assert "Never manually copy managed package files" in system
    assert "relevant_operations" in system and "Return exactly one JSON object" in system
    status = assistant_status(tmp_path)
    assert status["endpoint"].endswith("/v1") and status["model"] == "qwen-test"
    assert status["assistant_schema_version"] == 1
    assert status["structured_output_ready"] is True
    assert status["sdk_build_id"]
    assert status["model_sha256"] == "unknown"


def test_prompt_validates_authority_limits_and_api_configuration(tmp_path: Path) -> None:
    write_config(tmp_path, mode="disabled")
    with pytest.raises(ValueError, match="disabled"):
        prompt_assistant("hello", root=tmp_path)
    write_config(tmp_path, endpoint="bad", model_name="qwen")
    with pytest.raises(ValueError, match="HTTP"):
        prompt_assistant("hello", root=tmp_path)
    write_config(tmp_path, model_name="")
    with pytest.raises(ValueError, match="model name"):
        prompt_assistant("hello", root=tmp_path)
    write_config(tmp_path, api_key_env="MISSING_QWEN_KEY")
    with pytest.raises(ValueError, match="is not set"):
        prompt_assistant("hello", root=tmp_path)
    with pytest.raises(ValueError, match="cannot be empty"):
        prompt_assistant(" ", root=tmp_path)
    with pytest.raises(ValueError, match="64 KiB"):
        prompt_assistant("x" * (64 * 1024 + 1), root=tmp_path)
    with pytest.raises(ValueError, match="system prompt"):
        prompt_assistant("hello", root=tmp_path, system_prompt="x" * (64 * 1024 + 1))
    with pytest.raises(ValueError, match="max tokens"):
        prompt_assistant("hello", root=tmp_path, max_tokens=9000)
    with pytest.raises(ValueError, match="timeouts"):
        prompt_assistant("hello", root=tmp_path, timeout=0)


@pytest.mark.parametrize("payload,match", [
    ([], "non-object"),
    ({}, "completion choice"),
    ({"choices": [{}]}, "empty response"),
])
def test_prompt_rejects_invalid_api_responses(
    tmp_path: Path, payload: object, match: str,
) -> None:
    write_config(tmp_path)
    with pytest.raises(ValueError, match=match):
        prompt_assistant(
            "hello", root=tmp_path,
            opener=lambda *_a, **_k: Response(json.dumps(payload).encode()),
        )


def test_local_server_reuses_custom_qwen_then_stops_cleanly(tmp_path: Path) -> None:
    runtime, model = local_files(tmp_path)
    write_config(
        tmp_path, mode="custom_local", runtime_path=str(runtime),
        model_path=str(model), model_name="Qwen custom", capabilities=None,
    )
    processes = []
    starts = []

    def popen(command, **options):
        starts.append((command, options))
        process = FakeProcess()
        processes.append(process)
        return process

    calls = []
    bodies = []
    authorizations = []

    def opener(request, timeout):
        calls.append(request.full_url)
        authorizations.append(request.headers.get("Authorization"))
        if request.full_url.endswith("/health"):
            return Response(b'{"status":"ok"}')
        bodies.append(json.loads(request.data))
        return Response(json.dumps({
            "choices": [{"message": {"content": [
                {"type": "text", "text": advisory_content("Local Qwen answer")},
            ]}}],
        }).encode())

    server = LocalAssistantServer(popen)
    progress = []
    first = prompt_assistant(
        "one", root=tmp_path, opener=opener, server=server, progress=progress.append,
    )
    second = prompt_assistant("two", root=tmp_path, opener=opener, server=server)
    assert first.advisory["summary"] == second.advisory["summary"] == "Local Qwen answer"
    assert progress == [
        "building grounding", "starting runtime", "prefill", "generating", "complete",
    ]
    assert "Local Qwen answer" in first.text and "Local Qwen answer" in second.text
    assert len(starts) == 1
    assert starts[0][0][0] == str(runtime.resolve())
    assert starts[0][0][1:3] == ["--model", str(model.resolve())]
    assert len([url for url in calls if url.endswith("/health")]) == 1
    assert all(
        body["chat_template_kwargs"] == {"enable_thinking": False}
        for body in bodies
    )
    assert all(body["cache_prompt"] is True for body in bodies)
    assert all(value and value.startswith("Bearer ") for value in authorizations)
    key_path = Path(starts[0][0][starts[0][0].index("--api-key-file") + 1])
    assert "--no-ui" in starts[0][0]
    assert server.running
    assert key_path.exists()
    assert server.stop()
    assert all(process.terminated for process in processes)
    assert not server.running and not key_path.exists()
    assert not server.stop()


def test_local_server_reports_start_failure_exit_timeout_and_forced_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, model = local_files(tmp_path)
    spec = assistant_client.LocalRuntimeSpec(runtime, model, "qwen", 4096)
    failed = LocalAssistantServer(
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("blocked")),
    )
    with pytest.raises(ValueError, match="Could not start"):
        failed.ensure(spec, tmp_path, startup_timeout=1)

    exited = LocalAssistantServer(lambda *_a, **_k: FakeProcess(exit_code=7))
    with pytest.raises(ValueError, match="exited during startup"):
        exited.ensure(
            spec, tmp_path, startup_timeout=1,
            opener=lambda *_a, **_k: (_ for _ in ()).throw(OSError()),
        )

    waiting = FakeProcess(wait_timeout=True)
    server = LocalAssistantServer(lambda *_a, **_k: waiting)
    server.ensure(
        spec, tmp_path, startup_timeout=1,
        opener=lambda *_a, **_k: Response(b"{}"),
    )
    assert server.stop()
    assert waiting.killed


def test_console_cli_and_agent_api_expose_read_only_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = PromptResult("Qwen says hello", "qwen-test", "compatible_api", 0.125)
    monkeypatch.setattr(assistant_client, "prompt_assistant", lambda *_a, **_k: result)
    monkeypatch.setattr(
        assistant_client, "review_assistant",
        lambda *_a, **_k: assistant_client.ReviewResult(
            symbols=("one",), priorities=("callers",),
            source_discovery={"sources": []}, chunks=(),
            advisory={
                "summary": "Review complete", "findings": [],
                "recommended_operations": [], "proposed_changes": [],
                "missing_context": [], "abstentions": [],
            },
        ),
    )
    monkeypatch.setattr(
        assistant_client, "assistant_status",
        lambda _root=None: {"enabled": True, "mode": "compatible_api"},
    )
    console_commands = {item.name for item in console_catalog()}
    assert "assistant" in console_commands
    suggestion = suggestions_for("assistant pr", cwd=tmp_path)
    assert suggestion[0].replacement == "assistant prompt "
    review_suggestion = suggestions_for("assistant re", cwd=tmp_path)
    assert review_suggestion[0].replacement == "assistant review "
    console_result = execute_console_command("assistant prompt hello from console")
    assert console_result.exit_code == 0 and "Qwen says hello" in console_result.output

    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["assistant"]["risk"] == "read_only"
    api = execute_request({
        "id": "qwen", "action": "execute", "command": "assistant",
        "args": ["prompt", "hello", "from", "api", "--json-output"],
    }, audit_path=tmp_path / "audit.jsonl")
    assert api["ok"] is True and "Qwen says hello" in api["result"]["output"]
    review_api = execute_request({
        "id": "qwen-review", "action": "execute", "command": "assistant",
        "args": [
            "review", "--symbols", "one", "--repository-root", str(tmp_path),
            "--no-progress",
        ],
    }, audit_path=tmp_path / "audit.jsonl")
    assert review_api["ok"] is True
    assert "Review complete" in review_api["result"]["output"]

    status = CliRunner().invoke(cli.main, ["assistant", "status"])
    assert status.exit_code == 0 and "compatible_api" in status.output

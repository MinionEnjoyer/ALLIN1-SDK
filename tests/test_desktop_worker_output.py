import io
import json
import sys
import threading

import pytest

from allin1_sdk import desktop_protocol


@pytest.fixture
def owned_services():
    services = []
    yield services
    for service in services:
        with service._lock:
            active = service._job.job_id if service._job else None
        if active is not None:
            try:
                service._cancel_active_job(
                    request_id="test-cleanup", requested_job_id=active,
                    status="Test cleanup",
                )
            except desktop_protocol.ProtocolError:
                pass


def _request(operation, payload, request_id):
    return desktop_protocol.envelope(
        operation, payload, request_id=request_id, terminal=False,
    )


def _handshake(service):
    reply = service.handle(_request("handshake", {
        "client": {"name": "worker-output-test", "version": "1.0"},
        "supported_versions": [desktop_protocol.PROTOCOL_VERSION],
    }, "handshake"))[0]
    assert reply["operation"] == "result"


def _start_read_only_job(service, job_id):
    return service.handle(_request("start_job", {
        "job_id": job_id,
        "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
    }, f"start-{job_id}"))[0]


def test_job_worker_serialization_falls_back_before_writing_oversized_json(monkeypatch):
    monkeypatch.setattr(desktop_protocol, "MAX_JOB_WORKER_STDOUT_BYTES", 256)
    monkeypatch.setattr(
        desktop_protocol, "dispatch_operation",
        lambda *_args: ("read_only", {"large": "x" * 8_192}),
    )
    source = io.StringIO(json.dumps({
        "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
        "allow_game_writes": False,
    }) + "\n")
    destination = io.StringIO()

    assert desktop_protocol.run_job_worker(source, destination) == 1
    serialized = destination.getvalue()
    assert len(serialized.encode("utf-8")) <= 256
    response = json.loads(serialized)
    assert response["ok"] is False
    assert "bounded output limit" in response["error"]


def test_job_worker_rejects_total_node_budget_before_serializing(monkeypatch):
    monkeypatch.setattr(desktop_protocol, "_WORKER_JSON_NODE_BUDGET", 4)
    monkeypatch.setattr(
        desktop_protocol, "dispatch_operation",
        lambda *_args: ("read_only", {"values": [1, 2, 3, 4]}),
    )
    source = io.StringIO(json.dumps({
        "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
        "allow_game_writes": False,
    }) + "\n")
    destination = io.StringIO()

    assert desktop_protocol.run_job_worker(source, destination) == 1
    response = json.loads(destination.getvalue())
    assert response["ok"] is False
    assert "node budget" in response["error"]


def test_job_worker_rejects_huge_integer_before_json_encoding(monkeypatch):
    monkeypatch.setattr(
        desktop_protocol, "dispatch_operation", lambda *_args: ("read_only", 1 << 4_097),
    )
    source = io.StringIO(json.dumps({
        "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
        "allow_game_writes": False,
    }) + "\n")
    destination = io.StringIO()

    assert desktop_protocol.run_job_worker(source, destination) == 1
    assert "integer exceeds size budget" in json.loads(destination.getvalue())["error"]


@pytest.mark.parametrize("value", ["\ud800", None], ids=["invalid-unicode", "circular"])
def test_job_worker_safely_reports_unserializable_results(monkeypatch, value):
    if value is None:
        value = []
        value.append(value)
    monkeypatch.setattr(
        desktop_protocol, "dispatch_operation", lambda *_args: ("read_only", value),
    )
    source = io.StringIO(json.dumps({
        "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
        "allow_game_writes": False,
    }) + "\n")
    destination = io.StringIO()

    code = desktop_protocol.run_job_worker(source, destination)
    response = json.loads(destination.getvalue())
    if code:
        assert "bounded output limit" in response["error"]
    else:
        assert response["result"] == ["[circular reference]"]


def test_owned_worker_stdout_limit_cancels_and_service_remains_usable(
    monkeypatch, owned_services,
):
    monkeypatch.setattr(desktop_protocol, "MAX_JOB_WORKER_STDOUT_BYTES", 256)
    monkeypatch.setattr(desktop_protocol, "MAX_JOB_WORKER_STDERR_BYTES", 256)
    monkeypatch.setattr(desktop_protocol, "_worker_command", lambda: [
        sys.executable, "-c",
        # Deliberately never reads stdin: the feed must not block monitor
        # cancellation after its overflowing output arrives.
        "import sys,time; "
        "sys.stdout.write('x'*4096); sys.stdout.flush(); time.sleep(30)",
    ])
    events = []
    completed = threading.Event()

    def emitted(event):
        events.append(event)
        if event["terminal"]:
            completed.set()

    service = desktop_protocol.DesktopProtocolService(emit=emitted)
    owned_services.append(service)
    _handshake(service)
    assert _start_read_only_job(service, "large-stdout")["operation"] == "job_event"
    assert completed.wait(10)
    assert events[-1]["operation"] == "error"
    assert "stdout" in events[-1]["payload"]["message"]
    assert service._job is None

    # A fresh owned child can still complete after the oversized child is
    # drained/cancelled, proving the sidecar did not remain busy.
    completed.clear()
    monkeypatch.setattr(desktop_protocol, "_worker_command", lambda: [
        sys.executable, "-c",
        "import sys; sys.stdin.readline(); "
        "print('{\"ok\":true,\"risk\":\"read_only\",\"result\":{\"value\":1}}')",
    ])
    assert _start_read_only_job(service, "small-stdout")["operation"] == "job_event"
    assert completed.wait(10)
    assert events[-1]["operation"] == "result"
    assert events[-1]["payload"]["result"] == {"value": 1}
    assert service._job is None


def test_owned_worker_stderr_is_tail_truncated_without_cancelling_result(
    monkeypatch, owned_services,
):
    monkeypatch.setattr(desktop_protocol, "MAX_JOB_WORKER_STDOUT_BYTES", 256)
    monkeypatch.setattr(desktop_protocol, "MAX_JOB_WORKER_STDERR_BYTES", 32)
    monkeypatch.setattr(desktop_protocol, "_worker_command", lambda: [
        sys.executable, "-c",
        "import sys; sys.stdin.readline(); "
        "sys.stderr.write('x'*4096); sys.stderr.flush(); "
        "print('{\"ok\":true,\"risk\":\"read_only\",\"result\":{\"value\":1}}')",
    ])
    events = []
    completed = threading.Event()

    def emitted(event):
        events.append(event)
        if event["terminal"]:
            completed.set()

    service = desktop_protocol.DesktopProtocolService(emit=emitted)
    owned_services.append(service)
    _handshake(service)
    assert _start_read_only_job(service, "large-stderr")["operation"] == "job_event"
    assert completed.wait(10)
    assert events[-1]["operation"] == "result"
    assert events[-1]["payload"]["result"] == {"value": 1}
    assert events[-1]["payload"]["diagnostics"].startswith("[stderr truncated]")
    assert service._job is None

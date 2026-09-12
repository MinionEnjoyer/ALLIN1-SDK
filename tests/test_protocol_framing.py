"""Malformed local clients must not exhaust or desynchronize the SDK service."""
import io
import json
import sys

import pytest

from allin1_sdk import agent_api, desktop_protocol


def desktop_ping():
    return desktop_protocol.envelope(
        "handshake", {"client": {"name": "test", "version": "1"},
                      "supported_versions": [desktop_protocol.PROTOCOL_VERSION]},
        request_id="healthy", terminal=False,
    )


@pytest.mark.parametrize("transport", ["agent", "desktop"])
@pytest.mark.parametrize("bad", [
    '{"action":"ping","action":"ping"}',
    '{"action":"ping","id":NaN}',
    '{"action":"ping","id":1e999}',
    '{"action":"ping","id":"\\ud800"}',
    "[" * 2000 + "0" + "]" * 2000,
    '{"id":' + "9" * 5000 + '}',
], ids=["duplicate", "nan", "infinity", "surrogate", "deep", "integer"])
def test_bad_json_frame_is_rejected_and_next_request_survives(transport, bad):
    module = agent_api if transport == "agent" else desktop_protocol
    good = {"action": "ping", "id": "healthy"} if transport == "agent" else desktop_ping()
    destination = io.StringIO()
    module.serve_stdio(io.StringIO(bad + "\n" + json.dumps(good) + "\n"), destination)
    rows = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert len(rows) == 2
    assert "invalid JSON" in str(rows[0])
    if transport == "agent":
        assert rows[0]["ok"] is False and rows[1]["ok"] is True
        assert rows[1]["id"] == "healthy"
    else:
        assert rows[0]["operation"] == "error"
        assert rows[1]["operation"] == "result"
        assert rows[1]["request_id"] == "healthy"


class BoundedReads(io.StringIO):
    def __iter__(self):
        raise AssertionError("Unbounded line iteration")

    def readline(self, size=-1):
        assert 0 < size <= agent_api.MAX_REQUEST_BYTES + 1
        return super().readline(size)


@pytest.mark.parametrize("transport", ["agent", "desktop"])
def test_oversized_frame_is_drained_with_bounded_reads(transport):
    module = agent_api if transport == "agent" else desktop_protocol
    good = {"action": "ping"} if transport == "agent" else desktop_ping()
    source = BoundedReads("x" * (agent_api.MAX_REQUEST_BYTES * 3) + "\n" + json.dumps(good) + "\n")
    destination = io.StringIO()
    module.serve_stdio(source, destination)
    rows = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert len(rows) == 2
    assert "size limit" in str(rows[0])
    assert rows[1].get("ok") is True or rows[1].get("operation") == "result"


def test_worker_rejects_deep_or_duplicate_requests_without_dispatch(monkeypatch):
    monkeypatch.setattr(desktop_protocol, "dispatch_operation", lambda *_a, **_k: pytest.fail("Dispatched malformed frame"))
    for raw in ("[" * 2000 + "0" + "]" * 2000, '{"operation":"execute","operation":"execute"}'):
        output = io.StringIO()
        assert desktop_protocol.run_job_worker(BoundedReads(raw + "\n"), output) == 1
        assert json.loads(output.getvalue())["ok"] is False


@pytest.mark.parametrize("broken_output", [False, True], ids=["eof", "broken-pipe"])
def test_closed_transport_reaps_its_owned_read_only_worker(monkeypatch, broken_output):
    workers = []
    original_start = desktop_protocol.DesktopProtocolService._start_job

    def tracked_start(service, *args, **kwargs):
        response = original_start(service, *args, **kwargs)
        workers.append(service._job.process)
        return response

    monkeypatch.setattr(desktop_protocol.DesktopProtocolService, "_start_job", tracked_start)
    monkeypatch.setattr(desktop_protocol, "_worker_command", lambda: [
        sys.executable, "-c", "import sys,time; sys.stdin.readline(); time.sleep(30)",
    ])

    class Destination(io.StringIO):
        def write(self, value):
            if broken_output and '"operation":"job_event"' in value:
                raise BrokenPipeError("test client closed")
            return super().write(value)

    job = desktop_protocol.envelope("start_job", {
        "job_id": "owned-eof-test", "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
    }, request_id="start-owned", terminal=False)
    source = io.StringIO(json.dumps(desktop_ping()) + "\n" + json.dumps(job) + "\n")
    try:
        if broken_output:
            with pytest.raises(BrokenPipeError, match="test client closed"):
                desktop_protocol.serve_stdio(source, Destination())
        else:
            desktop_protocol.serve_stdio(source, Destination())
        assert len(workers) == 1
        assert workers[0].poll() is not None
    finally:
        # Only the child created by this test, even if the regression fails.
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_frame_byte_limits_and_valid_unterminated_unicode():
    from allin1_sdk.jsonl_protocol import FrameError, load_request, read_frame
    assert read_frame(io.StringIO('"é"'), 4) == '"é"'
    with pytest.raises(FrameError, match="size limit"):
        read_frame(io.StringIO('"é"\n'), 4)
    with pytest.raises(FrameError, match="invalid Unicode"):
        read_frame(io.StringIO('"\ud800"'), 16)
    assert load_request('{"value":' + '9' * 128 + '}')["value"] == int('9' * 128)
    assert load_request('[' * 64 + '0' + ']' * 64) is not None
    with pytest.raises(FrameError, match="depth limit"):
        load_request('[' * 65 + '0' + ']' * 65)


def test_monitor_start_failure_reaps_owned_child_and_clears_busy_state(monkeypatch):
    workers = []
    original_popen = desktop_protocol.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        if kwargs.get("stdin") == desktop_protocol.subprocess.PIPE:
            workers.append(process)
        return process

    def fail_start(_thread):
        raise RuntimeError("test monitor exhaustion")

    monkeypatch.setattr(desktop_protocol.subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(desktop_protocol.threading.Thread, "start", fail_start)
    monkeypatch.setattr(desktop_protocol, "_worker_command", lambda: [
        sys.executable, "-c", "import sys; sys.stdin.readline()",
    ])
    service = desktop_protocol.DesktopProtocolService()
    service.handle(desktop_ping())
    request = desktop_protocol.envelope("start_job", {
        "operation": "execute", "payload": {"command": "list-axle-prefabs", "args": []},
    }, request_id="monitor-test", terminal=False)
    try:
        reply = service.handle(request)[0]
        assert reply["operation"] == "error"
        assert "test monitor exhaustion" in str(reply)
        assert service._job is None
        assert len(workers) == 1 and workers[0].poll() is not None
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

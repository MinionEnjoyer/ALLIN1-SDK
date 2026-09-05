"""The React service must retain its catalog when Tcl/Tk is absent."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_sdk_stdio_catalog_without_tkinter(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = r'''
import importlib.abc, runpy, socket, sys
attempts = []
class NoTk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'tkinter', '_tkinter'}:
            attempts.append(fullname)
            raise ImportError('Tkinter deliberately unavailable: ' + fullname)
sys.meta_path.insert(0, NoTk())
def no_network(*args, **kwargs):
    raise AssertionError('Offline catalog attempted network access')
socket.socket.connect = no_network
socket.create_connection = no_network
runpy.run_module('allin1_sdk.desktop_sidecar_host', run_name='__main__')
assert not attempts, attempts
'''
    def request(operation, payload=None):
        return {"protocol_version": "1.0.0", "request_id": operation, "operation": operation,
                "payload": payload or {}, "job_id": None, "sequence": 0,
                "risk": "none", "terminal": False}
    requests = [request("handshake", {"client": {"name": "no-tk-test", "version": "1"},
        "supported_versions": ["1.0.0"]}), request("catalog"), request("shutdown")]
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONIOENCODING="utf-8")
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        env[key] = str(tmp_path / "User state with spaces" / key)
    for key in ("ALLIN1_GTA_PATH", "ALLIN1_SDK_HOME"):
        env.pop(key, None)
    canary = tmp_path / "outside.canary"
    canary.write_bytes(b"unchanged")
    result = subprocess.run([sys.executable, "-u", "-c", code],
        input="".join(json.dumps(item) + "\n" for item in requests), capture_output=True,
        text=True, encoding="utf-8", env=env, cwd=tmp_path, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 3, responses
    assert all(item["operation"] == "result" for item in responses), responses
    from allin1_sdk.help_topics import HELP_TOPICS, search_help_topics
    topics = responses[1]["payload"]["help_topics"]
    assert {item["key"] for item in topics} == {item.key for item in HELP_TOPICS}
    assert len(topics) > 10
    assert search_help_topics("console")
    assert canary.read_bytes() == b"unchanged"

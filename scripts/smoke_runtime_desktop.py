"""Build real native candidates through the desktop protocol in a disposable root."""
import json
from pathlib import Path
import tempfile

from allin1_sdk.desktop_protocol import dispatch_operation


def main():
    with tempfile.TemporaryDirectory(prefix="allin1-desktop-runtime-") as temporary:
        _, preflight = dispatch_operation("inspect_authoring_workspace", {"module": "runtime"})
        if not preflight["toolchain"]["ready"]:
            raise RuntimeError("; ".join(preflight["toolchain"]["problems"]))
        print("Native toolchain preflight passed", flush=True)
        request = {"module": "runtime", "action": "build", "targets": ["story-legacy", "story-enhanced"],
                   "destination": str(Path(temporary) / "Candidate with spaces"), "build_id": "sdk-0.6.4-react-fixture",
                   "expected_state_sha256": preflight["state_sha256"]}
        _, review = dispatch_operation("review_workspace_action", request)
        print("Reviewed both targets; building and running native tests", flush=True)
        _, result = dispatch_operation("apply_workspace_action", {**request, "authoring_confirmed": True, "review_sha256": review["review_sha256"]})
        assert result["runtime_build"]["candidate_status"]["game_acceptance"] == "not-tested"
        assert result["runtime_build"]["candidate_status"]["supported"] is False
        assert result["runtime_build"]["built_targets"] == ["story-legacy", "story-enhanced"]
        assert result["game_write_performed"] is False
        print(json.dumps({"status": "PASS", "targets": result["runtime_build"]["built_targets"],
                          "checksums": result["runtime_build"]["checksums"],
                          "commands": [{"name": command["name"], "returncode": command["returncode"]} for command in result["runtime_build"]["commands"]],
                          "live_acceptance": "NOT TESTED", "game_launched": False, "real_installation_modified": False}, indent=2))


if __name__ == "__main__":
    main()

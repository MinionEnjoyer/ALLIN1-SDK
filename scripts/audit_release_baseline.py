"""Reproduce the reviewed updater baseline only inside disposable directories.

No real executable is launched. The old cleanup is exercised only after its
resolved destination has been checked against this script's fresh temp root.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace

BASELINE = "bfc4e010126efe3a549adb96cbe9a4c855c80db3"


def main():
    root = Path(__file__).resolve().parents[1]
    source = subprocess.check_output(["git", "-C", str(root), "show", f"{BASELINE}:src/allin1_sdk/updater_host.py"], text=True)
    old = ModuleType("isolated_updater_baseline")
    exec(compile(source, "reviewed-baseline/updater_host.py", "exec"), old.__dict__)
    old.subprocess = SimpleNamespace(Popen=lambda *args, **kwargs: None)
    results = {"baseline_commit": BASELINE, "scope": "disposable directories; relaunch stubbed"}
    with tempfile.TemporaryDirectory(prefix="sdk-baseline-canaries-") as directory:
        temporary = Path(directory).resolve()
        install = temporary / "SDK"; install.mkdir()
        (install / "user-data.txt").write_bytes(b"user data canary")
        stage = temporary / "SDK.updating-fixture"; stage.mkdir()
        (stage / "ALLIN1-SDK-Desktop.exe").write_bytes(b"not a real PE, never launched")
        outside = temporary / "outside-destination"; outside.mkdir()
        canary = outside / "canary.txt"; canary.write_bytes(b"outside canary")
        backup = temporary / "SDK.previous"
        if os.name == "nt":
            quote = lambda p: "'" + str(p).replace("'", "''") + "'"
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"New-Item -ItemType Junction -Path {quote(backup)} -Target {quote(outside)} | Out-Null"],
                check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            backup.symlink_to(outside, target_is_directory=True)
        # Validate the *actual resolved* target before invoking old destructive code.
        assert backup.resolve() == outside and outside.is_relative_to(temporary)
        try:
            old.apply_staged_update(install, stage, "ALLIN1-SDK-Desktop.exe")
            results["unverified_stage_accepted"] = True
        except OSError as error:
            # Windows may leave the dangling junction in place after the old
            # cleanup, then refuse the rename. The canary result still proves it.
            results["swap_error"] = str(error)
        results["outside_destination_canary_deleted"] = not canary.exists()
        # Independently reproduce acceptance/user-data loss without a junction.
        plain = temporary / "Plain SDK"; plain.mkdir()
        (plain / "user-data.txt").write_bytes(b"user data canary")
        pending = temporary / "Plain SDK.updating-fixture"; pending.mkdir()
        (pending / "ALLIN1-SDK-Desktop.exe").write_bytes(b"unverified fixture")
        old.apply_staged_update(plain, pending, "ALLIN1-SDK-Desktop.exe")
        results["unverified_stage_accepted"] = (plain / "ALLIN1-SDK-Desktop.exe").read_bytes() == b"unverified fixture"
        results["install_local_user_data_deleted"] = not (plain / "user-data.txt").exists() and not (temporary / "Plain SDK.previous").exists()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

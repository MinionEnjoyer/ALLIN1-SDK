import json
import pytest
from allin1_sdk import workspace_desktop as desktop


@pytest.mark.parametrize("task", ["meta_diff", "meta_roundtrip", "vehicle_data", "dlc_inventory"])
def test_data_tools_inspect_review_export_real_services(tmp_path, task):
    source = tmp_path / "source"; source.mkdir()
    before = source / "before.meta"; before.write_text('<root><item value="1" /></root>')
    after = source / "after.meta"; after.write_text('<root><item value="2" /></root>')
    if task == "dlc_inventory":
        (source / "GTA5.exe").write_bytes(b"disposable fixture")
    payload = {"module": "data_tools", "task": task, "source": str(source if task in {"vehicle_data", "dlc_inventory"} else before)}
    if task == "meta_diff": payload["comparison"] = str(after)
    session = desktop.inspect(payload)
    assert session["read_only"] and not session["game_write_performed"]
    if task == "meta_diff": assert session["document"]["change_count"] == 1
    if task == "meta_roundtrip": assert session["document"]["semantically_equivalent"]
    destination = tmp_path / "reports with spaces"
    request = {**payload, "action": "export", "expected_state_sha256": session["state_sha256"], "destination": str(destination)}
    review = desktop.review(request)
    assert not destination.exists()
    assert desktop.review(request)["review_sha256"] == review["review_sha256"]
    with pytest.raises(ValueError, match="confirmation"): desktop.apply(request)
    result = desktop.apply({**request, "authoring_confirmed": True, "review_sha256": review["review_sha256"]})
    assert set(result["outputs"]) == set(session["outputs"])
    assert set(p.name for p in destination.iterdir()) == set(session["outputs"])
    assert 'value="1"' in before.read_text()
    with pytest.raises(ValueError, match="new destination"):
        desktop.review(request)


def test_changed_input_cannot_export_a_previously_confirmed_report(tmp_path):
    source = tmp_path / "source.xml"; source.write_text("<root/>")
    request = {"module": "data_tools", "task": "meta_roundtrip", "source": str(source)}
    session = desktop.inspect(request)
    request.update(action="export", expected_state_sha256=session["state_sha256"], destination=str(tmp_path / "reports"))
    review = desktop.review(request)
    source.write_text("<changed/>")
    with pytest.raises(ValueError, match="changed"):
        desktop.apply({**request, "authoring_confirmed": True, "review_sha256": review["review_sha256"]})
    assert not (tmp_path / "reports").exists()

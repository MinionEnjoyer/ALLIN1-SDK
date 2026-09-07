"""Offline receipt consistency. Content hashes are not authenticity signatures."""
from allin1_sdk.artifact_contract import digest, inventory, validate_manifest
from allin1_sdk.diagnostic_asset_evidence import summarize


def validate_receipt(receipt, state):
    if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int
            or receipt["schema_version"] != 1 or receipt.get("kind") != "optimization_package"
            or receipt.get("runtime_status") != "not_tested"):
        raise ValueError("Unsupported optimization recovery receipt")
    changes = receipt.get("changes")
    if not isinstance(changes, list) or len(changes) > 8 or any(not isinstance(c, dict) for c in changes):
        raise ValueError("Invalid recovery change report")
    original = {k:v for k,v in receipt.items() if k not in {"schema_version", "kind"}}
    if receipt.get("state_sha256") != state(original):
        raise ValueError("Recovery receipt state identity mismatch")
    artifact = validate_manifest(receipt.get("artifact_manifest"))
    for field, side in (("before_inventory", "inputs"), ("after_inventory", "outputs")):
        inventory(receipt.get(field))
        if receipt[field] != artifact[side]:
            raise ValueError("Recovery inventory contradicts the artifact")
    if receipt.get("edition") != artifact["edition"]:
        raise ValueError("Recovery edition contradicts the artifact")
    authored = [{k:v for k,v in c.items() if not k.startswith("preview_")} for c in changes]
    if digest(authored) != artifact["changes_sha256"]:
        raise ValueError("Recovery changes contradict the artifact")
    reports = []
    for field, side in (("before_report", "inputs"), ("after_report", "outputs")):
        report = receipt.get(field)
        evidence = summarize(report, artifact, "0" * 64)
        if evidence["status"] != "recorded" or evidence["source_sha256"] != digest(artifact[side]):
            raise ValueError("Recovery validation report contradicts the artifact inventory")
        reports.append(report)
    if artifact["validation_reports"] != [r["report_sha256"] for r in reports]:
        raise ValueError("Recovery validation report identities disagree")
    if reports[0]["validator_sha256"] != reports[1]["validator_sha256"]:
        raise ValueError("Recovery before/after validators disagree")
    contexts = [r.get("source_identity") for r in reports]
    if any(not isinstance(c, dict) for c in contexts) or contexts[0].get("comparison_sha256") != contexts[1].get("comparison_sha256"):
        raise ValueError("Recovery before/after comparison contexts disagree")
    return receipt

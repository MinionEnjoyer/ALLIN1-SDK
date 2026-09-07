"""Link selected static reports to artifact evidence without inferring causality."""
import re

from allin1_sdk.artifact_contract import digest, sha, verify_seal
from allin1_sdk.asset_validation import CATEGORIES


def selected_report(document):
    """Accept a standalone report or the candidate report in an existing export.

    Only the nested report is attributed by its sealed identity. This does not
    certify the surrounding optimization receipt or its recovery files.
    """
    if isinstance(document, dict) and document.get("kind") == "optimization_package":
        if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
            raise ValueError("Unsupported optimization receipt")
        return document.get("after_report")
    return document


def summarize(report, artifact, file_sha256):
    verify_seal(report, "report_sha256")
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or report.get("read_only") is not True or report.get("runtime_status") != "not_tested"):
        raise ValueError("Choose a supported static asset-validation report")
    for key in ("source_sha256", "validator_sha256", "report_sha256"):
        sha(report.get(key))
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CATEGORIES):
        raise ValueError("Static report must contain all validation categories")
    ranks = {"pass":0, "warning":1, "not_checked":2, "fail":3}
    summaries = []; seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Invalid static category evidence")
        category = check.get("category"); status = check.get("status")
        if not isinstance(category, str) or category not in CATEGORIES or category in seen or not isinstance(status, str) or status not in ranks:
            raise ValueError("Invalid or duplicate static category")
        seen.add(category)
        findings = check.get("findings"); total = check.get("finding_count")
        if (not isinstance(findings, list) or len(findings) > 40 or type(total) is not int or not len(findings) <= total <= 1000000
                or type(check.get("truncated")) is not bool or check["truncated"] != (total > len(findings))):
            raise ValueError("Invalid or contradictory static finding counts")
        codes = set(); maximum = 0
        for finding in findings:
            if (not isinstance(finding, dict) or not isinstance(finding.get("status"), str) or finding["status"] not in ranks
                    or not isinstance(finding.get("code"), str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", finding["code"])):
                raise ValueError("Invalid static finding evidence")
            maximum = max(maximum, ranks[finding["status"]]); codes.add(finding["code"])
        if maximum > ranks[status] or (not check["truncated"] and maximum != ranks[status]):
            raise ValueError("Static category status contradicts its findings")
        summaries.append({"category":category, "status":status, "finding_count":total,
                          "shown_findings":len(findings), "truncated":check["truncated"], "codes":sorted(codes)})
    maximum = max(ranks[row["status"]] for row in summaries)
    expected = {0:"pass", 1:"warning", 2:"incomplete", 3:"fail"}[maximum]
    if report.get("static_status") != expected:
        raise ValueError("Static report status contradicts its category evidence")
    recorded = report["report_sha256"] in artifact["validation_reports"]
    matches = [name for name in ("inputs", "outputs") if digest(artifact[name]) == report["source_sha256"]]
    relation = "+".join(matches) + "_inventory" if recorded and matches else "not_established"
    # Deliberately omit free-text messages, source paths and locations. They can
    # contain private paths; this portable summary is linked to the original hash.
    return {"status":"recorded" if recorded else "not_recorded", "file_sha256":file_sha256,
            "report_sha256":report["report_sha256"], "source_sha256":report["source_sha256"],
            "validator_sha256":report["validator_sha256"], "static_status":expected,
            "source_relation":relation, "checks":summaries, "runtime_status":"not_tested",
            "scope":"The artifact records this report only when its exact report hash matches. Input/before reports are not candidate/output proof. Static findings, including failures, do not establish a crash cause. Paths and free-text findings are omitted from this summary."}

"""User-selected log excerpts with previewable redaction; no upload or discovery."""
import hashlib
import json
import re

from allin1_sdk.artifact_contract import digest
from allin1_sdk.workspace_desktop import path
from allin1_sdk.implementation_identity import identify

MAX_FILE=2*1024**2
SENSITIVE=re.compile(r"(?i)(?:password|passwd|api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|authorization|cookie|secret|credential|bearer\s|-----BEGIN.*PRIVATE KEY)")


def _bytes(file):
    with file.open("rb") as stream: data=stream.read(MAX_FILE+1)
    if len(data)>MAX_FILE: raise ValueError("Selected diagnostic log exceeds 2 MiB; choose a bounded text snapshot")
    return data


def _redact(text, game, terms):
    if SENSITIVE.search(text): return "<redacted sensitive line>"
    for term in terms:
        text=re.sub(re.escape(term),"<redacted user term>",text,flags=re.I)
    # Preserve game-relative diagnostic paths while withholding private roots.
    for prefix in {str(game),str(game).replace("\\","/"),str(game).replace("/","\\")}:
        text=re.sub(re.escape(prefix),"<game>",text,flags=re.I)
    text=re.sub(r"(?i)\b(?:https?|file)://[^\s\"'<>]+","<redacted URL>",text)
    text=re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b","<redacted email>",text)
    text=re.sub(r"(?i)\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b","<redacted token>",text)
    # Broad path patterns intentionally favor omission over preserving nearby
    # words. Free-form prose is not guaranteed anonymous: preview is mandatory.
    text=re.sub(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n\"'<>|;]*","<redacted absolute path>",text)
    text=re.sub(r"(?<![\w:<])/(?:home|Users|mnt|media|Volumes|tmp|var)/[^\r\n\"'<>|;]*","<redacted absolute path>",text)
    text=re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b","<redacted IP>",text)
    return text


def inspect(settings, game):
    if not isinstance(settings,dict) or set(settings)-{"logs","redact_terms"}:
        raise ValueError("Unsupported diagnostic bundle settings")
    logs=settings.get("logs",[]);terms=settings.get("redact_terms",[])
    if not isinstance(logs,list) or len(logs)>8 or not isinstance(terms,list) or len(terms)>16 or any(not isinstance(term,str) or not 1<=len(term)<=128 or "\x00" in term for term in terms):
        raise ValueError("Select at most eight logs and sixteen bounded private terms")
    result=[];seen=set();line_budget=512;byte_budget=8*1024**2;text_budget=96*1024
    for index,item in enumerate(logs):
        if not isinstance(item,dict) or set(item)!={"source","start_line","line_count"} or any(type(item[key]) is not int for key in ("start_line","line_count")) or item["start_line"]<1 or not 1<=item["line_count"]<=512:
            raise ValueError("Choose a log file and a positive one-based line range")
        line_budget-=item["line_count"]
        if line_budget<0: raise ValueError("Selected excerpts exceed 512 total lines")
        file=path(item["source"])
        if not file.is_file() or file.suffix.lower() not in {".log",".txt"}:
            raise ValueError("Select a .log or .txt text snapshot, not an asset, dump or archive")
        if str(file).casefold() in seen: raise ValueError("A diagnostic log was selected more than once")
        seen.add(str(file).casefold())
        data=_bytes(file);byte_budget-=len(data)
        if byte_budget<0: raise ValueError("Selected logs exceed the 8 MiB input budget")
        try: text=data.decode("utf-16" if data.startswith((b"\xff\xfe",b"\xfe\xff")) else "utf-8-sig")
        except UnicodeError as exc: raise ValueError("Diagnostic logs must be UTF-8 or BOM-marked UTF-16 text") from exc
        if "\x00" in text or any(ord(c)<32 and c not in "\r\n\t" for c in text):
            raise ValueError("Diagnostic log contains binary/control data")
        original_lines=text.splitlines()
        if item["start_line"]>len(original_lines)+1: raise ValueError("Selected start line is beyond the log")
        lines=[];redacted=0;clipped=0;in_private_key=False
        for number,line in enumerate(original_lines,1):
            if re.search(r"-----BEGIN.*PRIVATE KEY",line): in_private_key=True
            sanitized="<redacted private key line>" if in_private_key else _redact(line,game,terms)
            if re.search(r"-----END.*PRIVATE KEY",line): in_private_key=False
            if number<item["start_line"] or number>=item["start_line"]+item["line_count"]: continue
            if sanitized!=line: redacted+=1
            if len(sanitized)>4096:
                sanitized=sanitized[:4096]+" [line truncated]";clipped+=1
            lines.append({"line":number,"text":sanitized})
        content="".join(f"{row['line']}: {row['text']}\n" for row in lines)
        text_budget-=len(content.encode("utf-8"))
        if text_budget<0: raise ValueError("Redacted excerpts exceed 96 KiB; narrow the selected ranges")
        if hashlib.sha256(_bytes(file)).digest()!=hashlib.sha256(data).digest():
            raise ValueError("Diagnostic log changed during inspection; use a stable snapshot")
        result.append({"id":f"log-{index+1:02d}","output":f"log-{index+1:02d}.txt","source_sha256":hashlib.sha256(data).hexdigest(),
            "source_bytes":len(data),"source_lines":len(original_lines),"start_line":item["start_line"],"requested_lines":item["line_count"],
            "included_lines":len(lines),"redacted_lines":redacted,"truncated_lines":clipped,"lines":lines,
            "output_sha256":hashlib.sha256(content.encode("utf-8")).hexdigest()})
    implementation=identify([__file__])
    preview={"schema_version":1,"logs":result,"redaction_implementation":implementation,"redaction_policy_sha256":digest({"rules_sha256":implementation["sha256"],"terms":terms}),
        "association":"User-selected context, not independently session-correlated telemetry.",
        "privacy_scope":"Only these previewed, redacted excerpts are exported; source paths/names and custom private terms are omitted. Pattern redaction cannot guarantee anonymous free-form text: inspect the complete preview and add terms or narrow/remove excerpts before confirming export. No automatic upload."}
    preview["preview_sha256"]=digest(preview)
    return preview


def require_review(document,payload):
    bundle=document.get("log_bundle",{})
    if bundle.get("logs") and payload.get("privacy_review_sha256")!=bundle["preview_sha256"]:
        raise ValueError("Review and acknowledge the exact redacted log preview before export")


def outputs(document):
    logs=document.get("log_bundle",{}).get("logs",[])
    return ["diagnostic-trail.json"]+(["diagnostic-bundle.json"]+[row["output"] for row in logs] if logs else [])


def write(directory, document):
    report=(json.dumps(document,indent=2)+"\n").encode("utf-8")
    (directory/"diagnostic-trail.json").write_bytes(report)
    logs=document.get("log_bundle",{}).get("logs",[])
    if not logs:return
    inventory={"diagnostic-trail.json":hashlib.sha256(report).hexdigest()}
    for row in logs:
        content="".join(f"{line['line']}: {line['text']}\n" for line in row["lines"]).encode("utf-8")
        if hashlib.sha256(content).hexdigest()!=row["output_sha256"]: raise ValueError("Redacted excerpt changed before publication")
        (directory/row["output"]).write_bytes(content);inventory[row["output"]]=row["output_sha256"]
    manifest={"schema_version":1,"kind":"sdk_diagnostic_bundle","artifact_id":document["artifact_id"],"build_fingerprint":document["build_fingerprint"],
        "session_id":document["session_id"],"diagnostic_state_sha256":document["state_sha256"],"files":inventory,
        "scope":"Content-bound local diagnostic excerpts, not authenticated telemetry or a causal verdict."}
    manifest["bundle_sha256"]=digest(manifest)
    (directory/"diagnostic-bundle.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")

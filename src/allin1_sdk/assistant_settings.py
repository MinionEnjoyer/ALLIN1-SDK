"""Explicit workstation setup for standalone SDK users (no downloads or inference)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from allin1_sdk.assistant_client import (
    ASSISTANT_CONFIG, CAPABILITY_JSON_SCHEMA, _chat_url,
    _validate_model, _validate_runtime, sdk_assistant_root,
)


def save_standalone_assistant_settings(payload: object) -> Path:
    if not isinstance(payload, dict):
        raise ValueError("Assistant settings must be an object")
    allowed = {
        "mode", "endpoint", "model_name", "api_key_env", "runtime_path",
        "model_path", "structured_output",
    }
    if set(payload) - allowed:
        raise ValueError("Unknown assistant settings; store only the API key environment variable name, never a key")
    values: dict[str, str] = {}
    for key in allowed - {"structured_output"}:
        value = payload.get(key, "")
        if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in ("\0", "\n", "\r")):
            raise ValueError(f"Invalid assistant setting: {key}")
        values[key] = value.strip()
    mode = values["mode"]
    if mode not in {"disabled", "custom_local", "compatible_api"}:
        raise ValueError("Choose disabled, custom_local, or compatible_api for standalone setup")
    structured = payload.get("structured_output", False)
    if not isinstance(structured, bool):
        raise ValueError("Structured output support must be a boolean")
    config: dict[str, object] = {
        "schema": 1, "mode": mode, "workflow": "standalone", "profile": "custom",
        "context_tokens": 8192, "temperature": 0.1,
    }
    if mode == "compatible_api":
        endpoint = values["endpoint"]
        _chat_url(endpoint)
        parsed = urlparse(endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Endpoint must not contain credentials, query parameters, or a fragment")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote assistant endpoints require HTTPS; HTTP is allowed only on loopback")
        if not values["model_name"]:
            raise ValueError("A provider model name is required")
        if values["api_key_env"] and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", values["api_key_env"]):
            raise ValueError("API key environment variable must be a variable name, not a secret")
        config.update({key: values[key] for key in ("endpoint", "model_name", "api_key_env")})
        config["capabilities"] = [CAPABILITY_JSON_SCHEMA] if structured else []
        config["thinking"] = "provider_default"
    elif mode == "custom_local":
        runtime = _validate_runtime(Path(values["runtime_path"]))
        model = _validate_model(Path(values["model_path"]))
        config.update({
            "runtime_path": str(runtime), "model_path": str(model),
            "model_name": values["model_name"] or model.stem,
        })

    destination = sdk_assistant_root() / ASSISTANT_CONFIG
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A unique sibling plus replace prevents partial JSON and never edits legacy settings.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=".config-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(config, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination

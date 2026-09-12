"""Bounded, unambiguous request framing shared by the local SDK transports."""
from __future__ import annotations

import json
import math
from typing import IO, Any


class FrameError(ValueError):
    """One rejected frame; the next newline-delimited request remains usable."""


def read_frame(stream: IO[str], max_bytes: int) -> str | None:
    # Text streams count characters. At most max_bytes + 1 characters are ever
    # retained (bounded even for four-byte UTF-8); enforce bytes before parsing.
    raw = stream.readline(max_bytes + 1)
    if not raw:
        return None
    if len(raw) > max_bytes:
        # Drain this frame in bounded pieces, never reinterpret a tail as a new
        # request. EOF without a final newline is supported for valid clients.
        while raw and not raw.endswith("\n"):
            raw = stream.readline(max_bytes + 1)
        raise FrameError("request exceeds the size limit")
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise FrameError("invalid JSON: invalid Unicode") from exc
    if size > max_bytes:
        raise FrameError("request exceeds the size limit")
    return raw


def load_request(raw: str) -> Any:
    """Reject ambiguous keys, non-finite numbers, invalid Unicode and deep trees."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite number")

    def integer(value):
        if len(value.lstrip("-")) > 128:
            raise ValueError("integer exceeds the size limit")
        return int(value)

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_int=integer)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 64:
                raise ValueError("nesting exceeds the depth limit")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
                pending.extend((key, depth + 1) for key in item)
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("non-finite number")
            elif isinstance(item, str):
                item.encode("utf-8")
        return value
    except RecursionError as exc:
        raise FrameError("invalid JSON: nesting exceeds the depth limit") from exc
    except json.JSONDecodeError as exc:
        raise FrameError(f"invalid JSON: {exc.msg}") from exc
    except UnicodeError as exc:
        raise FrameError("invalid JSON: invalid Unicode") from exc
    except ValueError as exc:
        raise FrameError(f"invalid JSON: {exc}") from exc

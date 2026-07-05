from __future__ import annotations

import base64
import binascii
import json
from typing import Any


def decode_base64_image(value: str) -> bytes:
    cleaned = "".join(value.split())
    try:
        return base64.b64decode(cleaned, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 image data") from exc


def parse_sse_events(response_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush_data_lines() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data or data == "[DONE]":
            return
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed SSE JSON: {data[:200]}") from exc
        if isinstance(parsed, dict):
            events.append(parsed)

    for raw_line in response_text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            flush_data_lines()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    flush_data_lines()
    return events

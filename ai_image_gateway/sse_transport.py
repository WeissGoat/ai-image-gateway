"""Incremental response transport for OpenAI-compatible chat image APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal

import httpx
from httpx_sse import EventSource


class SSEPayloadError(ValueError):
    """The stream framing was valid but an SSE data payload was unusable."""


@dataclass(frozen=True)
class StreamReadResult:
    payload: dict[str, Any]
    response_mode: Literal["sse", "json"]
    event_count: int
    first_event_elapsed_s: float | None
    completed_by_done: bool

    def generation_params(self) -> dict[str, Any]:
        return {
            "stream_requested": True,
            "stream_response_mode": self.response_mode,
            "stream_event_count": self.event_count,
            "stream_first_event_elapsed_s": self.first_event_elapsed_s,
            "stream_completed_by_done": self.completed_by_done,
        }


def is_sse_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    return content_type == "text/event-stream"


async def read_response_excerpt(response: httpx.Response, *, limit: int = 4096) -> str:
    collected = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = limit - len(collected)
        if remaining <= 0:
            break
        collected.extend(chunk[:remaining])
        if len(collected) >= limit:
            break
    return bytes(collected).decode("utf-8", errors="replace")


async def read_streaming_response(
    response: httpx.Response,
    *,
    started_at: float | None = None,
) -> StreamReadResult:
    request_started_at = monotonic() if started_at is None else started_at
    if not is_sse_response(response):
        body = await response.aread()
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            excerpt = body[:500].decode("utf-8", errors="replace")
            raise SSEPayloadError(f"Non-JSON streaming response: {excerpt}") from exc
        if not isinstance(payload, dict):
            raise SSEPayloadError("Expected JSON object response")
        return StreamReadResult(payload, "json", 0, None, False)

    events: list[dict[str, Any]] = []
    event_count = 0
    first_event_elapsed_s: float | None = None
    completed_by_done = False
    source = EventSource(response)
    async for event in source.aiter_sse():
        data = event.data.strip()
        if not data:
            continue
        if data == "[DONE]":
            completed_by_done = True
            break
        if first_event_elapsed_s is None:
            first_event_elapsed_s = round(monotonic() - request_started_at, 3)
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SSEPayloadError(f"Malformed SSE JSON: {data[:500]}") from exc
        if not isinstance(payload, dict):
            raise SSEPayloadError(f"Expected SSE JSON object: {data[:500]}")
        events.append(payload)
        event_count += 1

    return StreamReadResult(
        payload={"_sse_events": events},
        response_mode="sse",
        event_count=event_count,
        first_event_elapsed_s=first_event_elapsed_s,
        completed_by_done=completed_by_done,
    )

"""Incremental SSE transport tests."""

from __future__ import annotations

import json

import httpx
import pytest

from ai_image_gateway.sse_transport import (
    SSEPayloadError,
    read_response_excerpt,
    read_streaming_response,
)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self._chunks = chunks
        self._error = error

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error


@pytest.mark.asyncio
async def test_streaming_sse_collects_multiline_json_and_done():
    payload = {
        "choices": [{"delta": {"content": "data:image/png;base64,AAAA"}}]
    }
    encoded = json.dumps(payload, indent=2)
    body = "".join(f"data: {line}\n" for line in encoded.splitlines())
    body += "\ndata: [DONE]\n\n"
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        stream=ChunkStream([body.encode("utf-8")]),
    )

    try:
        result = await read_streaming_response(response)
    finally:
        await response.aclose()

    assert result.response_mode == "sse"
    assert result.payload == {"_sse_events": [payload]}
    assert result.event_count == 1
    assert result.first_event_elapsed_s is not None
    assert result.completed_by_done is True


@pytest.mark.asyncio
async def test_streaming_sse_accepts_clean_eof_without_done():
    body = b'data: {"choices":[{"message":{"content":"https://example.com/a.png"}}]}\n\n'
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
        stream=ChunkStream([body]),
    )
    try:
        result = await read_streaming_response(response)
    finally:
        await response.aclose()
    assert result.event_count == 1
    assert result.completed_by_done is False


@pytest.mark.asyncio
async def test_streaming_response_falls_back_to_json_on_same_connection():
    payload = {"data": [{"url": "https://example.com/a.png"}]}
    response = httpx.Response(
        200,
        headers={"Content-Type": "application/json"},
        stream=ChunkStream([json.dumps(payload).encode("utf-8")]),
    )
    try:
        result = await read_streaming_response(response)
    finally:
        await response.aclose()
    assert result.response_mode == "json"
    assert result.payload == payload
    assert result.event_count == 0


@pytest.mark.asyncio
async def test_streaming_sse_rejects_malformed_json():
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        stream=ChunkStream([b"data: {not-json}\n\n"]),
    )
    try:
        with pytest.raises(SSEPayloadError, match="Malformed SSE JSON"):
            await read_streaming_response(response)
    finally:
        await response.aclose()


@pytest.mark.asyncio
async def test_streaming_sse_propagates_transport_disconnect():
    error = httpx.ReadError("connection reset")
    response = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        stream=ChunkStream([b'data: {"choices":[]}\n\n'], error=error),
    )
    try:
        with pytest.raises(httpx.ReadError, match="connection reset"):
            await read_streaming_response(response)
    finally:
        await response.aclose()


@pytest.mark.asyncio
async def test_response_excerpt_is_bounded():
    response = httpx.Response(524, stream=ChunkStream([b"x" * 6000]))
    try:
        excerpt = await read_response_excerpt(response, limit=4096)
    finally:
        await response.aclose()
    assert len(excerpt) == 4096

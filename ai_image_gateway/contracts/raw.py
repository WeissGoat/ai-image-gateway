from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NovelAIRawPayload(BaseModel):
    input: str
    model: str
    action: str = "generate"
    parameters: dict[str, Any] = Field(default_factory=dict)


class RetryRecord(BaseModel):
    attempt: int
    status_code: int | None = None
    error: str | None = None
    retryable: bool = False
    sleep_seconds: float | None = None


class NovelAIRawImage(BaseModel):
    filename: str
    image_bytes: bytes


class NovelAIRawResult(BaseModel):
    images: list[NovelAIRawImage] = Field(default_factory=list)
    request_payload: NovelAIRawPayload
    retry_records: list[RetryRecord] = Field(default_factory=list)
    response_headers: dict[str, str] = Field(default_factory=dict)

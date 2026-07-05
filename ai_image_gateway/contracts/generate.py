from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .common import ImageFormat


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    count: int = Field(default=1, ge=1, le=16)
    seed: int | None = None
    provider: str | None = None
    output_format: ImageFormat = ImageFormat.PNG
    extra: dict[str, Any] = Field(default_factory=dict)


class ImageToImageRequest(BaseModel):
    images: list[bytes] = Field(min_length=1, max_length=16)
    prompt: str
    negative_prompt: str = ""
    width: int | None = None
    height: int | None = None
    count: int = Field(default=1, ge=1, le=16)
    seed: int | None = None
    provider: str | None = None
    output_format: ImageFormat = ImageFormat.PNG
    extra: dict[str, Any] = Field(default_factory=dict)


class InpaintRequest(BaseModel):
    image: bytes
    mask: bytes
    prompt: str
    negative_prompt: str = ""
    width: int | None = None
    height: int | None = None
    count: int = Field(default=1, ge=1, le=16)
    seed: int | None = None
    provider: str | None = None
    output_format: ImageFormat = ImageFormat.PNG
    extra: dict[str, Any] = Field(default_factory=dict)

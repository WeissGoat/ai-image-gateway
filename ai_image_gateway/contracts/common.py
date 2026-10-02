from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Capability(str, Enum):
    GENERATE = "generate"
    IMAGE_TO_IMAGE = "image_to_image"
    INPAINT = "inpaint"
    UPSCALE = "upscale"


class ImageFormat(str, Enum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class ImageResult(BaseModel):
    image_bytes: bytes
    seed: int | None = None
    provider_name: str
    model_name: str = ""
    generation_params: dict[str, Any] = Field(default_factory=dict)
    cost: float = 0.0


class BatchResult(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    results: list[ImageResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(result.cost for result in self.results)

    @property
    def success_count(self) -> int:
        return len(self.results)

"""
统一数据模型。

所有对外交互的请求/响应结构定义。Provider 适配器和上层调用方
均通过此模块的类型进行数据交换，不直接暴露 provider 特有的数据结构。
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Capability(str, Enum):
    """Provider 支持的能力枚举。"""
    GENERATE = "generate"
    IMAGE_TO_IMAGE = "image_to_image"
    INPAINT = "inpaint"
    UPSCALE = "upscale"


class ImageFormat(str, Enum):
    """输出图片格式。"""
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    """文本到图片生成请求。"""

    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    count: int = Field(default=1, ge=1, le=16, description="单次生成张数")
    seed: int | None = Field(default=None, description="随机种子, None=随机")
    provider: str | None = Field(default=None, description="指定 provider 名称, None=按路由规则")
    output_format: ImageFormat = ImageFormat.PNG
    extra: dict[str, Any] = Field(default_factory=dict, description="Provider 特有参数透传")


class ImageToImageRequest(BaseModel):
    """参考图 / 图生图请求。"""

    images: list[bytes] = Field(
        min_length=1,
        max_length=16,
        description="参考图二进制数据列表",
    )
    prompt: str
    negative_prompt: str = ""
    width: int | None = None
    height: int | None = None
    count: int = Field(default=1, ge=1, le=16, description="单次生成候选张数")
    seed: int | None = Field(default=None, description="随机种子, None=随机")
    provider: str | None = None
    output_format: ImageFormat = ImageFormat.PNG
    extra: dict[str, Any] = Field(default_factory=dict)


class InpaintRequest(BaseModel):
    """图片局部重绘/修复请求。"""

    image: bytes = Field(description="原图二进制数据")
    mask: bytes = Field(description="Mask 二进制数据 (白色=重绘区域)")
    prompt: str
    negative_prompt: str = ""
    width: int | None = None
    height: int | None = None
    count: int = Field(default=1, ge=1, le=16, description="单次生成候选张数")
    seed: int | None = Field(default=None, description="随机种子, None=随机")
    provider: str | None = None
    output_format: ImageFormat = ImageFormat.PNG
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

class ImageResult(BaseModel):
    """单张图片生成结果。"""

    image_bytes: bytes
    seed: int | None = None
    provider_name: str
    model_name: str = ""
    generation_params: dict[str, Any] = Field(
        default_factory=dict,
        description="完整生成参数快照, 用于 generation.json 记录"
    )
    cost: float = Field(default=0.0, description="本次消耗 (Anlas / API credit)")


class BatchResult(BaseModel):
    """一次请求的批量结果容器。"""

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    results: list[ImageResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(r.cost for r in self.results)

    @property
    def success_count(self) -> int:
        return len(self.results)

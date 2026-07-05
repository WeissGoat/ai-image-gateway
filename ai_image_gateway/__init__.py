"""
AI Image Gateway — 聚合 AI 图片处理模块。

对外暴露统一接口，内部通过 Provider 适配器模式接入多种 AI 服务。

Usage::

    from ai_image_gateway import ImageService, GenerateRequest

    async with ImageService("config.yaml") as svc:
        result = await svc.generate(GenerateRequest(
            prompt="game item icon, rusty dagger, steampunk",
            width=512, height=512, count=4,
        ))
"""

from .contracts import (
    BatchResult,
    Capability,
    GenerateRequest,
    ImageFormat,
    ImageResult,
    ImageToImageRequest,
    InpaintRequest,
    NovelAIRawImage,
    NovelAIRawPayload,
    NovelAIRawResult,
    RetryRecord,
)
from .service import ImageService
from .image_inputs import (
    ImageInputError,
    ResolvedImageInput,
    detect_image_mime_type,
    image_bytes_to_data_url,
    resolve_image_input,
    resolve_image_inputs,
)
from .errors import (
    GatewayError,
    ConfigError,
    ProviderCapabilityError,
    ProviderError,
    ProviderNotFoundError,
    RateLimitError,
)
from .router import register_provider

__all__ = [
    # Service
    "ImageService",
    # Schema
    "GenerateRequest",
    "ImageToImageRequest",
    "InpaintRequest",
    "ImageResult",
    "BatchResult",
    "NovelAIRawPayload",
    "NovelAIRawResult",
    "NovelAIRawImage",
    "RetryRecord",
    "Capability",
    "ImageFormat",
    "ImageInputError",
    "ResolvedImageInput",
    "detect_image_mime_type",
    "image_bytes_to_data_url",
    "resolve_image_input",
    "resolve_image_inputs",
    # Errors
    "GatewayError",
    "ConfigError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderCapabilityError",
    "RateLimitError",
    # Extension
    "register_provider",
]

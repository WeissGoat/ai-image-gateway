"""Public package exports for ai-image-gateway."""

from .contracts.common import (
    BatchResult,
    Capability,
    ImageFormat,
    ImageResult,
)
from .contracts.generate import GenerateRequest, ImageToImageRequest, InpaintRequest
from .contracts.raw import NovelAIRawImage, NovelAIRawPayload, NovelAIRawResult, RetryRecord
from .errors import (
    ConfigError,
    GatewayError,
    ProviderCapabilityError,
    ProviderError,
    ProviderNotFoundError,
    RateLimitError,
)
from .facade.batch_service import BatchService
from .facade.image_service import ImageService
from .image_inputs import (
    ImageInputError,
    ResolvedImageInput,
    detect_image_mime_type,
    image_bytes_to_data_url,
    resolve_image_input,
    resolve_image_inputs,
)
from .router import register_provider

__all__ = [
    "BatchService",
    "ImageService",
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
    "GatewayError",
    "ConfigError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderCapabilityError",
    "RateLimitError",
    "register_provider",
]

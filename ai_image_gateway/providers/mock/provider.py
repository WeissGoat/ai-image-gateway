from __future__ import annotations

import io
import random
from typing import TYPE_CHECKING

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from ...schema import (
    Capability,
    GenerateRequest,
    ImageResult,
    ImageToImageRequest,
    InpaintRequest,
)
from ..base import BaseImageProvider

if TYPE_CHECKING:
    from ...config import ProviderConfig


class MockProvider(BaseImageProvider):
    """Generate placeholder images for local verification and tests."""

    name = "mock"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._bg_color: str = self._config.settings.get("bg_color", "#2a2a3d")
        self._text_color: str = self._config.settings.get("text_color", "#e0c878")
        self._initialized = False

    async def initialize(self) -> None:
        logger.info("[MockProvider] Initialized")
        self._initialized = True

    async def close(self) -> None:
        logger.info("[MockProvider] Closed")
        self._initialized = False

    def supports(self, capability: Capability) -> bool:
        return capability in (
            Capability.GENERATE,
            Capability.IMAGE_TO_IMAGE,
            Capability.INPAINT,
        )

    async def generate(self, request: GenerateRequest) -> list[ImageResult]:
        logger.info(
            "[MockProvider] generate: {}x{} count={} prompt='{}'",
            request.width,
            request.height,
            request.count,
            request.prompt[:60],
        )
        results: list[ImageResult] = []
        for i in range(request.count):
            seed = request.seed + i if request.seed is not None else random.randint(0, 2**32 - 1)
            img_bytes = self._render_placeholder(
                width=request.width,
                height=request.height,
                seed=seed,
                label=request.prompt[:80],
            )
            results.append(ImageResult(
                image_bytes=img_bytes,
                seed=seed,
                provider_name=self.name,
                model_name="mock-v1",
                generation_params={
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt,
                    "width": request.width,
                    "height": request.height,
                    "seed": seed,
                },
                cost=0.0,
            ))
        return results

    async def image_to_image(self, request: ImageToImageRequest) -> list[ImageResult]:
        logger.info("[MockProvider] image_to_image: prompt='{}'", request.prompt[:60])
        width = request.width or 512
        height = request.height or 512
        results: list[ImageResult] = []
        for i in range(request.count):
            seed = request.seed + i if request.seed is not None else random.randint(0, 2**32 - 1)
            img_bytes = self._render_placeholder(
                width=width,
                height=height,
                seed=seed,
                label=f"[IMAGE_TO_IMAGE] {request.prompt[:60]}",
            )
            results.append(ImageResult(
                image_bytes=img_bytes,
                seed=seed,
                provider_name=self.name,
                model_name="mock-v1",
                generation_params={
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "mode": "image_to_image",
                    "reference_image_count": len(request.images),
                },
                cost=0.0,
            ))
        return results

    async def inpaint(self, request: InpaintRequest) -> list[ImageResult]:
        logger.info("[MockProvider] inpaint: prompt='{}'", request.prompt[:60])
        width = request.width or 512
        height = request.height or 512
        results: list[ImageResult] = []
        for i in range(request.count):
            seed = request.seed + i if request.seed is not None else random.randint(0, 2**32 - 1)
            img_bytes = self._render_placeholder(
                width=width,
                height=height,
                seed=seed,
                label=f"[INPAINT] {request.prompt[:60]}",
            )
            results.append(ImageResult(
                image_bytes=img_bytes,
                seed=seed,
                provider_name=self.name,
                model_name="mock-v1",
                generation_params={
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "mode": "inpaint",
                },
                cost=0.0,
            ))
        return results

    def _render_placeholder(
        self,
        width: int,
        height: int,
        seed: int,
        label: str,
    ) -> bytes:
        img = Image.new("RGBA", (width, height), self._bg_color)
        draw = ImageDraw.Draw(img)

        line_color = "#444466"
        draw.line([(0, 0), (width, height)], fill=line_color, width=2)
        draw.line([(width, 0), (0, height)], fill=line_color, width=2)

        margin = min(width, height) // 8
        draw.rectangle(
            [margin, margin, width - margin, height - margin],
            outline=self._text_color,
            width=2,
        )

        try:
            font = ImageFont.truetype("arial.ttf", size=max(12, min(width, height) // 20))
        except OSError:
            font = ImageFont.load_default()

        lines = [
            f"MOCK | {width}x{height}",
            f"seed: {seed}",
            label,
        ]
        y = margin + 10
        for line in lines:
            draw.text((margin + 10, y), line, fill=self._text_color, font=font)
            y += max(16, min(width, height) // 16)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

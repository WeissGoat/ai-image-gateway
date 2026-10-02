from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ..config import GatewayConfig, load_config
from ..contracts import (
    BatchResult,
    Capability,
    GenerateRequest,
    ImageToImageRequest,
    InpaintRequest,
)
from ..errors import GatewayError
from ..router import ProviderRouter

if TYPE_CHECKING:
    from .batch_service import BatchService


class ImageService:
    def __init__(
        self,
        config: str | Path | GatewayConfig | None = None,
    ) -> None:
        if isinstance(config, GatewayConfig):
            self._config = config
        else:
            self._config = load_config(config)
        self._router = ProviderRouter(self._config)
        self._batch_service: BatchService | None = None

    async def generate(self, request: GenerateRequest) -> BatchResult:
        batch = BatchResult()
        try:
            provider = await self._router.get_provider(
                requested_name=request.provider,
                capability=Capability.GENERATE,
            )
            results = await provider.generate(request)
            batch.results.extend(results)
        except GatewayError as error:
            logger.error("Generate failed: {}", error)
            batch.errors.append(str(error))
        except Exception as error:
            logger.exception("Unexpected error during generate")
            batch.errors.append(f"Unexpected: {error}")
        return batch

    async def inpaint(self, request: InpaintRequest) -> BatchResult:
        batch = BatchResult()
        try:
            provider = await self._router.get_provider(
                requested_name=request.provider,
                capability=Capability.INPAINT,
            )
            results = await provider.inpaint(request)
            batch.results.extend(results)
        except GatewayError as error:
            logger.error("Inpaint failed: {}", error)
            batch.errors.append(str(error))
        except Exception as error:
            logger.exception("Unexpected error during inpaint")
            batch.errors.append(f"Unexpected: {error}")
        return batch

    async def image_to_image(self, request: ImageToImageRequest) -> BatchResult:
        batch = BatchResult()
        try:
            provider = await self._router.get_provider(
                requested_name=request.provider,
                capability=Capability.IMAGE_TO_IMAGE,
            )
            results = await provider.image_to_image(request)
            batch.results.extend(results)
        except GatewayError as error:
            logger.error("Image-to-image failed: {}", error)
            batch.errors.append(str(error))
        except Exception as error:
            logger.exception("Unexpected error during image_to_image")
            batch.errors.append(f"Unexpected: {error}")
        return batch

    async def batch_generate(
        self,
        requests: list[GenerateRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress=None,
    ) -> list[BatchResult]:
        return await self._get_batch_service().batch_generate(
            requests,
            concurrency=concurrency,
            delay_seconds=delay_seconds,
            on_progress=on_progress,
        )

    async def batch_inpaint(
        self,
        requests: list[InpaintRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress=None,
    ) -> list[BatchResult]:
        return await self._get_batch_service().batch_inpaint(
            requests,
            concurrency=concurrency,
            delay_seconds=delay_seconds,
            on_progress=on_progress,
        )

    async def batch_image_to_image(
        self,
        requests: list[ImageToImageRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress=None,
    ) -> list[BatchResult]:
        return await self._get_batch_service().batch_image_to_image(
            requests,
            concurrency=concurrency,
            delay_seconds=delay_seconds,
            on_progress=on_progress,
        )

    @property
    def available_providers(self) -> list[str]:
        return self._router.available_providers

    async def close(self) -> None:
        await self._router.close_all()

    async def __aenter__(self) -> ImageService:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _get_batch_service(self) -> BatchService:
        if self._batch_service is None:
            from .batch_service import BatchService

            self._batch_service = BatchService(self)
        return self._batch_service

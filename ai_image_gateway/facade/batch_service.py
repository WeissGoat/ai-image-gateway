from __future__ import annotations

import asyncio
from typing import Any, Callable, TYPE_CHECKING

from loguru import logger

from ..contracts import BatchResult, GenerateRequest, ImageToImageRequest, InpaintRequest

if TYPE_CHECKING:
    from .image_service import ImageService

ProgressCallback = Callable[[int, int, BatchResult], Any] | None


class BatchService:
    def __init__(self, image_service: ImageService) -> None:
        self._image_service = image_service

    async def batch_generate(
        self,
        requests: list[GenerateRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress: ProgressCallback = None,
    ) -> list[BatchResult]:
        return await self._run_batches(
            requests=requests,
            handler=self._image_service.generate,
            concurrency=concurrency,
            delay_seconds=delay_seconds,
            on_progress=on_progress,
            progress_label="Batch progress",
        )

    async def batch_inpaint(
        self,
        requests: list[InpaintRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress: ProgressCallback = None,
    ) -> list[BatchResult]:
        return await self._run_batches(
            requests=requests,
            handler=self._image_service.inpaint,
            concurrency=concurrency,
            delay_seconds=delay_seconds,
            on_progress=on_progress,
            progress_label="Inpaint batch progress",
        )

    async def batch_image_to_image(
        self,
        requests: list[ImageToImageRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress: ProgressCallback = None,
    ) -> list[BatchResult]:
        return await self._run_batches(
            requests=requests,
            handler=self._image_service.image_to_image,
            concurrency=concurrency,
            delay_seconds=delay_seconds,
            on_progress=on_progress,
            progress_label="Image-to-image batch progress",
        )

    async def _run_batches(
        self,
        *,
        requests: list[Any],
        handler: Callable[[Any], Any],
        concurrency: int,
        delay_seconds: float,
        on_progress: ProgressCallback,
        progress_label: str,
    ) -> list[BatchResult]:
        results: list[BatchResult] = []
        total = len(requests)
        semaphore = asyncio.Semaphore(concurrency)

        async def process(index: int, request: Any) -> BatchResult:
            async with semaphore:
                if index > 0 and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                batch = await handler(request)
                if on_progress:
                    on_progress(index + 1, total, batch)
                return batch

        if concurrency <= 1:
            for index, request in enumerate(requests):
                batch = await process(index, request)
                results.append(batch)
                logger.info(
                    "{}: {}/{}, success={}, errors={}",
                    progress_label,
                    index + 1,
                    total,
                    batch.success_count,
                    len(batch.errors),
                )
            return results

        tasks = [process(index, request) for index, request in enumerate(requests)]
        return await asyncio.gather(*tasks)

"""
ImageService — 对外统一入口 (Facade)。

使用方只需与此类交互，不直接接触 provider、router 或 config。
支持同步上下文管理器 (async with) 和批量生成。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from .config import GatewayConfig, load_config
from .errors import GatewayError, ProviderError
from .router import ProviderRouter
from .schema import (
    BatchResult,
    Capability,
    GenerateRequest,
    ImageToImageRequest,
    ImageResult,
    InpaintRequest,
)


class ImageService:
    """
    AI 图片服务聚合入口。

    Usage::

        async with ImageService("config.yaml") as svc:
            result = await svc.generate(GenerateRequest(
                prompt="game item icon, rusty dagger",
                width=512, height=512, count=4,
            ))
            for img in result.results:
                Path(f"out/{img.seed}.png").write_bytes(img.image_bytes)
    """

    def __init__(
        self,
        config: str | Path | GatewayConfig | None = None,
    ) -> None:
        if isinstance(config, GatewayConfig):
            self._config = config
        else:
            self._config = load_config(config)
        self._router = ProviderRouter(self._config)

    # ------------------------------------------------------------------
    # 核心操作
    # ------------------------------------------------------------------

    async def generate(self, request: GenerateRequest) -> BatchResult:
        """单次文本到图片生成。"""
        batch = BatchResult()
        try:
            provider = await self._router.get_provider(
                requested_name=request.provider,
                capability=Capability.GENERATE,
            )
            results = await provider.generate(request)
            batch.results.extend(results)
        except GatewayError as e:
            logger.error("Generate failed: {}", e)
            batch.errors.append(str(e))
        except Exception as e:
            logger.exception("Unexpected error during generate")
            batch.errors.append(f"Unexpected: {e}")
        return batch

    async def inpaint(self, request: InpaintRequest) -> BatchResult:
        """图片局部重绘/修复。"""
        batch = BatchResult()
        try:
            provider = await self._router.get_provider(
                requested_name=request.provider,
                capability=Capability.INPAINT,
            )
            results = await provider.inpaint(request)
            batch.results.extend(results)
        except GatewayError as e:
            logger.error("Inpaint failed: {}", e)
            batch.errors.append(str(e))
        except Exception as e:
            logger.exception("Unexpected error during inpaint")
            batch.errors.append(f"Unexpected: {e}")
        return batch

    async def image_to_image(self, request: ImageToImageRequest) -> BatchResult:
        """参考图 / 图生图。"""
        batch = BatchResult()
        try:
            provider = await self._router.get_provider(
                requested_name=request.provider,
                capability=Capability.IMAGE_TO_IMAGE,
            )
            results = await provider.image_to_image(request)
            batch.results.extend(results)
        except GatewayError as e:
            logger.error("Image-to-image failed: {}", e)
            batch.errors.append(str(e))
        except Exception as e:
            logger.exception("Unexpected error during image_to_image")
            batch.errors.append(f"Unexpected: {e}")
        return batch

    async def batch_generate(
        self,
        requests: list[GenerateRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress: Callable[[int, int, BatchResult], Any] | None = None,
    ) -> list[BatchResult]:
        """
        批量生成。

        Args:
            requests: 生成请求列表。
            concurrency: 并发数 (建议 1，多数 AI 服务限制并发)。
            delay_seconds: 请求间延迟 (秒)，防限流。
            on_progress: 进度回调 (current_index, total, latest_result)。

        Returns:
            与 requests 等长的 BatchResult 列表。
        """
        results: list[BatchResult] = []
        total = len(requests)
        semaphore = asyncio.Semaphore(concurrency)

        async def _process(index: int, req: GenerateRequest) -> BatchResult:
            async with semaphore:
                if index > 0 and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                batch = await self.generate(req)
                if on_progress:
                    on_progress(index + 1, total, batch)
                return batch

        if concurrency <= 1:
            # 串行执行 (最常见场景)
            for i, req in enumerate(requests):
                batch = await _process(i, req)
                results.append(batch)
                logger.info(
                    "Batch progress: {}/{}, success={}, errors={}",
                    i + 1, total, batch.success_count, len(batch.errors),
                )
        else:
            # 有限并发
            tasks = [_process(i, req) for i, req in enumerate(requests)]
            results = await asyncio.gather(*tasks)

        return results

    async def batch_inpaint(
        self,
        requests: list[InpaintRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress: Callable[[int, int, BatchResult], Any] | None = None,
    ) -> list[BatchResult]:
        """
        批量局部重绘/修复。
        Defaults to serial execution because most image providers rate-limit
        inpaint endpoints aggressively.
        """
        results: list[BatchResult] = []
        total = len(requests)
        semaphore = asyncio.Semaphore(concurrency)

        async def _process(index: int, req: InpaintRequest) -> BatchResult:
            async with semaphore:
                if index > 0 and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                batch = await self.inpaint(req)
                if on_progress:
                    on_progress(index + 1, total, batch)
                return batch

        if concurrency <= 1:
            for i, req in enumerate(requests):
                batch = await _process(i, req)
                results.append(batch)
                logger.info(
                    "Inpaint batch progress: {}/{}, success={}, errors={}",
                    i + 1, total, batch.success_count, len(batch.errors),
                )
        else:
            tasks = [_process(i, req) for i, req in enumerate(requests)]
            results = await asyncio.gather(*tasks)

        return results

    async def batch_image_to_image(
        self,
        requests: list[ImageToImageRequest],
        *,
        concurrency: int = 1,
        delay_seconds: float = 2.0,
        on_progress: Callable[[int, int, BatchResult], Any] | None = None,
    ) -> list[BatchResult]:
        """批量参考图 / 图生图。"""
        results: list[BatchResult] = []
        total = len(requests)
        semaphore = asyncio.Semaphore(concurrency)

        async def _process(index: int, req: ImageToImageRequest) -> BatchResult:
            async with semaphore:
                if index > 0 and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                batch = await self.image_to_image(req)
                if on_progress:
                    on_progress(index + 1, total, batch)
                return batch

        if concurrency <= 1:
            for i, req in enumerate(requests):
                batch = await _process(i, req)
                results.append(batch)
                logger.info(
                    "Image-to-image batch progress: {}/{}, success={}, errors={}",
                    i + 1, total, batch.success_count, len(batch.errors),
                )
        else:
            tasks = [_process(i, req) for i, req in enumerate(requests)]
            results = await asyncio.gather(*tasks)

        return results

    # ------------------------------------------------------------------
    # 实用方法
    # ------------------------------------------------------------------

    @property
    def available_providers(self) -> list[str]:
        """返回当前可用的 provider 名称列表。"""
        return self._router.available_providers

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭所有 provider 连接。"""
        await self._router.close_all()

    async def __aenter__(self) -> ImageService:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

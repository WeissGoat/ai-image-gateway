"""
Provider 抽象基类。

所有 AI 图片服务适配器必须继承 BaseImageProvider 并实现其抽象方法。
Gateway 的 Router 和 Service 层仅依赖此协议，不依赖具体 provider 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..errors import ProviderCapabilityError
from ..schema import Capability, GenerateRequest, ImageToImageRequest, InpaintRequest, ImageResult

if TYPE_CHECKING:
    from ..config import ProviderConfig


class BaseImageProvider(ABC):
    """AI 图片服务适配器协议。"""

    # 子类必须设置
    name: str = ""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @abstractmethod
    async def initialize(self) -> None:
        """
        初始化连接/认证。

        在首次使用前由 Service 层调用。允许抛出 ProviderError。
        """

    @abstractmethod
    async def close(self) -> None:
        """释放资源 (HTTP session、WebSocket 等)。"""

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------

    @abstractmethod
    def supports(self, capability: Capability) -> bool:
        """声明本 provider 是否支持指定能力。"""

    # ------------------------------------------------------------------
    # 核心操作
    # ------------------------------------------------------------------

    @abstractmethod
    async def generate(self, request: GenerateRequest) -> list[ImageResult]:
        """
        文本到图片生成。

        返回 list[ImageResult]，长度应等于 request.count。
        单张失败不应中断整批，而应在返回的 list 中减少数量并由 Service 层记录 error。
        """

    async def image_to_image(self, request: ImageToImageRequest) -> list[ImageResult]:
        """
        参考图 / 图生图。
        不支持时抛出 ProviderCapabilityError。
        """
        raise ProviderCapabilityError(self.name, Capability.IMAGE_TO_IMAGE.value)

    @abstractmethod
    async def inpaint(self, request: InpaintRequest) -> list[ImageResult]:
        """
        图片局部重绘/修复。

        不支持时抛出 ProviderCapabilityError。
        """

    # ------------------------------------------------------------------
    # Context Manager 支持
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BaseImageProvider:
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

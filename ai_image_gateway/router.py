"""
Provider 路由。

根据请求中指定的 provider 或配置文件的默认规则，
选择并返回对应的 BaseImageProvider 实例。
Provider 实例通过工厂注册表按 name 索引，支持后续动态扩展。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from .config import GatewayConfig, ProviderConfig
from .errors import ProviderCapabilityError, ProviderNotFoundError
from .schema import Capability
from .providers.base import BaseImageProvider
from .providers.mock import MockProvider

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Provider 工厂注册表
# ---------------------------------------------------------------------------

# name -> provider class OR lazy import string "module:ClassName"
# 新增 provider 只需在此注册
_PROVIDER_REGISTRY: dict[str, type[BaseImageProvider] | str] = {
    "mock": MockProvider,
    "novelai": "ai_image_gateway.providers.novelai:NovelAIProvider",
    "openai_images": "ai_image_gateway.providers.openai_compatible:OpenAIImagesProvider",
    "openai_chat_image": "ai_image_gateway.providers.openai_compatible:OpenAIChatImageProvider",
    "gemini_chat_image": "ai_image_gateway.providers.openai_compatible:GeminiChatImageProvider",
    "grok_chat_image": "ai_image_gateway.providers.openai_compatible:GrokChatImageProvider",
}


def _resolve_lazy(entry: type[BaseImageProvider] | str) -> type[BaseImageProvider]:
    """Resolve a lazy import string to the actual class."""
    if isinstance(entry, str):
        module_path, class_name = entry.rsplit(":", 1)
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    return entry


def register_provider(name: str, cls: type[BaseImageProvider]) -> None:
    """注册自定义 provider 类型 (供外部扩展)。"""
    _PROVIDER_REGISTRY[name] = cls
    logger.info("Registered provider: '{}'", name)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class ProviderRouter:
    """
    Provider 选择与实例管理。

    懒初始化: Provider 实例在首次请求时创建并缓存。
    """

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._instances: dict[str, BaseImageProvider] = {}

    async def get_provider(
        self,
        *,
        requested_name: str | None = None,
        capability: Capability = Capability.GENERATE,
    ) -> BaseImageProvider:
        """
        获取 provider 实例。

        - requested_name 非空: 直接定位
        - requested_name 为空: 按 default_provider 配置路由
        """
        name = requested_name or self._resolve_default(capability)
        provider = await self._get_or_create(name)

        if not provider.supports(capability):
            raise ProviderCapabilityError(name, capability.value)

        return provider

    @property
    def available_providers(self) -> list[str]:
        """返回配置中已启用的 provider 名称列表。"""
        names = [
            name for name, cfg in self._config.providers.items()
            if cfg.enabled
        ]
        # mock 始终可用
        if "mock" not in names:
            names.append("mock")
        return names

    async def close_all(self) -> None:
        """关闭所有已实例化的 provider。"""
        for name, instance in self._instances.items():
            try:
                await instance.close()
                logger.info("Closed provider: '{}'", name)
            except Exception as e:
                logger.error("Error closing provider '{}': {}", name, e)
        self._instances.clear()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _resolve_default(self, capability: Capability) -> str:
        """根据能力类型查找默认 provider。"""
        defaults = self._config.default_provider
        mapping = {
            Capability.GENERATE: defaults.generate,
            Capability.IMAGE_TO_IMAGE: defaults.image_to_image,
            Capability.INPAINT: defaults.inpaint,
            Capability.UPSCALE: defaults.upscale,
        }
        return mapping.get(capability, "mock")

    async def _get_or_create(self, name: str) -> BaseImageProvider:
        """懒创建并缓存 provider 实例。"""
        if name in self._instances:
            return self._instances[name]

        # 查找 provider 类
        entry = _PROVIDER_REGISTRY.get(name)
        if entry is None:
            raise ProviderNotFoundError(name)
        cls = _resolve_lazy(entry)

        # 查找配置 (mock 无需显式配置)
        provider_cfg = self._config.providers.get(name, ProviderConfig())

        if not provider_cfg.enabled and name != "mock":
            raise ProviderNotFoundError(name)

        instance = cls(provider_cfg)
        await instance.initialize()
        self._instances[name] = instance
        logger.info("Created and initialized provider: '{}'", name)
        return instance

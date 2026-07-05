"""Providers 子包。"""

from .base import BaseImageProvider
from .mock import MockProvider

# NovelAIProvider is NOT imported eagerly to avoid requiring numpy/argon2
# at package load time. It's lazily loaded via router._PROVIDER_REGISTRY.

def __getattr__(name: str):
    if name in {"NovelAIProvider", "NovelAIRawClient"}:
        from .novelai import NovelAIProvider, NovelAIRawClient

        return {
            "NovelAIProvider": NovelAIProvider,
            "NovelAIRawClient": NovelAIRawClient,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["BaseImageProvider", "MockProvider", "NovelAIProvider", "NovelAIRawClient"]

"""Providers 子包。"""

from .base import BaseImageProvider
from .mock import MockProvider

# NovelAIProvider is NOT imported eagerly to avoid requiring numpy/argon2
# at package load time. It's lazily loaded via router._PROVIDER_REGISTRY.

__all__ = ["BaseImageProvider", "MockProvider"]

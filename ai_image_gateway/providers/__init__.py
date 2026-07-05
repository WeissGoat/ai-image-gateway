"""Provider package public exports."""

from .base import BaseImageProvider
from .mock import MockProvider


def __getattr__(name: str):
    if name in {"NovelAIProvider", "NovelAIRawClient"}:
        from .novelai import NovelAIProvider, NovelAIRawClient

        return {
            "NovelAIProvider": NovelAIProvider,
            "NovelAIRawClient": NovelAIRawClient,
        }[name]

    if name in {
        "GeminiChatImageProvider",
        "GrokChatImageProvider",
        "OpenAIChatImageProvider",
        "OpenAIImagesProvider",
    }:
        from .openai_compatible import (
            GeminiChatImageProvider,
            GrokChatImageProvider,
            OpenAIChatImageProvider,
            OpenAIImagesProvider,
        )

        return {
            "GeminiChatImageProvider": GeminiChatImageProvider,
            "GrokChatImageProvider": GrokChatImageProvider,
            "OpenAIChatImageProvider": OpenAIChatImageProvider,
            "OpenAIImagesProvider": OpenAIImagesProvider,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseImageProvider",
    "MockProvider",
    "NovelAIProvider",
    "NovelAIRawClient",
    "OpenAIImagesProvider",
    "OpenAIChatImageProvider",
    "GeminiChatImageProvider",
    "GrokChatImageProvider",
]

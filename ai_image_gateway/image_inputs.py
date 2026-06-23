"""Utilities for normalizing image inputs used by gateway runners."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import httpx


DEFAULT_MAX_IMAGE_BYTES = 32 * 1024 * 1024

DATA_IMAGE_URL_RE = re.compile(
    r"data:(?P<mime>image/(?:png|jpe?g|webp|gif|avif|bmp));base64,(?P<data>[A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)


class ImageInputError(ValueError):
    """Raised when an image input cannot be resolved safely."""


@dataclass(frozen=True)
class ResolvedImageInput:
    """A normalized image input ready for provider-specific payload building."""

    image_bytes: bytes
    mime_type: str
    source: str


def detect_image_mime_type(image: bytes) -> str:
    """Best-effort MIME sniffing for common raster formats."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    if image.startswith(b"GIF87a") or image.startswith(b"GIF89a"):
        return "image/gif"
    if image.startswith(b"BM"):
        return "image/bmp"
    if len(image) >= 12 and image[4:8] == b"ftyp" and b"avif" in image[8:16]:
        return "image/avif"
    return "image/png"


def image_bytes_to_data_url(image: bytes, mime_type: str | None = None) -> str:
    """Encode image bytes as a data URL for chat-completions image parts."""
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{mime_type or detect_image_mime_type(image)};base64,{encoded}"


def decode_image_data_url(data_url: str, *, max_bytes: int = DEFAULT_MAX_IMAGE_BYTES) -> ResolvedImageInput:
    """Decode a data:image URL and enforce a byte-size limit."""
    match = DATA_IMAGE_URL_RE.fullmatch(data_url.strip())
    if not match:
        raise ImageInputError("Invalid image data URL")
    raw_base64 = re.sub(r"\s+", "", match.group("data"))
    try:
        image_bytes = base64.b64decode(raw_base64, validate=True)
    except ValueError as exc:
        raise ImageInputError("Invalid base64 image data") from exc
    _ensure_size_limit(image_bytes, max_bytes)
    return ResolvedImageInput(
        image_bytes=image_bytes,
        mime_type=match.group("mime").lower().replace("jpg", "jpeg"),
        source="data_url",
    )


async def resolve_image_input(
    value: str | Path | bytes | bytearray,
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> ResolvedImageInput:
    """Resolve bytes, local paths, HTTP(S) URLs, or data URLs into image bytes."""
    if isinstance(value, (bytes, bytearray)):
        image_bytes = bytes(value)
        _ensure_size_limit(image_bytes, max_bytes)
        return ResolvedImageInput(
            image_bytes=image_bytes,
            mime_type=detect_image_mime_type(image_bytes),
            source="bytes",
        )

    text = str(value).strip()
    if not text:
        raise ImageInputError("Image input is empty")
    if text.startswith("data:image/"):
        return decode_image_data_url(text, max_bytes=max_bytes)
    if text.startswith(("http://", "https://")):
        return await _download_image_input(text, client=client, max_bytes=max_bytes)

    path = Path(text).expanduser()
    if not path.exists() or not path.is_file():
        raise ImageInputError(f"Image file not found: {path}")
    image_bytes = path.read_bytes()
    _ensure_size_limit(image_bytes, max_bytes)
    return ResolvedImageInput(
        image_bytes=image_bytes,
        mime_type=detect_image_mime_type(image_bytes),
        source=str(path),
    )


async def resolve_image_inputs(
    values: Iterable[str | Path | bytes | bytearray],
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> list[ResolvedImageInput]:
    """Resolve multiple image inputs while preserving input order."""
    return [
        await resolve_image_input(value, client=client, max_bytes=max_bytes)
        for value in values
    ]


async def _download_image_input(
    url: str,
    *,
    client: httpx.AsyncClient | None,
    max_bytes: int,
) -> ResolvedImageInput:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ImageInputError("Only HTTP(S) image URLs are supported")

    owned_client: httpx.AsyncClient | None = None
    if client is None:
        owned_client = httpx.AsyncClient(timeout=120, follow_redirects=True, max_redirects=3)
        client = owned_client

    try:
        response = await client.get(url, headers={"User-Agent": "ai-image-gateway/0.1"})
    finally:
        if owned_client is not None:
            await owned_client.aclose()

    if response.status_code >= 400:
        raise ImageInputError(f"Image URL download failed: HTTP {response.status_code}")

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type and not content_type.startswith("image/") and content_type != "application/octet-stream":
        raise ImageInputError(f"Image URL returned non-image content type: {content_type}")

    image_bytes = response.content
    _ensure_size_limit(image_bytes, max_bytes)
    return ResolvedImageInput(
        image_bytes=image_bytes,
        mime_type=content_type if content_type.startswith("image/") else detect_image_mime_type(image_bytes),
        source=url,
    )


def _ensure_size_limit(image_bytes: bytes, max_bytes: int) -> None:
    if len(image_bytes) > max_bytes:
        raise ImageInputError(f"Image input too large: {len(image_bytes)} bytes (max {max_bytes})")

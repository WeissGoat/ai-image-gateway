from __future__ import annotations

import io
import zipfile

from ...contracts import NovelAIRawImage, NovelAIRawResult
from ...errors import ProviderError


def extract_zip_images(content: bytes) -> list[NovelAIRawImage]:
    images: list[NovelAIRawImage] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            images.append(
                NovelAIRawImage(
                    filename=info.filename,
                    image_bytes=zf.read(info),
                )
            )
    return images


def first_image_bytes(raw: NovelAIRawResult, *, provider_name: str) -> bytes:
    if not raw.images:
        raise ProviderError(provider_name, "NovelAI returned no images")
    return raw.images[0].image_bytes

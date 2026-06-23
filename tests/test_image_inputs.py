"""Image input normalization tests."""

import base64
import io

import httpx
import pytest
from PIL import Image

from ai_image_gateway.image_inputs import (
    ImageInputError,
    decode_image_data_url,
    detect_image_mime_type,
    image_bytes_to_data_url,
    resolve_image_input,
    resolve_image_inputs,
)


def _png_bytes(width: int = 16, height: int = 12) -> bytes:
    img = Image.new("RGBA", (width, height), "#224466")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_detect_image_mime_type_and_data_url_roundtrip():
    image = _png_bytes()

    assert detect_image_mime_type(image) == "image/png"
    data_url = image_bytes_to_data_url(image)
    resolved = decode_image_data_url(data_url)

    assert resolved.image_bytes == image
    assert resolved.mime_type == "image/png"
    assert resolved.source == "data_url"


@pytest.mark.asyncio
async def test_resolve_local_path(tmp_path):
    image = _png_bytes(10, 8)
    path = tmp_path / "reference.png"
    path.write_bytes(image)

    resolved = await resolve_image_input(path)

    assert resolved.image_bytes == image
    assert resolved.mime_type == "image/png"
    assert resolved.source.endswith("reference.png")


@pytest.mark.asyncio
async def test_resolve_http_image_url():
    image = _png_bytes(9, 7)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://cdn.example.com/reference.png"
        return httpx.Response(200, content=image, headers={"Content-Type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolved = await resolve_image_input("https://cdn.example.com/reference.png", client=client)

    assert resolved.image_bytes == image
    assert resolved.mime_type == "image/png"
    assert resolved.source == "https://cdn.example.com/reference.png"


@pytest.mark.asyncio
async def test_resolve_multiple_inputs_preserves_order():
    first = _png_bytes(5, 5)
    second = _png_bytes(6, 6)
    second_data_url = "data:image/png;base64," + base64.b64encode(second).decode("ascii")

    resolved = await resolve_image_inputs([first, second_data_url])

    assert [item.image_bytes for item in resolved] == [first, second]


@pytest.mark.asyncio
async def test_resolve_http_rejects_non_image_content_type():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not an image", headers={"Content-Type": "text/plain"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ImageInputError, match="non-image"):
            await resolve_image_input("https://cdn.example.com/not-image.txt", client=client)


def test_data_url_rejects_oversized_payload():
    data_url = "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")

    with pytest.raises(ImageInputError, match="too large"):
        decode_image_data_url(data_url, max_bytes=1)

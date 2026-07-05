"""OpenAI-compatible image provider tests."""

import base64
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from ai_image_gateway.config import ProviderConfig
from ai_image_gateway import ImageService
from ai_image_gateway.config import DefaultProviderConfig, GatewayConfig
from ai_image_gateway.errors import ProviderError
from ai_image_gateway.providers.openai_compatible import (
    OpenAIChatImageProvider,
    OpenAIImagesProvider,
)
from ai_image_gateway.schema import Capability, GenerateRequest, ImageToImageRequest


def test_provider_packages_are_the_only_homes_for_exports():
    from ai_image_gateway.providers.mock import MockProvider
    from ai_image_gateway.providers.openai_compatible import OpenAIImagesProvider as PackagedOpenAIImagesProvider

    assert MockProvider is not None
    assert PackagedOpenAIImagesProvider is OpenAIImagesProvider
    assert not Path("ai_image_gateway/providers/mock.py").exists()
    assert not Path("ai_image_gateway/providers/openai_compatible.py").exists()


def _png_b64(width: int = 16, height: int = 12) -> str:
    return base64.b64encode(_png_bytes(width, height)).decode("ascii")


def _png_bytes(width: int = 16, height: int = 12) -> bytes:
    img = Image.new("RGBA", (width, height), "#224466")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mock_response(
    payload: dict,
    *,
    text: str | None = None,
    headers: dict[str, str] | None = None,
    json_error: bool = False,
) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    if json_error:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = payload
    response.text = text if text is not None else str(payload)
    response.headers = headers or {}
    return response


class TestOpenAIImagesProvider:
    @pytest.mark.asyncio
    async def test_generate_posts_images_payload_and_decodes_b64_json(self):
        provider = OpenAIImagesProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={
                "base_url": "https://proxy.example.com/v1/",
                "model": "gpt-image-2",
                "response_format": "b64_json",
            },
        ))
        await provider.initialize()

        response = _mock_response({
            "data": [
                {"b64_json": _png_b64(), "seed": 101},
                {"b64_json": _png_b64(), "seed": 102},
            ],
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response) as post:
            results = await provider.generate(GenerateRequest(
                prompt="formal game icon",
                negative_prompt="watermark",
                width=512,
                height=768,
                count=2,
                seed=101,
                extra={"quality": "high"},
            ))

        assert len(results) == 2
        assert results[0].provider_name == "openai_images"
        assert results[0].model_name == "gpt-image-2"
        assert results[0].seed == 101
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (16, 12)

        assert post.call_args.args[0] == "https://proxy.example.com/v1/images/generations"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
        payload = post.call_args.kwargs["json"]
        assert payload["model"] == "gpt-image-2"
        assert payload["prompt"] == "formal game icon\n\nNegative prompt: watermark"
        assert payload["n"] == 2
        assert payload["size"] == "512x768"
        assert payload["response_format"] == "b64_json"
        assert payload["quality"] == "high"

    @pytest.mark.asyncio
    async def test_supports_generate_and_image_to_image_not_inpaint(self):
        provider = OpenAIImagesProvider(ProviderConfig())
        assert provider.supports(Capability.GENERATE)
        assert provider.supports(Capability.IMAGE_TO_IMAGE)
        assert not provider.supports(Capability.INPAINT)

    @pytest.mark.asyncio
    async def test_image_to_image_posts_images_edits_multipart_and_decodes_b64_json(self):
        provider = OpenAIImagesProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={
                "base_url": "https://proxy.example.com/v1",
                "model": "gpt-image-2",
                "response_format": "b64_json",
                "quality": "high",
            },
        ))
        await provider.initialize()

        response = _mock_response({"data": [{"b64_json": _png_b64(24, 18), "seed": 301}]})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response) as post:
            results = await provider.image_to_image(ImageToImageRequest(
                images=[_png_bytes(64, 64), _png_bytes(32, 32)],
                prompt="repair the face line",
                negative_prompt="blur",
                width=1024,
                height=1024,
                count=1,
                seed=301,
                extra={"output_format": "png"},
            ))

        assert len(results) == 1
        assert results[0].provider_name == "openai_images"
        assert results[0].model_name == "gpt-image-2"
        assert results[0].seed == 301
        assert results[0].generation_params["api_surface"] == "images/edits"
        assert results[0].generation_params["mode"] == "image_to_image"
        assert results[0].generation_params["reference_image_count"] == 2
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (24, 18)

        assert post.call_args.args[0] == "https://proxy.example.com/v1/images/edits"
        assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}
        assert post.call_args.kwargs["data"] == {
            "model": "gpt-image-2",
            "prompt": "repair the face line\n\nNegative prompt: blur",
            "n": "1",
            "response_format": "b64_json",
            "size": "1024x1024",
            "quality": "high",
            "output_format": "png",
        }
        files = post.call_args.kwargs["files"]
        assert len(files) == 2
        assert [field for field, _ in files] == ["image[]", "image[]"]
        assert files[0][1][0] == "image_0.png"
        assert files[0][1][2] == "image/png"
        assert files[1][1][2] == "image/png"
        assert "mask" not in [field for field, _ in files]

    @pytest.mark.asyncio
    async def test_image_to_image_single_reference_uses_image_field(self):
        provider = OpenAIImagesProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={"base_url": "https://proxy.example.com/v1", "model": "gpt-image-2"},
        ))
        await provider.initialize()

        response = _mock_response({"data": [{"b64_json": _png_b64(12, 12)}]})

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response) as post:
            await provider.image_to_image(ImageToImageRequest(
                images=[_png_bytes(64, 64)],
                prompt="reference edit",
            ))

        files = post.call_args.kwargs["files"]
        assert [field for field, _ in files] == ["image"]

    @pytest.mark.asyncio
    async def test_images_provider_surfaces_provider_error_payload(self):
        provider = OpenAIImagesProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={
                "base_url": "https://proxy.example.com/v1",
                "model": "gpt-image-2",
            },
        ))
        await provider.initialize()

        response = _mock_response({
            "error": {
                "message": "openai_error",
                "type": "bad_response_status_code",
                "code": "bad_response_status_code",
            },
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response):
            with pytest.raises(ProviderError, match="openai_error"):
                await provider.generate(GenerateRequest(prompt="icon"))


class TestOpenAIChatImageProvider:
    @pytest.mark.asyncio
    async def test_generate_posts_chat_payload_and_decodes_data_url(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={
                "base_url": "https://proxy.example.com/v1",
                "model": "gemini-3.1-flash-image",
            },
        ))
        await provider.initialize()

        response = _mock_response({
            "choices": [{
                "message": {
                    "content": f"![image](data:image/png;base64,{_png_b64(20, 10)})"
                }
            }],
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response) as post:
            results = await provider.generate(GenerateRequest(
                prompt="anime dungeon background",
                width=1024,
                height=1024,
                count=1,
                extra={"temperature": 0.2},
            ))

        assert len(results) == 1
        assert results[0].provider_name == "openai_chat_image"
        assert results[0].model_name == "gemini-3.1-flash-image"
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (20, 10)

        assert post.call_args.args[0] == "https://proxy.example.com/v1/chat/completions"
        payload = post.call_args.kwargs["json"]
        assert payload["model"] == "gemini-3.1-flash-image"
        assert payload["messages"][-1]["role"] == "user"
        assert "anime dungeon background" in payload["messages"][-1]["content"]
        assert "1024x1024" in payload["messages"][-1]["content"]
        assert "n" not in payload
        assert payload["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_generate_chat_omits_images_api_response_format_string(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={
                "base_url": "https://proxy.example.com/v1",
                "model": "gemini-3.1-flash-image",
                "response_format": "b64_json",
            },
        ))
        await provider.initialize()

        response = _mock_response({
            "choices": [{
                "message": {"content": f"data:image/png;base64,{_png_b64(20, 10)}"}
            }],
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response) as post:
            await provider.generate(GenerateRequest(prompt="icon"))

        payload = post.call_args.kwargs["json"]
        assert "response_format" not in payload

    @pytest.mark.asyncio
    async def test_generate_chat_allows_explicit_n_passthrough(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={
                "base_url": "https://proxy.example.com/v1",
                "model": "gemini-3.1-flash-image",
            },
        ))
        await provider.initialize()

        response = _mock_response({
            "choices": [{
                "message": {"content": f"data:image/png;base64,{_png_b64(20, 10)}"}
            }],
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response) as post:
            await provider.generate(GenerateRequest(prompt="icon", extra={"n": 2}))

        payload = post.call_args.kwargs["json"]
        assert payload["n"] == 2

    @pytest.mark.asyncio
    async def test_generate_decodes_json_content_b64_json(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={"model": "grok-imagine-image-quality"},
        ))
        await provider.initialize()

        response = _mock_response({
            "choices": [{
                "message": {
                    "content": '{"b64_json": "' + _png_b64(8, 8) + '", "seed": 77}'
                }
            }],
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response):
            results = await provider.generate(GenerateRequest(prompt="icon", seed=77))

        assert len(results) == 1
        assert results[0].seed == 77
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (8, 8)

    @pytest.mark.asyncio
    async def test_generate_decodes_sse_chat_completion_chunks(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={"model": "grok-imagine-image-quality"},
        ))
        await provider.initialize()

        image_b64 = _png_b64(13, 9)
        sse_text = "\n\n".join([
            "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "data:image/png;base64,"}}]}),
            "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": image_b64}}]}),
            "data: [DONE]",
        ])
        response = _mock_response(
            {},
            text=sse_text,
            headers={"Content-Type": "text/event-stream"},
            json_error=True,
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response):
            results = await provider.generate(GenerateRequest(prompt="icon"))

        assert len(results) == 1
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (13, 9)

    @pytest.mark.asyncio
    async def test_generate_surfaces_sse_provider_error(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={"model": "grok-imagine-image-lite"},
        ))
        await provider.initialize()

        sse_text = "\n\n".join([
            "data: " + json.dumps({"error": {"message": "upstream task failed"}}),
            "data: [DONE]",
        ])
        response = _mock_response(
            {},
            text=sse_text,
            headers={"Content-Type": "text/event-stream"},
            json_error=True,
        )

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response):
            with pytest.raises(ProviderError, match="upstream task failed"):
                await provider.generate(GenerateRequest(prompt="icon"))

    @pytest.mark.asyncio
    async def test_generate_decodes_nested_chat_result_base64(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={"model": "grok-imagine-image-quality"},
        ))
        await provider.initialize()

        response = _mock_response({
            "choices": [{
                "message": {
                    "content": {
                        "artifact": {
                            "result": _png_b64(10, 7),
                        },
                    },
                }
            }],
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response):
            results = await provider.generate(GenerateRequest(prompt="icon"))

        assert len(results) == 1
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (10, 7)

    @pytest.mark.asyncio
    async def test_generate_downloads_bare_http_image_url_from_chat_text(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={"model": "grok-imagine-image-quality"},
        ))
        await provider.initialize()

        response = _mock_response({
            "choices": [{
                "message": {"content": "Here is the image: https://cdn.example.com/generated.png"}
            }],
        })
        image_response = MagicMock()
        image_response.status_code = 200
        image_response.content = _png_bytes(19, 11)
        image_response.headers = {"Content-Type": "image/png"}

        with (
            patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response),
            patch.object(provider._client, "get", new_callable=AsyncMock, return_value=image_response),
        ):
            results = await provider.generate(GenerateRequest(prompt="icon"))

        assert len(results) == 1
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (19, 11)

    @pytest.mark.asyncio
    async def test_image_to_image_posts_chat_content_array_and_decodes_data_url(self):
        provider = OpenAIChatImageProvider(ProviderConfig(
            auth={"api_key": "test-key"},
            settings={
                "base_url": "https://proxy.example.com/v1",
                "model": "gemini-3.1-flash-image",
            },
        ))
        await provider.initialize()

        response = _mock_response({
            "choices": [{
                "message": {"content": f"data:image/png;base64,{_png_b64(18, 14)}"}
            }],
        })

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=response) as post:
            results = await provider.image_to_image(ImageToImageRequest(
                images=[_png_bytes(48, 48)],
                prompt="redraw as game portrait",
                negative_prompt="blur",
                width=768,
                height=1024,
                seed=55,
                extra={"temperature": 0.1},
            ))

        assert len(results) == 1
        assert results[0].provider_name == "openai_chat_image"
        assert results[0].generation_params["mode"] == "image_to_image"
        assert results[0].generation_params["reference_image_count"] == 1
        assert Image.open(io.BytesIO(results[0].image_bytes)).size == (18, 14)

        assert post.call_args.args[0] == "https://proxy.example.com/v1/chat/completions"
        payload = post.call_args.kwargs["json"]
        content = payload["messages"][-1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert "redraw as game portrait" in content[0]["text"]
        assert "768x1024" in content[0]["text"]
        assert "Negative prompt: blur." in content[0]["text"]
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "n" not in payload
        assert payload["temperature"] == 0.1

    @pytest.mark.asyncio
    async def test_supports_generate_and_image_to_image_not_inpaint(self):
        provider = OpenAIChatImageProvider(ProviderConfig())
        assert provider.supports(Capability.GENERATE)
        assert provider.supports(Capability.IMAGE_TO_IMAGE)
        assert not provider.supports(Capability.INPAINT)


class TestOpenAICompatibleRouting:
    @pytest.mark.asyncio
    async def test_service_routes_to_gemini_chat_image_alias(self):
        cfg = GatewayConfig(
            default_provider=DefaultProviderConfig(generate="gemini_chat_image"),
            providers={
                "gemini_chat_image": ProviderConfig(
                    enabled=True,
                    auth={"api_key": "test-key"},
                    settings={"model": "gemini-3.1-flash-image"},
                ),
            },
        )
        response = _mock_response({
            "choices": [{
                "message": {"content": f"data:image/png;base64,{_png_b64(9, 9)}"}
            }],
        })

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response):
            async with ImageService(cfg) as svc:
                result = await svc.generate(GenerateRequest(prompt="route check"))

        assert result.success_count == 1
        assert result.results[0].provider_name == "gemini_chat_image"

    @pytest.mark.asyncio
    async def test_service_routes_image_to_image_default_provider(self):
        cfg = GatewayConfig(
            default_provider=DefaultProviderConfig(image_to_image="openai_images"),
            providers={
                "openai_images": ProviderConfig(
                    enabled=True,
                    auth={"api_key": "test-key"},
                    settings={"model": "gpt-image-2"},
                ),
            },
        )
        response = _mock_response({"data": [{"b64_json": _png_b64(11, 11)}]})

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response):
            async with ImageService(cfg) as svc:
                result = await svc.image_to_image(ImageToImageRequest(
                    images=[_png_bytes()],
                    prompt="route image to image",
                ))

        assert result.success_count == 1
        assert result.results[0].provider_name == "openai_images"

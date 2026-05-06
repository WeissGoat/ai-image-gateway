"""NovelAI Provider 单元测试 (Mock HTTP, 不调真实 API)。"""

import io
import json
import zipfile
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from PIL import Image

from ai_image_gateway.config import GatewayConfig, ProviderConfig
from ai_image_gateway.providers.novelai import (
    NovelAIProvider,
    _argon_hash,
    _calculate_resolution,
    _get_access_key,
    _image_to_base64,
)
from ai_image_gateway.schema import Capability, GenerateRequest, InpaintRequest
from ai_image_gateway.errors import ProviderError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_zip_png(width: int = 64, height: int = 64) -> bytes:
    """Create a zip file containing a single PNG, mimicking NAI response."""
    img = Image.new("RGBA", (width, height), (100, 50, 200, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("image_0.png", png_bytes)
    return zip_buf.getvalue()


def _make_provider(access_token: str = "test_token_123") -> NovelAIProvider:
    """Create a NovelAIProvider with test config."""
    config = ProviderConfig(
        enabled=True,
        auth={"access_token": access_token},
        settings={
            "model": "nai-diffusion-4-5-full",
            "sampler": "k_euler",
            "steps": 28,
            "cfg": 5.0,
            "timeout": 30,
            "retry": 1,
        },
    )
    return NovelAIProvider(config)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

class TestAuthHelpers:
    def test_calculate_resolution(self):
        w, h = _calculate_resolution(832 * 1216, (832, 1216))
        assert w % 64 == 0
        assert h % 64 == 0
        assert w == 832
        assert h == 1216

    def test_image_to_base64(self):
        img = Image.new("RGBA", (64, 64), "red")
        b64 = _image_to_base64(img)
        assert isinstance(b64, str)
        assert len(b64) > 0


# ---------------------------------------------------------------------------
# Provider lifecycle
# ---------------------------------------------------------------------------

class TestNovelAIProviderInit:
    @pytest.mark.asyncio
    async def test_init_with_token(self):
        provider = _make_provider()
        await provider.initialize()
        assert provider._access_token == "test_token_123"
        await provider.close()

    @pytest.mark.asyncio
    async def test_init_missing_auth(self):
        config = ProviderConfig(enabled=True, auth={}, settings={})
        provider = NovelAIProvider(config)
        with pytest.raises(ProviderError):
            await provider.initialize()


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------

class TestCapability:
    def test_supports(self):
        provider = _make_provider()
        assert provider.supports(Capability.GENERATE) is True
        assert provider.supports(Capability.INPAINT) is True
        assert provider.supports(Capability.UPSCALE) is False


# ---------------------------------------------------------------------------
# Generate (mocked HTTP)
# ---------------------------------------------------------------------------

class TestGenerate:
    @pytest.mark.asyncio
    async def test_generate_single(self):
        provider = _make_provider()
        await provider.initialize()

        fake_zip = _make_fake_zip_png(512, 512)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_zip

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_response):
            results = await provider.generate(GenerateRequest(
                prompt="test prompt, steampunk dagger",
                negative_prompt="lowres",
                width=512,
                height=512,
                count=1,
                seed=42,
            ))

        assert len(results) == 1
        assert results[0].provider_name == "novelai"
        assert results[0].seed == 42
        assert results[0].model_name == "nai-diffusion-4-5-full"

        # Verify output is valid PNG
        img = Image.open(io.BytesIO(results[0].image_bytes))
        assert img.size == (512, 512)

        await provider.close()

    @pytest.mark.asyncio
    async def test_generate_multi(self):
        provider = _make_provider()
        await provider.initialize()

        fake_zip = _make_fake_zip_png(256, 256)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_zip

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_response):
            results = await provider.generate(GenerateRequest(
                prompt="batch test",
                width=256,
                height=256,
                count=3,
                seed=100,
            ))

        assert len(results) == 3
        seeds = [r.seed for r in results]
        assert seeds == [100, 101, 102]

        await provider.close()

    @pytest.mark.asyncio
    async def test_generate_params_snapshot(self):
        provider = _make_provider()
        await provider.initialize()

        fake_zip = _make_fake_zip_png()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_zip

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            results = await provider.generate(GenerateRequest(
                prompt="params check",
                negative_prompt="bad quality",
                width=832,
                height=1216,
                count=1,
                seed=77,
            ))

        # Verify params in result
        params = results[0].generation_params
        assert params["prompt"] == "params check"
        assert params["negative_prompt"] == "bad quality"
        assert params["seed"] == 77
        assert params["action"] == "generate"

        # Verify the HTTP request payload
        call_args = mock_post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["model"] == "nai-diffusion-4-5-full"
        assert payload["action"] == "generate"
        assert payload["parameters"]["sampler"] == "k_euler"

        await provider.close()


class TestBuildParams:
    def test_v4_prompt_structure(self):
        provider = _make_provider()
        params = provider._build_params(
            width=832, height=1216,
            positive="test pos", negative="test neg",
            seed=1, steps=28, cfg=5.0,
            sampler="k_euler", scheduler="native",
            smea="none", model="nai-diffusion-4-5-full",
            extra={},
        )
        assert params["v4_prompt"]["caption"]["base_caption"] == "test pos"
        assert params["v4_negative_prompt"]["caption"]["base_caption"] == "test neg"
        assert params["ucPreset"] == 3
        assert params["params_version"] == 1

    def test_smea_enabled(self):
        provider = _make_provider()
        params = provider._build_params(
            width=832, height=1216,
            positive="", negative="",
            seed=1, steps=28, cfg=5.0,
            sampler="k_euler", scheduler="native",
            smea="SMEA+DYN", model="nai-diffusion-4-5-full",
            extra={},
        )
        assert params["sm"] is True
        assert params["sm_dyn"] is True

    def test_ddim_v3_fix(self):
        provider = _make_provider()
        params = provider._build_params(
            width=832, height=1216,
            positive="", negative="",
            seed=1, steps=28, cfg=5.0,
            sampler="ddim", scheduler="native",
            smea="none", model="nai-diffusion-4-5-full",
            extra={},
        )
        assert params["sampler"] == "ddim_v3"
        assert params["sm"] is False

"""NovelAI Provider 单元测试 (Mock HTTP, 不调真实 API)。"""

import base64
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
    _mask_to_novelai_inpaint_base64,
    _novelai_inpaint_model,
    _prepare_inpaint_source_image,
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


def _make_png_bytes(width: int = 64, height: int = 64, color=(100, 50, 200, 255)) -> bytes:
    img = Image.new("RGBA", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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


def _multipart_request_json(call) -> dict:
    files = call.kwargs["files"]
    for field_name, file_tuple in files:
        if field_name == "request":
            return json.loads(file_tuple[1].decode("utf-8"))
    raise AssertionError("multipart request part not found")


def _multipart_png(call, field: str) -> Image.Image:
    files = call.kwargs["files"]
    for field_name, file_tuple in files:
        if field_name == field:
            return Image.open(io.BytesIO(file_tuple[1]))
    raise AssertionError(f"multipart {field} part not found")


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

    def test_prepare_inpaint_source_image_preserves_alpha_by_default(self):
        img = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
        img.putpixel((1, 0), (20, 40, 60, 255))

        prepared = _prepare_inpaint_source_image(img)

        assert prepared.mode == "RGBA"
        assert prepared.getpixel((0, 0)) == (0, 0, 0, 0)
        assert prepared.getpixel((1, 0)) == (20, 40, 60, 255)

    def test_prepare_inpaint_source_image_can_flatten_alpha_to_rgb(self):
        img = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
        img.putpixel((1, 0), (20, 40, 60, 255))

        prepared = _prepare_inpaint_source_image(img, flatten_alpha=True)

        assert prepared.mode == "RGB"
        assert prepared.getpixel((0, 0)) == (255, 255, 255)
        assert prepared.getpixel((1, 0)) == (20, 40, 60)

    def test_mask_to_novelai_inpaint_base64_uses_binary_full_size_mask(self):
        mask = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        for x in range(120, 136):
            for y in range(120, 136):
                mask.putpixel((x, y), (0, 0, 0, 255))
        buf = io.BytesIO()
        mask.save(buf, format="PNG")

        encoded = _mask_to_novelai_inpaint_base64(buf.getvalue(), (256, 256))
        decoded = Image.open(io.BytesIO(base64.b64decode(encoded)))

        # Output is RGBA (matching ComfyUI naimask_to_base64)
        assert decoded.mode == "RGBA"
        assert decoded.size == (256, 256)
        # Black region: alpha should be 0
        assert decoded.getpixel((0, 0))[3] == 0
        # White mask region around (128,128): should have alpha=255
        assert decoded.getpixel((128, 128))[3] == 255

    def test_novelai_inpaint_model_matches_anr_names(self):
        assert _novelai_inpaint_model("nai-diffusion-4-5-full") == "nai-diffusion-4-5-full-inpainting"
        assert _novelai_inpaint_model("nai-diffusion-4-5-curated") == "nai-diffusion-4-5-curated-inpainting"
        assert _novelai_inpaint_model("nai-diffusion-4-curated-preview") == "nai-diffusion-4-curated-inpainting"
        assert _novelai_inpaint_model("nai-diffusion-3") == "nai-diffusion-3-inpainting"
        assert _novelai_inpaint_model("nai-diffusion-2") == "nai-diffusion-2"


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
    async def test_init_with_client_py_fallback(self, monkeypatch, tmp_path):
        client_py = tmp_path / "client.py"
        client_py.write_text(
            "class NAIClient:\n"
            "    async def get_access_token(self):\n"
            "        return 'pst-from-client'\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("NAI_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("NAI_CLIENT_PY", str(client_py))

        config = ProviderConfig(
            enabled=True,
            auth={"access_token": "${NAI_ACCESS_TOKEN}"},
            settings={},
        )
        provider = NovelAIProvider(config)
        await provider.initialize()
        assert provider._access_token == "pst-from-client"
        await provider.close()

    @pytest.mark.asyncio
    async def test_init_missing_auth(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NAI_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("NAI_CLIENT_PY", str(tmp_path / "missing_client.py"))
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


class TestInpaint:
    @pytest.mark.asyncio
    async def test_inpaint_payload_and_result_snapshot(self):
        provider = _make_provider()
        await provider.initialize()

        fake_zip = _make_fake_zip_png(512, 768)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_zip

        source = _make_png_bytes(512, 768, (20, 30, 40, 255))
        mask = _make_png_bytes(512, 768, (255, 255, 255, 255))

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            results = await provider.inpaint(InpaintRequest(
                image=source,
                mask=mask,
                prompt="recover hidden hair",
                negative_prompt="identity drift",
                count=2,
                seed=12,
                extra={
                    "strength": 0.55,
                    "noise": 0.12,
                    "inpaint_i2i_strength": 0.82,
                },
            ))

        assert len(results) == 2
        assert [r.seed for r in results] == [12, 13]
        assert results[0].model_name == "nai-diffusion-4-5-full-inpainting"
        assert results[0].generation_params["action"] == "infill"
        assert results[0].generation_params["negative_prompt"] == "identity drift"
        assert results[0].generation_params["add_original_image"] is False
        assert results[0].generation_params["strength"] == 0.55
        assert results[0].generation_params["noise"] == 0.12
        assert results[0].generation_params["inpaint_i2i_strength"] == 0.82

        first_payload = _multipart_request_json(mock_post.call_args_list[0])
        assert first_payload["model"] == "nai-diffusion-4-5-full-inpainting"
        assert first_payload["action"] == "infill"
        assert first_payload["parameters"]["seed"] == 12
        assert first_payload["parameters"]["add_original_image"] is False
        assert first_payload["parameters"]["params_version"] == 3
        assert first_payload["parameters"]["strength"] == 0.55
        assert first_payload["parameters"]["noise"] == 0.12
        assert first_payload["parameters"]["inpaintImg2ImgStrength"] == 0.82
        assert first_payload["parameters"]["extra_noise_seed"] == 12
        assert first_payload["parameters"]["color_correct"] is False
        assert first_payload["parameters"]["image"] == "image"
        assert first_payload["parameters"]["mask"] == "mask"

        mask_img = _multipart_png(mock_post.call_args_list[0], "mask")
        # V4 mask is RGBA and quantized to latent grid
        assert mask_img.mode == "RGBA"

        await provider.close()

    @pytest.mark.asyncio
    async def test_inpaint_payload_image_mask_and_params_share_limited_size(self):
        provider = _make_provider()
        await provider.initialize()

        fake_zip = _make_fake_zip_png(832, 1216)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_zip

        source = _make_png_bytes(1024, 1536, (20, 30, 40, 128))
        mask = _make_png_bytes(1024, 1536, (255, 255, 255, 255))

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            await provider.inpaint(InpaintRequest(
                image=source,
                mask=mask,
                prompt="recover hidden hair",
                count=1,
                seed=44,
            ))

        payload = _multipart_request_json(mock_post.call_args)
        params = payload["parameters"]
        assert (params["width"], params["height"]) == (832, 1216)

        assert params["image"] == "image"
        assert params["mask"] == "mask"

        image_img = _multipart_png(mock_post.call_args, "image")
        assert image_img.mode == "RGBA"
        assert image_img.size == (832, 1216)

        mask_img = _multipart_png(mock_post.call_args, "mask")
        # V4 mask is now RGBA (matching ComfyUI naimask_to_base64)
        assert mask_img.mode == "RGBA"

        await provider.close()

    @pytest.mark.asyncio
    async def test_inpaint_accepts_already_suffixed_inpainting_model(self):
        provider = _make_provider()
        await provider.initialize()

        fake_zip = _make_fake_zip_png(512, 512)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_zip

        source = _make_png_bytes(512, 512)
        mask = _make_png_bytes(512, 512)

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            results = await provider.inpaint(InpaintRequest(
                image=source,
                mask=mask,
                prompt="repair edge",
                seed=33,
                extra={
                    "model": "nai-diffusion-4-5-full-inpainting",
                    "inpaint_strength": 0.7,
                },
            ))

        payload = _multipart_request_json(mock_post.call_args)
        assert payload["model"] == "nai-diffusion-4-5-full-inpainting"
        assert payload["parameters"]["inpaintImg2ImgStrength"] == 0.7
        assert results[0].model_name == "nai-diffusion-4-5-full-inpainting"
        assert results[0].generation_params["base_model"] == "nai-diffusion-4-5-full"

        await provider.close()

    @pytest.mark.asyncio
    async def test_inpaint_v2_does_not_append_inpainting_suffix(self):
        provider = _make_provider()
        await provider.initialize()

        fake_zip = _make_fake_zip_png(512, 512)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_zip

        source = _make_png_bytes(512, 512)
        mask = _make_png_bytes(512, 512)

        with patch.object(provider._client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            results = await provider.inpaint(InpaintRequest(
                image=source,
                mask=mask,
                prompt="repair edge",
                seed=22,
                extra={"model": "nai-diffusion-2"},
            ))

        payload = _multipart_request_json(mock_post.call_args)
        assert payload["model"] == "nai-diffusion-2"
        assert results[0].model_name == "nai-diffusion-2"

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
        # V4 model should now use params_version=3
        assert params["params_version"] == 3

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

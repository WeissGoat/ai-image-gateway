"""MockProvider 端到端测试。"""

import pytest
from PIL import Image
import io

from ai_image_gateway import ImageService, GenerateRequest
from ai_image_gateway.config import GatewayConfig
from ai_image_gateway.schema import InpaintRequest


def _make_default_service() -> ImageService:
    """创建仅含 MockProvider 的 Service。"""
    return ImageService(GatewayConfig())


class TestMockProviderGenerate:
    @pytest.mark.asyncio
    async def test_single_generate(self):
        async with _make_default_service() as svc:
            result = await svc.generate(GenerateRequest(
                prompt="test icon, rusty dagger",
                width=256,
                height=256,
                count=1,
            ))
            assert len(result.errors) == 0
            assert result.success_count == 1
            img_data = result.results[0]
            assert img_data.provider_name == "mock"
            assert img_data.seed is not None

            # 验证输出是合法 PNG
            img = Image.open(io.BytesIO(img_data.image_bytes))
            assert img.size == (256, 256)
            assert img.mode == "RGBA"

    @pytest.mark.asyncio
    async def test_batch_generate(self):
        async with _make_default_service() as svc:
            result = await svc.generate(GenerateRequest(
                prompt="batch test",
                width=128,
                height=128,
                count=4,
            ))
            assert result.success_count == 4
            seeds = [r.seed for r in result.results]
            assert len(set(seeds)) == 4  # 种子不重复

    @pytest.mark.asyncio
    async def test_seed_determinism(self):
        async with _make_default_service() as svc:
            result = await svc.generate(GenerateRequest(
                prompt="seed test",
                width=64,
                height=64,
                count=2,
                seed=42,
            ))
            assert result.results[0].seed == 42
            assert result.results[1].seed == 43

    @pytest.mark.asyncio
    async def test_generation_params_snapshot(self):
        async with _make_default_service() as svc:
            result = await svc.generate(GenerateRequest(
                prompt="params test",
                negative_prompt="text, watermark",
                width=512,
                height=512,
            ))
            params = result.results[0].generation_params
            assert params["prompt"] == "params test"
            assert params["negative_prompt"] == "text, watermark"
            assert params["width"] == 512


class TestMockProviderInpaint:
    @pytest.mark.asyncio
    async def test_inpaint(self):
        async with _make_default_service() as svc:
            result = await svc.inpaint(InpaintRequest(
                image=b"fake_image",
                mask=b"fake_mask",
                prompt="fix this area",
                width=256,
                height=256,
            ))
            assert result.success_count == 1
            assert result.results[0].provider_name == "mock"


class TestBatchGenerate:
    @pytest.mark.asyncio
    async def test_batch_multiple_requests(self):
        async with _make_default_service() as svc:
            requests = [
                GenerateRequest(prompt=f"item_{i}", width=64, height=64, count=1)
                for i in range(3)
            ]
            results = await svc.batch_generate(
                requests, delay_seconds=0.0
            )
            assert len(results) == 3
            for r in results:
                assert r.success_count == 1
                assert len(r.errors) == 0


class TestServiceMeta:
    @pytest.mark.asyncio
    async def test_available_providers(self):
        async with _make_default_service() as svc:
            providers = svc.available_providers
            assert "mock" in providers

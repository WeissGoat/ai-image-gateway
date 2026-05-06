"""schema 数据模型测试。"""

import pytest
from ai_image_gateway.schema import (
    BatchResult,
    Capability,
    GenerateRequest,
    ImageFormat,
    ImageResult,
    InpaintRequest,
)


class TestGenerateRequest:
    def test_defaults(self):
        req = GenerateRequest(prompt="test")
        assert req.width == 512
        assert req.height == 512
        assert req.count == 1
        assert req.seed is None
        assert req.provider is None
        assert req.output_format == ImageFormat.PNG
        assert req.negative_prompt == ""
        assert req.extra == {}

    def test_custom_values(self):
        req = GenerateRequest(
            prompt="game icon, dagger",
            negative_prompt="text, watermark",
            width=1024,
            height=1024,
            count=4,
            seed=12345,
            provider="novelai",
            extra={"sampler": "euler"},
        )
        assert req.width == 1024
        assert req.count == 4
        assert req.seed == 12345
        assert req.provider == "novelai"
        assert req.extra["sampler"] == "euler"

    def test_count_validation(self):
        with pytest.raises(Exception):
            GenerateRequest(prompt="test", count=0)
        with pytest.raises(Exception):
            GenerateRequest(prompt="test", count=20)


class TestImageResult:
    def test_basic(self):
        result = ImageResult(
            image_bytes=b"fake_png",
            seed=42,
            provider_name="mock",
            model_name="mock-v1",
            cost=0.5,
        )
        assert result.seed == 42
        assert result.provider_name == "mock"
        assert result.cost == 0.5


class TestBatchResult:
    def test_total_cost(self):
        batch = BatchResult(
            results=[
                ImageResult(image_bytes=b"a", provider_name="mock", cost=1.0),
                ImageResult(image_bytes=b"b", provider_name="mock", cost=2.5),
            ]
        )
        assert batch.total_cost == 3.5
        assert batch.success_count == 2

    def test_empty(self):
        batch = BatchResult()
        assert batch.total_cost == 0.0
        assert batch.success_count == 0
        assert batch.request_id  # auto-generated


class TestCapability:
    def test_enum_values(self):
        assert Capability.GENERATE == "generate"
        assert Capability.INPAINT == "inpaint"
        assert Capability.UPSCALE == "upscale"

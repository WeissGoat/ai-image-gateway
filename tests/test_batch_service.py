import pytest

from ai_image_gateway.config import GatewayConfig
from ai_image_gateway.contracts import GenerateRequest
from ai_image_gateway.facade.batch_service import BatchService
from ai_image_gateway.facade.image_service import ImageService


@pytest.mark.asyncio
async def test_batch_service_generates_multiple_requests():
    async with ImageService(GatewayConfig()) as image_service:
        batch_service = BatchService(image_service)
        requests = [
            GenerateRequest(prompt=f"item_{index}", width=64, height=64, count=1)
            for index in range(3)
        ]

        results = await batch_service.batch_generate(requests, delay_seconds=0.0)

    assert len(results) == 3
    for result in results:
        assert result.success_count == 1
        assert len(result.errors) == 0

from ai_image_gateway.contracts.common import BatchResult, Capability
from ai_image_gateway.contracts.generate import GenerateRequest, InpaintRequest
from ai_image_gateway.contracts.raw import NovelAIRawPayload, RetryRecord


def test_contract_modules_expose_expected_types():
    request = GenerateRequest(prompt="test")
    payload = NovelAIRawPayload(input="p", model="m", parameters={})
    record = RetryRecord(attempt=1, retryable=False)
    batch = BatchResult()

    assert request.prompt == "test"
    assert payload.model == "m"
    assert record.attempt == 1
    assert batch.success_count == 0
    assert Capability.GENERATE.value == "generate"


def test_generate_and_inpaint_contracts_are_available():
    generate = GenerateRequest(prompt="test")
    inpaint = InpaintRequest(image=b"i", mask=b"m", prompt="repair")

    assert generate.output_format.value == "png"
    assert inpaint.count == 1

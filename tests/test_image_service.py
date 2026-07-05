from ai_image_gateway.facade.image_service import ImageService
from ai_image_gateway.facade.batch_service import BatchService


def test_facade_modules_export_services():
    assert ImageService is not None
    assert BatchService is not None

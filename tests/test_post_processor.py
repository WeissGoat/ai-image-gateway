"""post_processor 测试。"""

from PIL import Image

from ai_image_gateway.processing.post_processor import (
    crop_to_aspect,
    fit_safe_padding,
    from_bytes,
    resize,
    to_bytes,
    trim_transparent,
)


class TestResize:
    def test_resize(self):
        img = Image.new("RGBA", (1024, 1024), "red")
        result = resize(img, 512, 512)
        assert result.size == (512, 512)


class TestCropToAspect:
    def test_wide_to_square(self):
        img = Image.new("RGB", (200, 100))
        result = crop_to_aspect(img, 1.0)
        assert result.size == (100, 100)

    def test_tall_to_landscape(self):
        img = Image.new("RGB", (100, 200))
        result = crop_to_aspect(img, 16 / 9)
        w, h = result.size
        assert abs(w / h - 16 / 9) < 0.1

    def test_already_correct(self):
        img = Image.new("RGB", (160, 90))
        result = crop_to_aspect(img, 16 / 9)
        assert result.size == (160, 90)


class TestTrimTransparent:
    def test_trim(self):
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        # 在中间画一个小方块
        for x in range(40, 60):
            for y in range(40, 60):
                img.putpixel((x, y), (255, 0, 0, 255))
        result = trim_transparent(img)
        assert result.size == (20, 20)

    def test_trim_with_padding(self):
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        for x in range(40, 60):
            for y in range(40, 60):
                img.putpixel((x, y), (255, 0, 0, 255))
        result = trim_transparent(img, padding_percent=50)
        # 原 20x20 + 50% padding 每边 = 20+20 = 40x40
        assert result.size[0] > 20
        assert result.size[1] > 20

    def test_non_rgba_passthrough(self):
        img = Image.new("RGB", (100, 100), "red")
        result = trim_transparent(img)
        assert result.size == (100, 100)


class TestFitSafePadding:
    def test_fit(self):
        img = Image.new("RGBA", (200, 100), "blue")
        result = fit_safe_padding(img, 512, 512, padding_percent=10)
        assert result.size == (512, 512)


class TestSerialize:
    def test_roundtrip(self):
        img = Image.new("RGBA", (64, 64), "green")
        data = to_bytes(img, "PNG")
        restored = from_bytes(data)
        assert restored.size == (64, 64)

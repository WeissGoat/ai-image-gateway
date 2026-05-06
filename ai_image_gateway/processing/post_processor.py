"""
图片后处理工具。

基于 Pillow 的通用后处理操作，对应美术规范中 Spec.PostProcess 的处理步骤。
"""

from __future__ import annotations

import io
from typing import Literal

from PIL import Image


def resize(
    image: Image.Image,
    width: int,
    height: int,
    *,
    resample: int = Image.Resampling.LANCZOS,
) -> Image.Image:
    """缩放到指定尺寸。"""
    return image.resize((width, height), resample=resample)


def crop_to_aspect(
    image: Image.Image,
    target_ratio: float,
    *,
    anchor: Literal["center", "top", "bottom"] = "center",
) -> Image.Image:
    """
    按目标宽高比裁切。

    target_ratio = width / height, 例如 16/9 = 1.778
    """
    w, h = image.size
    current_ratio = w / h

    if abs(current_ratio - target_ratio) < 0.01:
        return image

    if current_ratio > target_ratio:
        # 图太宽，裁左右
        new_w = int(h * target_ratio)
        offset = (w - new_w) // 2
        return image.crop((offset, 0, offset + new_w, h))
    else:
        # 图太高，裁上下
        new_h = int(w / target_ratio)
        if anchor == "top":
            return image.crop((0, 0, w, new_h))
        elif anchor == "bottom":
            return image.crop((0, h - new_h, w, h))
        else:
            offset = (h - new_h) // 2
            return image.crop((0, offset, w, offset + new_h))


def trim_transparent(
    image: Image.Image,
    padding_percent: float = 0.0,
) -> Image.Image:
    """
    裁掉透明边缘，可选保留百分比边距。

    仅对 RGBA 模式有效。
    """
    if image.mode != "RGBA":
        return image

    bbox = image.getbbox()
    if bbox is None:
        return image

    cropped = image.crop(bbox)

    if padding_percent > 0:
        w, h = cropped.size
        pad_x = int(w * padding_percent / 100)
        pad_y = int(h * padding_percent / 100)
        padded = Image.new("RGBA", (w + pad_x * 2, h + pad_y * 2), (0, 0, 0, 0))
        padded.paste(cropped, (pad_x, pad_y))
        return padded

    return cropped


def fit_safe_padding(
    image: Image.Image,
    target_width: int,
    target_height: int,
    padding_percent: float = 10.0,
) -> Image.Image:
    """
    将主体缩放并居中到目标尺寸，保留安全边距。

    主体占 (100 - 2*padding_percent)% 的画布区域。
    """
    canvas = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))

    # 计算安全区域
    safe_w = int(target_width * (100 - 2 * padding_percent) / 100)
    safe_h = int(target_height * (100 - 2 * padding_percent) / 100)

    # 等比缩放到安全区域内
    img_w, img_h = image.size
    scale = min(safe_w / img_w, safe_h / img_h)
    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # 居中粘贴
    offset_x = (target_width - new_w) // 2
    offset_y = (target_height - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y), resized if resized.mode == "RGBA" else None)

    return canvas


def to_bytes(
    image: Image.Image,
    fmt: str = "PNG",
) -> bytes:
    """将 PIL Image 导出为字节。"""
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def from_bytes(data: bytes) -> Image.Image:
    """从字节加载 PIL Image。"""
    return Image.open(io.BytesIO(data))

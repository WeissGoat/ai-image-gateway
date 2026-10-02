from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from ...contracts import GenerateRequest

BASE_URL = "https://image.novelai.net"
AUTH_URL = "https://api.novelai.net"

MODELS = [
    "nai-diffusion-2",
    "nai-diffusion-furry-3",
    "nai-diffusion-3",
    "nai-diffusion-4-curated-preview",
    "nai-diffusion-4-full",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-5-full",
]

SAMPLERS = [
    "k_euler",
    "k_euler_ancestral",
    "k_dpmpp_2s_ancestral",
    "k_dpmpp_2m_sde",
    "k_dpmpp_2m",
    "k_dpmpp_sde",
    "ddim",
]

SCHEDULERS = ["native", "karras", "exponential", "polyexponential"]


@dataclass(slots=True)
class NovelAIDefaults:
    model: str = "nai-diffusion-4-5-full"
    sampler: str = "k_euler"
    scheduler: str = "native"
    steps: int = 28
    cfg: float = 5.0
    cfg_rescale: float = 0.0
    uncond_scale: float = 1.0
    uc_preset: int = 3
    smea: str = "none"
    variety: bool = False
    decrisper: bool = False
    limit_opus_free: bool = True

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> NovelAIDefaults:
        defaults = cls()
        return cls(
            model=settings.get("model", defaults.model),
            sampler=settings.get("sampler", defaults.sampler),
            scheduler=settings.get("scheduler", defaults.scheduler),
            steps=settings.get("steps", defaults.steps),
            cfg=settings.get("cfg", defaults.cfg),
            cfg_rescale=settings.get("cfg_rescale", defaults.cfg_rescale),
            uncond_scale=settings.get("uncond_scale", defaults.uncond_scale),
            uc_preset=settings.get("uc_preset", defaults.uc_preset),
            smea=settings.get("smea", defaults.smea),
            variety=settings.get("variety", defaults.variety),
            decrisper=settings.get("decrisper", defaults.decrisper),
            limit_opus_free=settings.get("limit_opus_free", defaults.limit_opus_free),
        )


def _image_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _calculate_resolution(pixel_count: int, aspect_ratio: tuple[int, int]) -> tuple[int, int]:
    pixel_count = pixel_count / 4096
    w, h = aspect_ratio
    k = (pixel_count * w / h) ** 0.5
    width = int(np.floor(k) * 64)
    height = int(np.floor(k * h / w) * 64)
    return width, height


def _calculate_skip_cfg_above_sigma(w: int, h: int) -> float:
    return (w * h / 1011712) ** 0.5 * 19


def _resize_image(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return img.resize(size, Image.Resampling.LANCZOS)


def _apply_opus_free_limits(
    width: int,
    height: int,
    steps: int,
    *,
    enabled: bool,
) -> tuple[int, int, int]:
    if not enabled:
        return width, height, steps

    pixel_limit = 1024 * 1024
    if width * height > pixel_limit:
        width, height = _calculate_resolution(pixel_limit, (width, height))
    if steps > 28:
        steps = 28
    return width, height, steps


def _pil_to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mask_b64_to_pil(mask_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(mask_b64)))


def _prepare_inpaint_source_image(
    img: Image.Image,
    background: tuple[int, int, int] = (255, 255, 255),
    *,
    flatten_alpha: bool = False,
) -> Image.Image:
    has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
    if flatten_alpha and has_alpha:
        rgba = img.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, (*background, 255))
        canvas.alpha_composite(rgba)
        return canvas.convert("RGB")
    if has_alpha:
        return img.convert("RGBA")
    return img.convert("RGB")


def _expand_binary_mask_to_anr_grid(binary: np.ndarray) -> np.ndarray:
    height, width = binary.shape
    if height % 8 != 0 or width % 8 != 0:
        return binary

    grid_height = height // 8
    grid_width = width // 8
    white_grids = np.zeros((grid_height, grid_width), dtype=bool)

    for i in range(grid_height):
        for j in range(grid_width):
            section = binary[i * 8:(i + 1) * 8, j * 8:(j + 1) * 8]
            if np.any(section > 0):
                white_grids[i, j] = True

    visited = np.zeros_like(white_grids, dtype=bool)
    result = binary.copy()

    def bfs(start_i: int, start_j: int) -> list[tuple[int, int]]:
        region: list[tuple[int, int]] = []
        queue = [(start_i, start_j)]
        visited[start_i, start_j] = True

        while queue:
            i, j = queue.pop(0)
            region.append((i, j))
            for di, dj in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                ni, nj = i + di, j + dj
                if (
                    0 <= ni < grid_height
                    and 0 <= nj < grid_width
                    and white_grids[ni, nj]
                    and not visited[ni, nj]
                ):
                    visited[ni, nj] = True
                    queue.append((ni, nj))
        return region

    for i in range(grid_height):
        for j in range(grid_width):
            if not white_grids[i, j] or visited[i, j]:
                continue

            region = bfs(i, j)
            region_i = [pos[0] for pos in region]
            region_j = [pos[1] for pos in region]
            min_i, max_i = min(region_i), max(region_i)
            min_j, max_j = min(region_j), max(region_j)

            top_distance = min_i
            bottom_distance = grid_height - 1 - max_i
            left_distance = min_j
            right_distance = grid_width - 1 - max_j

            target_top = (top_distance // 8) * 8
            target_bottom = (bottom_distance // 8) * 8
            target_left = (left_distance // 8) * 8
            target_right = (right_distance // 8) * 8

            expanded_min_i = max(0, min_i - (top_distance - target_top))
            expanded_max_i = min(grid_height - 1, max_i + (bottom_distance - target_bottom))
            expanded_min_j = max(0, min_j - (left_distance - target_left))
            expanded_max_j = min(grid_width - 1, max_j + (right_distance - target_right))

            brush_size_grid = 4
            brush_half = brush_size_grid // 2
            for center_i in range(expanded_min_i, expanded_max_i + 1):
                for center_j in range(expanded_min_j, expanded_max_j + 1):
                    brush_start_i = max(0, center_i - brush_half)
                    brush_end_i = min(grid_height, center_i + brush_half)
                    brush_start_j = max(0, center_j - brush_half)
                    brush_end_j = min(grid_width, center_j + brush_half)

                    overlaps_region = any(
                        brush_start_i <= pos[0] < brush_end_i
                        and brush_start_j <= pos[1] < brush_end_j
                        for pos in region
                    )
                    if overlaps_region:
                        result[
                            brush_start_i * 8:brush_end_i * 8,
                            brush_start_j * 8:brush_end_j * 8,
                        ] = 255

    return result


def _mask_to_novelai_inpaint_base64(
    mask_bytes: bytes,
    target_size: tuple[int, int],
    *,
    is_v4: bool = False,
) -> str:
    raw_mask = Image.open(io.BytesIO(mask_bytes))
    rgba = raw_mask.convert("RGBA")
    alpha = np.array(rgba.getchannel("A"))
    rgb_luma = np.array(rgba.convert("L"))
    if alpha.min() < 255:
        binary = (alpha > 0).astype(np.uint8) * 255
    else:
        binary = (rgb_luma > 128).astype(np.uint8) * 255

    w, h = target_size
    grid_w = int(np.ceil(w / 64) * 8)
    grid_h = int(np.ceil(h / 64) * 8)
    mask = Image.fromarray(binary, "L")
    mask = mask.resize((grid_w, grid_h), Image.Resampling.NEAREST)
    if is_v4:
        mask = mask.resize((grid_w * 8, grid_h * 8), Image.Resampling.NEAREST)
    else:
        mask = mask.resize(target_size, Image.Resampling.NEAREST)

    binary = np.array(mask).astype(np.uint8)
    binary = _expand_binary_mask_to_anr_grid(binary)

    rgb = np.stack([binary, binary, binary], axis=-1)
    alpha_ch = (binary > 0).astype(np.uint8) * 255
    rgba_out = np.dstack((rgb, alpha_ch))
    img = Image.fromarray(rgba_out, "RGBA")
    return _image_to_base64(img)


def _novelai_inpaint_model(model: str) -> str:
    if model.endswith("-inpainting") or "nai-diffusion-2" in model:
        return model
    if model == "nai-diffusion-4-curated-preview":
        return "nai-diffusion-4-curated-inpainting"
    return f"{model}-inpainting"


def build_params(
    *,
    width: int,
    height: int,
    positive: str,
    negative: str,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    smea: str,
    model: str,
    extra: dict[str, Any],
    defaults: NovelAIDefaults,
) -> dict[str, Any]:
    is_v4_model = "4" in model
    params: dict[str, Any] = {
        "params_version": 3 if is_v4_model else 1,
        "width": width,
        "height": height,
        "scale": cfg,
        "sampler": sampler,
        "steps": steps,
        "seed": seed,
        "n_samples": 1,
        "ucPreset": defaults.uc_preset,
        "qualityToggle": False,
        "sm": (smea in ("SMEA", "SMEA+DYN")) and sampler != "ddim",
        "sm_dyn": (smea == "SMEA+DYN") and sampler != "ddim",
        "dynamic_thresholding": defaults.decrisper,
        "controlnet_strength": 1.0,
        "legacy": False,
        "add_original_image": False,
        "cfg_rescale": defaults.cfg_rescale,
        "noise_schedule": scheduler,
        "legacy_v3_extend": False,
        "uncond_scale": defaults.uncond_scale,
        "negative_prompt": negative,
        "prompt": positive,
        "reference_image_multiple": [],
        "reference_information_extracted_multiple": [],
        "reference_strength_multiple": [],
        "characterPrompts": [],
        "extra_noise_seed": seed,
        "v4_prompt": {
            "use_coords": False,
            "use_order": False,
            "caption": {"base_caption": positive, "char_captions": []},
        },
        "v4_negative_prompt": {
            "use_coords": False,
            "use_order": False,
            "caption": {"base_caption": negative, "char_captions": []},
        },
    }

    if sampler == "k_euler_ancestral" and scheduler != "native":
        params["deliberate_euler_ancestral_bug"] = False
        params["prefer_brownian"] = True

    if sampler == "ddim" and "nai-diffusion-2" not in model:
        params["sampler"] = "ddim_v3"

    params["width"], params["height"], params["steps"] = _apply_opus_free_limits(
        params["width"],
        params["height"],
        params["steps"],
        enabled=defaults.limit_opus_free,
    )

    if defaults.variety or extra.get("variety", False):
        params["skip_cfg_above_sigma"] = _calculate_skip_cfg_above_sigma(
            params["width"], params["height"]
        )

    return params


def generation_dimensions(request: GenerateRequest) -> tuple[int, int]:
    return _calculate_resolution(
        request.width * request.height,
        (request.width, request.height),
    )

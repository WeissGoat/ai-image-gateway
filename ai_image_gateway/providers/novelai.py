"""
NovelAI Provider — 接入 NovelAI 图片生成 API。

API 逻辑提取自 ComfyUI_NAIDGenerator 项目，去除 ComfyUI/torch 依赖，
仅使用 httpx + Pillow 实现纯 HTTP 调用。

支持能力:
  - generate: txt2img / img2img
  - inpaint: infill (局部重绘)
  - augment: bg-removal / lineart / sketch / colorize / emotion / declutter

认证方式 (优先级):
  1. config.auth.access_token / NAI_ACCESS_TOKEN  — 直接提供 token
  2. NAI_CLIENT_PY 或 P3 本机 client.py — 解析 get_access_token() 字面量返回
  3. config.auth.access_key    — 提供 access_key，自动 login
  4. config.auth.username + config.auth.password — 自动 hash + login
"""

from __future__ import annotations

import base64
import io
import json
import random
import zipfile
from hashlib import blake2b
from typing import Any

import httpx
import numpy as np
from loguru import logger
from PIL import Image

from ..auth import resolve_novelai_access_token
from ..errors import ProviderError, RateLimitError
from ..schema import (
    Capability,
    GenerateRequest,
    ImageResult,
    InpaintRequest,
)
from .base import BaseImageProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://image.novelai.net"
AUTH_URL = "https://api.novelai.net"
USER_URL = "https://api.novelai.net"

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


# ---------------------------------------------------------------------------
# Auth helpers (cherry-picked from novelai_api, no external dependency)
# ---------------------------------------------------------------------------

def _argon_hash(email: str, password: str, size: int, domain: str) -> str:
    """Argon2id hash for NovelAI authentication."""
    try:
        import argon2.low_level
    except ImportError:
        raise ProviderError(
            "novelai",
            "argon2-cffi is required for username/password auth. "
            "Install with: pip install argon2-cffi"
        )
    pre_salt = f"{password[:6]}{email}{domain}"
    blake = blake2b(digest_size=16)
    blake.update(pre_salt.encode())
    salt = blake.digest()
    raw = argon2.low_level.hash_secret_raw(
        password.encode(), salt,
        time_cost=2,
        memory_cost=int(2000000 / 1024),
        parallelism=1,
        hash_len=size,
        type=argon2.low_level.Type.ID,
    )
    return base64.urlsafe_b64encode(raw).decode()


def _get_access_key(email: str, password: str) -> str:
    return _argon_hash(email, password, 64, "novelai_data_access_key")[:64]


# ---------------------------------------------------------------------------
# Image utility helpers (Pillow-only, no torch)
# ---------------------------------------------------------------------------

def _image_to_base64(img: Image.Image) -> str:
    """PIL Image -> base64 encoded PNG string."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _bytes_to_pil(data: bytes) -> Image.Image:
    """bytes -> PIL Image."""
    return Image.open(io.BytesIO(data))


def _pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    """PIL Image -> bytes."""
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _calculate_resolution(pixel_count: int, aspect_ratio: tuple[int, int]) -> tuple[int, int]:
    """Calculate NAI-compatible resolution (multiples of 64)."""
    pixel_count = pixel_count / 4096
    w, h = aspect_ratio
    k = (pixel_count * w / h) ** 0.5
    width = int(np.floor(k) * 64)
    height = int(np.floor(k * h / w) * 64)
    return width, height


def _calculate_skip_cfg_above_sigma(w: int, h: int) -> float:
    return (w * h / 1011712) ** 0.5 * 19


def _resize_image(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize PIL image to target (width, height)."""
    return img.resize(size, Image.Resampling.LANCZOS)


def _apply_opus_free_limits(
    width: int,
    height: int,
    steps: int,
    *,
    enabled: bool,
) -> tuple[int, int, int]:
    """Apply NovelAI free-tier size and step limits before encoding payloads."""
    if not enabled:
        return width, height, steps

    pixel_limit = 1024 * 1024
    if width * height > pixel_limit:
        width, height = _calculate_resolution(pixel_limit, (width, height))
    if steps > 28:
        steps = 28
    return width, height, steps


def _pil_to_png_bytes(img: Image.Image) -> bytes:
    """Encode a PIL Image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mask_b64_to_pil(mask_b64: str) -> Image.Image:
    """Decode a base64-encoded PNG back to a PIL Image."""
    return Image.open(io.BytesIO(base64.b64decode(mask_b64)))


def _prepare_inpaint_source_image(
    img: Image.Image,
    background: tuple[int, int, int] = (255, 255, 255),
    *,
    flatten_alpha: bool = False,
) -> Image.Image:
    """Return a PNG-ready source image for NovelAI inpaint."""
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
    """Match Auto-NovelAI-Refactor's 8px-grid mask expansion."""
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
    """Convert a user mask to the full-size binary PNG used by NAI inpaint.

    For V4 models the mask is quantized to the latent grid (1/8 shrink then
    nearest-neighbour 8× upscale) matching ComfyUI_NAIDGenerator's
    ``resize_to_naimask`` behaviour.  The output is an RGBA PNG (white mask
    region with alpha=255, black=transparent) matching ComfyUI's
    ``naimask_to_base64``.
    """
    raw_mask = Image.open(io.BytesIO(mask_bytes))
    rgba = raw_mask.convert("RGBA")
    alpha = np.array(rgba.getchannel("A"))
    rgb_luma = np.array(rgba.convert("L"))
    if alpha.min() < 255:
        binary = (alpha > 0).astype(np.uint8) * 255
    else:
        binary = (rgb_luma > 128).astype(np.uint8) * 255

    w, h = target_size
    # Quantize to 1/8 latent grid (ceil(dim/64)*8) — matches ComfyUI
    grid_w = int(np.ceil(w / 64) * 8)
    grid_h = int(np.ceil(h / 64) * 8)
    mask = Image.fromarray(binary, "L")
    mask = mask.resize((grid_w, grid_h), Image.Resampling.NEAREST)
    if is_v4:
        # V4: scale back to full-size via nearest so each latent cell is a
        # uniform 8×8 block — this is what the NAI backend expects.
        mask = mask.resize((grid_w * 8, grid_h * 8), Image.Resampling.NEAREST)
    else:
        mask = mask.resize(target_size, Image.Resampling.NEAREST)

    binary = np.array(mask).astype(np.uint8)
    binary = _expand_binary_mask_to_anr_grid(binary)

    # Output as RGBA (matching ComfyUI naimask_to_base64):
    # white RGB where mask is active, alpha derived from binary.
    rgb = np.stack([binary, binary, binary], axis=-1)
    alpha_ch = (binary > 0).astype(np.uint8) * 255
    rgba_out = np.dstack((rgb, alpha_ch))
    img = Image.fromarray(rgba_out, "RGBA")
    return _image_to_base64(img)


def _novelai_inpaint_model(model: str) -> str:
    """Return the API model name used for NovelAI infill requests."""
    if model.endswith("-inpainting") or "nai-diffusion-2" in model:
        return model
    if model == "nai-diffusion-4-curated-preview":
        return "nai-diffusion-4-curated-inpainting"
    return f"{model}-inpainting"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class NovelAIProvider(BaseImageProvider):
    """NovelAI 图片生成适配器。"""

    name = "novelai"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._access_token: str = ""
        self._client: httpx.AsyncClient | None = None
        # Settings with defaults
        s = self._config.settings
        self._model: str = s.get("model", "nai-diffusion-4-5-full")
        self._sampler: str = s.get("sampler", "k_euler")
        self._scheduler: str = s.get("scheduler", "native")
        self._steps: int = s.get("steps", 28)
        self._cfg: float = s.get("cfg", 5.0)
        self._cfg_rescale: float = s.get("cfg_rescale", 0.0)
        self._uncond_scale: float = s.get("uncond_scale", 1.0)
        self._uc_preset: int = s.get("uc_preset", 3)
        self._smea: str = s.get("smea", "none")
        self._variety: bool = s.get("variety", False)
        self._decrisper: bool = s.get("decrisper", False)
        self._limit_opus_free: bool = s.get("limit_opus_free", True)
        self._timeout: int = s.get("timeout", 120)
        self._retry: int = s.get("retry", 3)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        auth = self._config.auth
        # Resolve access token
        access_token = resolve_novelai_access_token(
            auth.get("access_token"),
            client_py_path=auth.get("client_py_path") or auth.get("client_py"),
        )
        if access_token:
            self._access_token = access_token
        elif "access_key" in auth and auth["access_key"]:
            self._access_token = await self._login(auth["access_key"])
        elif "username" in auth and "password" in auth:
            access_key = _get_access_key(auth["username"], auth["password"])
            self._access_token = await self._login(access_key)
        else:
            raise ProviderError(
                self.name,
                "No valid auth config. Provide access_token, NAI_ACCESS_TOKEN, "
                "client_py_path, access_key, or username+password."
            )
        self._client = httpx.AsyncClient(timeout=self._timeout)
        logger.info("[NovelAI] Initialized, model={}", self._model)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("[NovelAI] Closed")

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    def supports(self, capability: Capability) -> bool:
        return capability in (Capability.GENERATE, Capability.INPAINT)

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    async def generate(self, request: GenerateRequest) -> list[ImageResult]:
        results: list[ImageResult] = []
        extra = request.extra

        # Resolve dimensions
        width, height = _calculate_resolution(
            request.width * request.height,
            (request.width, request.height),
        )

        # Override from extra
        model = extra.get("model", self._model)
        sampler = extra.get("sampler", self._sampler)
        scheduler = extra.get("scheduler", self._scheduler)
        steps = extra.get("steps", self._steps)
        cfg = extra.get("cfg", self._cfg)
        smea = extra.get("smea", self._smea)

        for i in range(request.count):
            seed = (request.seed + i) if request.seed is not None else random.randint(0, 2**32 - 1)

            params = self._build_params(
                width=width,
                height=height,
                positive=request.prompt,
                negative=request.negative_prompt,
                seed=seed,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                scheduler=scheduler,
                smea=smea,
                model=model,
                extra=extra,
            )

            action = "generate"
            # img2img support via extra
            if "image_base64" in extra:
                action = "img2img"
                params["image"] = extra["image_base64"]
                params["strength"] = extra.get("strength", 0.7)
                params["noise"] = extra.get("noise", 0.0)

            try:
                image_bytes = await self._post_generate(
                    prompt=request.prompt,
                    model=model,
                    action=action,
                    parameters=params,
                )
                results.append(ImageResult(
                    image_bytes=image_bytes,
                    seed=seed,
                    provider_name=self.name,
                    model_name=model,
                    generation_params={
                        "prompt": request.prompt,
                        "negative_prompt": request.negative_prompt,
                        "width": width,
                        "height": height,
                        "seed": seed,
                        "steps": steps,
                        "cfg": cfg,
                        "sampler": sampler,
                        "scheduler": scheduler,
                        "model": model,
                        "action": action,
                    },
                    cost=0.0,  # TODO: Anlas tracking
                ))
            except Exception as e:
                logger.error("[NovelAI] Generate failed for seed {}: {}", seed, e)
                # Continue to next seed instead of aborting entire batch
                continue

        return results

    # ------------------------------------------------------------------
    # Inpaint
    # ------------------------------------------------------------------

    async def inpaint(self, request: InpaintRequest) -> list[ImageResult]:
        source_img = _bytes_to_pil(request.image)
        width = request.width or source_img.width
        height = request.height or source_img.height
        width, height = _calculate_resolution(width * height, (width, height))

        extra = request.extra
        model = extra.get("model", self._model)
        base_model = model.removesuffix("-inpainting")
        sampler = extra.get("sampler", self._sampler)
        steps = extra.get("steps", self._steps)
        # Apply opus free limits early so image and mask are sized to match
        # the params that _build_params will produce (idempotent).
        width, height, steps = _apply_opus_free_limits(
            width,
            height,
            steps,
            enabled=self._limit_opus_free,
        )

        # Prepare image and mask using the same payload shape as
        # Auto-NovelAI-Refactor: img2img base + infill mask.
        flatten_alpha = extra.get("flatten_alpha", False)
        img_resized = _prepare_inpaint_source_image(
            _resize_image(source_img, (width, height)),
            flatten_alpha=flatten_alpha,
        )
        image_b64 = _image_to_base64(img_resized)

        is_v4 = "4" in base_model
        mask_b64 = _mask_to_novelai_inpaint_base64(
            request.mask, (width, height), is_v4=is_v4,
        )

        action = "infill"
        api_model = _novelai_inpaint_model(model)
        results: list[ImageResult] = []

        for i in range(request.count):
            seed = request.seed + i if request.seed is not None else random.randint(0, 2**32 - 1)
            strength = extra.get("strength", 0.7)
            noise = extra.get("noise", 0.0)
            inpaint_i2i_strength = extra.get(
                "inpaint_i2i_strength",
                extra.get("inpaint_strength", 1.0),
            )
            params = self._build_params(
                width=width,
                height=height,
                positive=request.prompt,
                negative=request.negative_prompt,
                seed=seed,
                steps=steps,
                cfg=extra.get("cfg", self._cfg),
                sampler=sampler,
                scheduler=extra.get("scheduler", self._scheduler),
                smea=extra.get("smea", self._smea),
                model=base_model,
                extra=extra,
            )
            # params_version is now set by _build_params based on model
            # For multipart upload: image/mask are sent as files, referenced by name
            params["image"] = "image"
            params["mask"] = "mask"
            params["strength"] = strength
            params["noise"] = noise
            params["extra_noise_seed"] = extra.get("extra_noise_seed", seed)
            params["color_correct"] = extra.get("color_correct", False)
            params["add_original_image"] = extra.get("add_original_image", False)
            if is_v4:
                params["inpaintImg2ImgStrength"] = inpaint_i2i_strength

            # Prepare PNG bytes for multipart upload
            img_png_bytes = _pil_to_png_bytes(img_resized)
            mask_pil = _mask_b64_to_pil(mask_b64)
            mask_png_bytes = _pil_to_png_bytes(mask_pil)

            image_bytes = await self._post_generate_multipart(
                prompt=request.prompt,
                model=api_model,
                action=action,
                parameters=params,
                image_bytes=img_png_bytes,
                mask_bytes=mask_png_bytes,
            )

            results.append(ImageResult(
                image_bytes=image_bytes,
                seed=seed,
                provider_name=self.name,
                model_name=api_model,
                generation_params={
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "steps": params["steps"],
                    "cfg": params["scale"],
                    "sampler": params["sampler"],
                    "scheduler": params["noise_schedule"],
                    "model": api_model,
                    "base_model": base_model,
                    "action": action,
                    "add_original_image": params["add_original_image"],
                    "strength": params["strength"],
                    "noise": params["noise"],
                    "color_correct": params["color_correct"],
                    "extra_noise_seed": params["extra_noise_seed"],
                    "inpaint_i2i_strength": params.get("inpaintImg2ImgStrength"),
                },
                cost=0.0,
            ))

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_params(
        self,
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
    ) -> dict[str, Any]:
        """Build the NAI API parameters dict."""
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
            "ucPreset": self._uc_preset,
            "qualityToggle": False,
            "sm": (smea in ("SMEA", "SMEA+DYN")) and sampler != "ddim",
            "sm_dyn": (smea == "SMEA+DYN") and sampler != "ddim",
            "dynamic_thresholding": self._decrisper,
            "controlnet_strength": 1.0,
            "legacy": False,
            "add_original_image": False,
            "cfg_rescale": self._cfg_rescale,
            "noise_schedule": scheduler,
            "legacy_v3_extend": False,
            "uncond_scale": self._uncond_scale,
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

        # Sampler-specific fixes
        if sampler == "k_euler_ancestral" and scheduler != "native":
            params["deliberate_euler_ancestral_bug"] = False
            params["prefer_brownian"] = True

        if sampler == "ddim" and "nai-diffusion-2" not in model:
            params["sampler"] = "ddim_v3"

        # Opus free limits
        params["width"], params["height"], params["steps"] = _apply_opus_free_limits(
            params["width"],
            params["height"],
            params["steps"],
            enabled=self._limit_opus_free,
        )

        # Variety mode
        if self._variety or extra.get("variety", False):
            params["skip_cfg_above_sigma"] = _calculate_skip_cfg_above_sigma(
                params["width"], params["height"]
            )

        return params

    async def _login(self, access_key: str) -> str:
        """Login with access_key, return JWT token."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{AUTH_URL}/user/login",
                json={"key": access_key},
            )
            if resp.status_code != 200:
                raise ProviderError(self.name, f"Login failed: {resp.status_code} {resp.text}")
            return resp.json()["accessToken"]

    async def _post_generate(
        self,
        prompt: str,
        model: str,
        action: str,
        parameters: dict[str, Any],
    ) -> bytes:
        """POST to NAI generate-image endpoint, extract PNG from zip response."""
        assert self._client is not None

        data = {
            "input": prompt,
            "model": model,
            "action": action,
            "parameters": parameters,
        }
        headers = {"Authorization": f"Bearer {self._access_token}"}

        last_error: Exception | None = None
        for attempt in range(self._retry):
            try:
                resp = await self._client.post(
                    f"{BASE_URL}/ai/generate-image",
                    json=data,
                    headers=headers,
                    timeout=self._timeout,
                )

                if resp.status_code == 429:
                    raise RateLimitError(self.name, retry_after=5.0)

                if resp.status_code >= 400:
                    raise ProviderError(
                        self.name,
                        f"HTTP {resp.status_code}: {resp.text[:500]}"
                    )

                # NAI returns a zip file containing the PNG
                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    image_bytes = zf.read(zf.infolist()[0])
                return image_bytes

            except RateLimitError:
                import asyncio
                wait = (attempt + 1) * 5
                logger.warning("[NovelAI] Rate limited, waiting {}s...", wait)
                await asyncio.sleep(wait)
                last_error = RateLimitError(self.name, retry_after=float(wait))
            except ProviderError as e:
                last_error = e
                logger.error("[NovelAI] Attempt {}/{}: {}", attempt + 1, self._retry, e)
                import asyncio
                await asyncio.sleep(2)
            except httpx.TimeoutException as e:
                last_error = ProviderError(self.name, f"Timeout: {e}", e)
                logger.error("[NovelAI] Timeout on attempt {}/{}", attempt + 1, self._retry)
                import asyncio
                await asyncio.sleep(2)

        raise last_error or ProviderError(self.name, "All retries exhausted")
    async def _post_generate_multipart(
        self,
        prompt: str,
        model: str,
        action: str,
        parameters: dict[str, Any],
        image_bytes: bytes,
        mask_bytes: bytes,
    ) -> bytes:
        """POST to NAI generate-image using multipart/form-data (official website format).

        The official NovelAI website sends inpaint requests as multipart with
        three file-upload parts (all ``filename="blob"``):

        1. ``name="image"``  -- source PNG
        2. ``name="mask"``   -- inpaint mask PNG
        3. ``name="request"`` -- JSON parameters (``Content-Type: application/json``)

        ``parameters.image`` and ``parameters.mask`` are set to ``"image"`` /
        ``"mask"`` to reference the uploaded files by field name.
        """
        assert self._client is not None

        request_json = json.dumps({
            "input": prompt,
            "model": model,
            "action": action,
            "parameters": parameters,
        }).encode("utf-8")

        headers = {"Authorization": f"Bearer {self._access_token}"}

        # All three parts are sent as file uploads (filename="blob")
        # matching the exact structure captured from the official website.
        files = [
            ("image", ("blob", image_bytes, "image/png")),
            ("mask", ("blob", mask_bytes, "image/png")),
            ("request", ("blob", request_json, "application/json")),
        ]

        last_error: Exception | None = None
        for attempt in range(self._retry):
            try:
                resp = await self._client.post(
                    f"{BASE_URL}/ai/generate-image",
                    files=files,
                    headers=headers,
                    timeout=self._timeout,
                )

                if resp.status_code == 429:
                    raise RateLimitError(self.name, retry_after=5.0)

                if resp.status_code >= 400:
                    raise ProviderError(
                        self.name,
                        f"HTTP {resp.status_code}: {resp.text[:500]}"
                    )

                # NAI returns a zip file containing the PNG
                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    image_bytes_out = zf.read(zf.infolist()[0])
                return image_bytes_out

            except RateLimitError:
                import asyncio
                wait = (attempt + 1) * 5
                logger.warning("[NovelAI] Rate limited, waiting {}s...", wait)
                await asyncio.sleep(wait)
                last_error = RateLimitError(self.name, retry_after=float(wait))
            except ProviderError as e:
                last_error = e
                logger.error("[NovelAI] Multipart attempt {}/{}: {}", attempt + 1, self._retry, e)
                import asyncio
                await asyncio.sleep(2)
            except httpx.TimeoutException as e:
                last_error = ProviderError(self.name, f"Timeout: {e}", e)
                logger.error("[NovelAI] Multipart timeout on attempt {}/{}", attempt + 1, self._retry)
                import asyncio
                await asyncio.sleep(2)

        raise last_error or ProviderError(self.name, "All retries exhausted")

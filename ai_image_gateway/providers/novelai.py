"""
NovelAI Provider — 接入 NovelAI 图片生成 API。

API 逻辑提取自 ComfyUI_NAIDGenerator 项目，去除 ComfyUI/torch 依赖，
仅使用 httpx + Pillow 实现纯 HTTP 调用。

支持能力:
  - generate: txt2img / img2img
  - inpaint: infill (局部重绘)
  - augment: bg-removal / lineart / sketch / colorize / emotion / declutter

认证方式 (优先级):
  1. config.auth.access_token  — 直接提供 JWT token
  2. config.auth.access_key    — 提供 access_key，自动 login
  3. config.auth.username + config.auth.password — 自动 hash + login
"""

from __future__ import annotations

import base64
import io
import random
import zipfile
from hashlib import blake2b
from typing import Any

import httpx
import numpy as np
from loguru import logger
from PIL import Image

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


def _mask_to_nai_base64(mask_bytes: bytes, target_size: tuple[int, int], is_v4: bool = False) -> str:
    """Convert mask bytes to NAI-format mask (white on black, alpha channel)."""
    mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
    w, h = target_size
    mask_w = int(np.ceil(w / 64) * 8)
    mask_h = int(np.ceil(h / 64) * 8)
    if is_v4:
        mask_w *= 8
        mask_h *= 8
    mask = mask.resize((mask_w, mask_h), Image.Resampling.NEAREST)
    # Convert to RGBA with alpha = mask > 0
    arr = np.array(mask)
    alpha = (arr > 0).astype(np.uint8) * 255
    rgba = np.dstack([np.stack([arr, arr, arr], axis=-1), alpha[:, :, None].squeeze(-1) if alpha.ndim == 2 else alpha])
    # Fix: ensure rgba is 3D with 4 channels
    if rgba.ndim == 2:
        rgba = np.stack([arr, arr, arr, alpha], axis=-1)
    elif rgba.shape[-1] != 4:
        rgba = np.stack([arr, arr, arr, alpha], axis=-1)
    img = Image.fromarray(rgba.astype(np.uint8), "RGBA")
    return _image_to_base64(img)


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
        if "access_token" in auth and auth["access_token"]:
            self._access_token = auth["access_token"]
        elif "access_key" in auth and auth["access_key"]:
            self._access_token = await self._login(auth["access_key"])
        elif "username" in auth and "password" in auth:
            access_key = _get_access_key(auth["username"], auth["password"])
            self._access_token = await self._login(access_key)
        else:
            raise ProviderError(
                self.name,
                "No valid auth config. Provide access_token, access_key, or username+password."
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
        width = request.width or 512
        height = request.height or 512
        width, height = _calculate_resolution(width * height, (width, height))

        extra = request.extra
        model = extra.get("model", self._model)
        sampler = extra.get("sampler", self._sampler)
        seed = random.randint(0, 2**32 - 1)

        # Prepare image and mask
        img = _bytes_to_pil(request.image)
        img_resized = _resize_image(img, (width, height))
        image_b64 = _image_to_base64(img_resized)

        is_v4 = "4" in model
        mask_b64 = _mask_to_nai_base64(request.mask, (width, height), is_v4)

        params = self._build_params(
            width=width,
            height=height,
            positive=request.prompt,
            negative=request.negative_prompt,
            seed=seed,
            steps=extra.get("steps", self._steps),
            cfg=extra.get("cfg", self._cfg),
            sampler=sampler,
            scheduler=extra.get("scheduler", self._scheduler),
            smea=extra.get("smea", self._smea),
            model=model,
            extra=extra,
        )
        params["image"] = image_b64
        params["mask"] = mask_b64
        params["add_original_image"] = extra.get("add_original_image", True)

        action = "infill"
        if "nai-diffusion-2" not in model:
            model = f"{model}-inpainting"

        image_bytes = await self._post_generate(
            prompt=request.prompt,
            model=model,
            action=action,
            parameters=params,
        )

        return [ImageResult(
            image_bytes=image_bytes,
            seed=seed,
            provider_name=self.name,
            model_name=model,
            generation_params={
                "prompt": request.prompt,
                "width": width,
                "height": height,
                "seed": seed,
                "action": action,
            },
            cost=0.0,
        )]

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
        params: dict[str, Any] = {
            "params_version": 1,
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
        if self._limit_opus_free:
            pixel_limit = 1024 * 1024
            if width * height > pixel_limit:
                params["width"], params["height"] = _calculate_resolution(
                    pixel_limit, (width, height)
                )
            if steps > 28:
                params["steps"] = 28

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

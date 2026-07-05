from __future__ import annotations

import io
import random
from typing import Any

from loguru import logger

from ...config import ProviderConfig
from ...contracts import Capability, GenerateRequest, ImageResult, InpaintRequest, NovelAIRawPayload
from ...errors import ProviderError
from ..base import BaseImageProvider
from .decode import first_image_bytes
from .payloads import (
    NovelAIDefaults,
    _calculate_resolution,
    _mask_b64_to_pil,
    _mask_to_novelai_inpaint_base64,
    _novelai_inpaint_model,
    _pil_to_png_bytes,
    _prepare_inpaint_source_image,
    _resize_image,
    build_params,
)
from .raw_client import _NovelAITransportProvider


class NovelAIFacadeProvider(BaseImageProvider):
    name = "novelai"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._transport = _NovelAITransportProvider(config)
        self._defaults = NovelAIDefaults.from_settings(config.settings)
        self._access_token = ""
        self._client = None
        self._base_url: str = self._transport._base_url
        self._model: str = self._defaults.model
        self._sampler: str = self._defaults.sampler
        self._scheduler: str = self._defaults.scheduler
        self._steps: int = self._defaults.steps
        self._cfg: float = self._defaults.cfg
        self._cfg_rescale: float = self._defaults.cfg_rescale
        self._uncond_scale: float = self._defaults.uncond_scale
        self._uc_preset: int = self._defaults.uc_preset
        self._smea: str = self._defaults.smea
        self._variety: bool = self._defaults.variety
        self._decrisper: bool = self._defaults.decrisper
        self._limit_opus_free: bool = self._defaults.limit_opus_free
        self._timeout: int = self._transport._timeout
        self._retry: int = self._transport._retry
        self._retry_interval: float | None = self._transport._retry_interval

    async def initialize(self) -> None:
        await self._transport.initialize()
        self._access_token = self._transport._access_token
        self._client = self._transport._client
        logger.info("[NovelAI] Initialized, model={}", self._model)

    async def close(self) -> None:
        await self._transport.close()
        self._client = self._transport._client
        logger.info("[NovelAI] Closed")

    def supports(self, capability: Capability) -> bool:
        return capability in (Capability.GENERATE, Capability.INPAINT)

    async def generate(self, request: GenerateRequest) -> list[ImageResult]:
        results: list[ImageResult] = []
        extra = request.extra
        width, height = _calculate_resolution(
            request.width * request.height,
            (request.width, request.height),
        )

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
            if "image_base64" in extra:
                action = "img2img"
                params["image"] = extra["image_base64"]
                params["strength"] = extra.get("strength", 0.7)
                params["noise"] = extra.get("noise", 0.0)

            payload = NovelAIRawPayload(
                input=request.prompt,
                model=model,
                action=action,
                parameters=params,
            )

            try:
                raw = await self._transport.generate_raw(payload)
                results.append(
                    ImageResult(
                        image_bytes=first_image_bytes(raw, provider_name=self.name),
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
                        cost=0.0,
                    )
                )
            except Exception as exc:
                logger.error("[NovelAI] Generate failed for seed {}: {}", seed, exc)
                continue

        return results

    async def inpaint(self, request: InpaintRequest) -> list[ImageResult]:
        from PIL import Image

        source_img = Image.open(io.BytesIO(request.image))
        width = request.width or source_img.width
        height = request.height or source_img.height
        width, height = _calculate_resolution(width * height, (width, height))

        extra = request.extra
        model = extra.get("model", self._model)
        base_model = model.removesuffix("-inpainting")
        sampler = extra.get("sampler", self._sampler)
        steps = extra.get("steps", self._steps)
        width, height, steps = self._apply_opus_free_limits(
            width,
            height,
            steps,
            enabled=self._limit_opus_free,
        )

        flatten_alpha = extra.get("flatten_alpha", False)
        img_resized = _prepare_inpaint_source_image(
            _resize_image(source_img, (width, height)),
            flatten_alpha=flatten_alpha,
        )
        is_v4 = "4" in base_model
        mask_b64 = _mask_to_novelai_inpaint_base64(
            request.mask,
            (width, height),
            is_v4=is_v4,
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
            params["image"] = "image"
            params["mask"] = "mask"
            params["strength"] = strength
            params["noise"] = noise
            params["extra_noise_seed"] = extra.get("extra_noise_seed", seed)
            params["color_correct"] = extra.get("color_correct", False)
            params["add_original_image"] = extra.get("add_original_image", False)
            if is_v4:
                params["inpaintImg2ImgStrength"] = inpaint_i2i_strength

            payload = NovelAIRawPayload(
                input=request.prompt,
                model=api_model,
                action=action,
                parameters=params,
            )
            img_png_bytes = _pil_to_png_bytes(img_resized)
            mask_png_bytes = _pil_to_png_bytes(_mask_b64_to_pil(mask_b64))
            raw = await self._transport.generate_multipart(
                payload,
                image_bytes=img_png_bytes,
                mask_bytes=mask_png_bytes,
            )

            results.append(
                ImageResult(
                    image_bytes=first_image_bytes(raw, provider_name=self.name),
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
                )
            )

        return results

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
        return build_params(
            width=width,
            height=height,
            positive=positive,
            negative=negative,
            seed=seed,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            scheduler=scheduler,
            smea=smea,
            model=model,
            extra=extra,
            defaults=self._defaults,
        )

    @staticmethod
    def _apply_opus_free_limits(
        width: int,
        height: int,
        steps: int,
        *,
        enabled: bool,
    ) -> tuple[int, int, int]:
        pixel_limit = 1024 * 1024
        if not enabled:
            return width, height, steps
        if width * height > pixel_limit:
            width, height = _calculate_resolution(pixel_limit, (width, height))
        if steps > 28:
            steps = 28
        return width, height, steps


NovelAIProvider = NovelAIFacadeProvider

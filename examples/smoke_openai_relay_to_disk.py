"""Run OpenAI-compatible relay smoke tests and save every result to disk.

Secrets are read from a local config file or environment variables:

- AI_IMAGE_PROXY_KEY
- AI_IMAGE_PROXY_BASE, for example https://proxy.example.com or
  https://proxy.example.com/v1
- --config config.local.yaml

Outputs default to:

F:/design/game/project/p3/UnityClient/Assets/Art/_IncomingAI/OpenAICompatibleRelaySmoke/<run_id>/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw

from ai_image_gateway.config import DefaultProviderConfig, GatewayConfig, ProviderConfig, load_config
from ai_image_gateway.schema import GenerateRequest, ImageToImageRequest
from ai_image_gateway.service import ImageService


GPT_MODEL = "gpt-image-2"
GEMINI_MODEL = "gemini-3.1-flash-image"
GROK_MODEL = "grok-imagine-image-lite"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        return base_url
    return f"{base_url}/v1"


def _provider_api_key(config: GatewayConfig, provider_name: str) -> str:
    provider = config.providers.get(provider_name)
    if not provider:
        return ""
    return str(provider.auth.get("api_key", "")).strip()


def _provider_base_url(config: GatewayConfig, provider_name: str) -> str:
    provider = config.providers.get(provider_name)
    if not provider:
        return ""
    return str(provider.settings.get("base_url", "")).strip()


def _resolve_config(args: argparse.Namespace) -> tuple[GatewayConfig, str, str, str]:
    """Return config, base URL, API key, and config source label."""
    if args.config:
        config = load_config(args.config)
        api_key = (
            _provider_api_key(config, "openai_images")
            or _provider_api_key(config, "gemini_chat_image")
            or _provider_api_key(config, "grok_chat_image")
        )
        base_url = (
            _provider_base_url(config, "openai_images")
            or _provider_base_url(config, "gemini_chat_image")
            or _provider_base_url(config, "grok_chat_image")
        )
        if not api_key or not base_url:
            raise SystemExit(
                f"{args.config} must configure api_key and base_url for at least one relay provider."
            )
        return config, _normalize_base_url(base_url), api_key, str(args.config)

    api_key = os.environ.get("AI_IMAGE_PROXY_KEY", "").strip()
    base_url_raw = os.environ.get("AI_IMAGE_PROXY_BASE", "").strip()
    if not api_key or not base_url_raw:
        raise SystemExit(
            "Missing --config, AI_IMAGE_PROXY_KEY, or AI_IMAGE_PROXY_BASE. "
            "Use --config config.local.yaml or set environment variables."
        )
    base_url = _normalize_base_url(base_url_raw)
    return _config(base_url, api_key), base_url, api_key, "environment"


def _image_extension(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _fallback_reference_image(out_dir: Path) -> bytes:
    """Create a local reference image so image-to-image routes are always hit."""
    path = out_dir / "reference_fallback.png"
    image = Image.new("RGB", (1024, 1024), "#d8d1c7")
    draw = ImageDraw.Draw(image)
    draw.ellipse((292, 212, 732, 652), fill="#2457b8", outline="#0e1f44", width=16)
    draw.polygon(
        [(512, 292), (608, 512), (512, 732), (416, 512)],
        fill="#76d7ff",
        outline="#102b57",
    )
    draw.ellipse((462, 462, 562, 562), fill="#f7fbff", outline="#102b57", width=8)
    image.save(path, format="PNG")
    return path.read_bytes()


async def _fetch_models(base_url: str, api_key: str, out_dir: Path) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        payload = {
            "status_code": response.status_code,
            "text": response.text[:1000],
        }
    (out_dir / "models.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _config(base_url: str, api_key: str) -> GatewayConfig:
    return GatewayConfig(
        default_provider=DefaultProviderConfig(
            generate="openai_images",
            image_to_image="gemini_chat_image",
            inpaint="mock",
            upscale="mock",
        ),
        providers={
            "openai_images": ProviderConfig(
                enabled=True,
                auth={"api_key": api_key},
                settings={
                    "base_url": base_url,
                    "endpoint": "/images/generations",
                    "edit_endpoint": "/images/edits",
                    "model": GPT_MODEL,
                    "response_format": "b64_json",
                    "size": "1024x1024",
                    "quality": "high",
                    "output_format": "png",
                    "timeout": 180,
                    "retry": 1,
                },
            ),
            "gemini_chat_image": ProviderConfig(
                enabled=True,
                auth={"api_key": api_key},
                settings={
                    "base_url": base_url,
                    "endpoint": "/chat/completions",
                    "model": GEMINI_MODEL,
                    "temperature": 0.2,
                    "timeout": 180,
                    "retry": 1,
                },
            ),
            "grok_chat_image": ProviderConfig(
                enabled=True,
                auth={"api_key": api_key},
                settings={
                    "base_url": base_url,
                    "endpoint": "/chat/completions",
                    "model": GROK_MODEL,
                    "temperature": 0.2,
                    "timeout": 180,
                    "retry": 1,
                },
            ),
        },
    )


async def _save_batch(
    *,
    out_dir: Path,
    route: str,
    batch,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "route": route,
        "success_count": batch.success_count,
        "errors": batch.errors,
        "files": [],
    }
    route_dir = out_dir / route
    route_dir.mkdir(parents=True, exist_ok=True)

    for index, result in enumerate(batch.results):
        ext = _image_extension(result.image_bytes)
        file_name = f"{route}_{index:02d}{ext}"
        path = route_dir / file_name
        path.write_bytes(result.image_bytes)
        metadata_path = route_dir / f"{route}_{index:02d}.json"
        metadata = {
            "provider": result.provider_name,
            "model": result.model_name,
            "seed": result.seed,
            "generation_params": result.generation_params,
            "cost": result.cost,
            "bytes": len(result.image_bytes),
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        record["files"].append({
            "image": str(path),
            "metadata": str(metadata_path),
            **metadata,
        })
    return record


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="Local gateway config file, for example config.local.yaml.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Override output directory. Defaults to _IncomingAI/OpenAICompatibleRelaySmoke/<run_id>.",
    )
    args = parser.parse_args()

    config, base_url, api_key, config_source = _resolve_config(args)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or Path(os.environ.get(
        "AI_IMAGE_RELAY_SMOKE_OUT",
        _project_root() / "UnityClient" / "Assets" / "Art" / "_IncomingAI"
        / "OpenAICompatibleRelaySmoke" / run_id,
    ))
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "base_url": base_url,
        "config_source": config_source,
        "models": [GPT_MODEL, GEMINI_MODEL, GROK_MODEL],
        "routes": [],
        "notes": [
            "API key read from environment only.",
            "Known-bad routes are still attempted so failures are captured.",
        ],
    }

    manifest["model_list"] = await _fetch_models(base_url, api_key, out_dir)

    prompt = (
        "P3 relay smoke test image: a clean noncommercial game asset concept, "
        "a small luminous blue crystal compass on a plain warm gray background, "
        "single object centered, readable silhouette, no text, no watermark."
    )
    negative = "text, watermark, logo, signature, extra objects, blurry"

    async with ImageService(config) as service:
        route_requests: list[tuple[str, Any]] = [
            ("gpt_images_generate", GenerateRequest(
                provider="openai_images",
                prompt=prompt,
                negative_prompt=negative,
                width=1024,
                height=1024,
                count=1,
            )),
            ("gemini_chat_generate", GenerateRequest(
                provider="gemini_chat_image",
                prompt=prompt,
                negative_prompt=negative,
                width=1024,
                height=1024,
                count=1,
            )),
            ("grok_chat_generate", GenerateRequest(
                provider="grok_chat_image",
                prompt=prompt,
                negative_prompt=negative,
                width=1024,
                height=1024,
                count=1,
            )),
        ]

        reference_image: bytes | None = None
        reference_source = "none"
        for route, request in route_requests:
            batch = await service.generate(request)
            manifest["routes"].append(await _save_batch(out_dir=out_dir, route=route, batch=batch))
            if route == "gpt_images_generate" and batch.results:
                reference_image = batch.results[0].image_bytes
                reference_source = "gpt_images_generate"

        if reference_image is None:
            reference_image = _fallback_reference_image(out_dir)
            reference_source = "local_reference_fallback"
        manifest["reference_source"] = reference_source

        image_prompt = (
            "Use the reference image as the main object. Create a polished variant "
            "with a slightly stronger blue glow and cleaner game icon silhouette. "
            "Keep one centered object, no text, no watermark."
        )
        for route, provider in (
            ("gpt_images_image_to_image", "openai_images"),
            ("gemini_chat_image_to_image", "gemini_chat_image"),
            ("grok_chat_image_to_image", "grok_chat_image"),
        ):
            batch = await service.image_to_image(ImageToImageRequest(
                provider=provider,
                images=[reference_image],
                prompt=image_prompt,
                negative_prompt=negative,
                width=1024,
                height=1024,
                count=1,
            ))
            manifest["routes"].append(await _save_batch(out_dir=out_dir, route=route, batch=batch))

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

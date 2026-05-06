"""NovelAI Provider 真实 API Smoke Test。"""

import asyncio
from pathlib import Path

from ai_image_gateway import ImageService, GenerateRequest
from ai_image_gateway.config import (
    GatewayConfig,
    DefaultProviderConfig,
    ProviderConfig,
)

OUTPUT_DIR = Path(__file__).parent / "output"


async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    config = GatewayConfig(
        default_provider=DefaultProviderConfig(generate="novelai"),
        providers={
            "novelai": ProviderConfig(
                enabled=True,
                auth={
                    "access_token": "pst-2ZWgAWqeSRU6aAQ0YtwECMoQ1aV2x7S6Yo3U8Tcpet3LLooyXTskoaXgKz4Jn2Vg",
                },
                settings={
                    "model": "nai-diffusion-4-5-full",
                    "sampler": "k_euler",
                    "steps": 28,
                    "cfg": 5.0,
                    "limit_opus_free": True,
                    "timeout": 120,
                    "retry": 3,
                },
            ),
        },
    )

    print("[1/3] Initializing NovelAI provider...")
    async with ImageService(config) as svc:
        print(f"  Available providers: {svc.available_providers}")

        print("\n[2/3] Generating single image...")
        result = await svc.generate(GenerateRequest(
            prompt="game item icon, rusty dagger, steampunk, worn metal blade, transparent background, best quality, amazing quality, very aesthetic, absurdres",
            negative_prompt="lowres, text, watermark, signature",
            width=832,
            height=1216,
            count=1,
            seed=42,
        ))

        if result.errors:
            print(f"  ERRORS: {result.errors}")
        else:
            print(f"  Success: {result.success_count} image(s)")
            for img in result.results:
                out = OUTPUT_DIR / f"nai_smoke_{img.seed}.png"
                out.write_bytes(img.image_bytes)
                print(f"  Saved: {out}")
                print(f"  Provider: {img.provider_name}")
                print(f"  Model: {img.model_name}")
                print(f"  Seed: {img.seed}")
                print(f"  Size: {len(img.image_bytes)} bytes")

        print("\n[3/3] Done!")


if __name__ == "__main__":
    asyncio.run(main())

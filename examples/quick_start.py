"""
AI Image Gateway 快速上手模板。

运行: python examples/quick_start.py
输出: examples/output/ 目录下的占位图片
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime

from ai_image_gateway import ImageService, GenerateRequest
from ai_image_gateway.config import GatewayConfig
from ai_image_gateway.processing import prompt_tools, post_processor
from PIL import Image
import io


# ═══════════════════════════════════════════════════════════════════
# 1. 最简单的用法 — 单张生图
# ═══════════════════════════════════════════════════════════════════

async def example_single():
    """生成单张图片。"""
    print("\n=== 示例 1: 单张生图 ===")

    # 无配置文件时自动使用 MockProvider
    async with ImageService() as svc:
        result = await svc.generate(GenerateRequest(
            prompt="game item icon, rusty dagger, steampunk, transparent background",
            negative_prompt="text, watermark, signature",
            width=512,
            height=512,
        ))

        # 检查结果
        print(f"  成功: {result.success_count} 张")
        print(f"  错误: {result.errors}")
        print(f"  消耗: {result.total_cost}")

        # 保存
        if result.results:
            img = result.results[0]
            out = OUTPUT_DIR / f"single_{img.seed}.png"
            out.write_bytes(img.image_bytes)
            print(f"  保存: {out}")


# ═══════════════════════════════════════════════════════════════════
# 2. 批量生图 — 同一个 prompt 生成多张候选
# ═══════════════════════════════════════════════════════════════════

async def example_multi_candidates():
    """同一 prompt 生成 4 张候选图。"""
    print("\n=== 示例 2: 多张候选 ===")

    async with ImageService() as svc:
        result = await svc.generate(GenerateRequest(
            prompt="game item icon, chainsaw greatsword, heavy metal blade",
            negative_prompt="text, watermark",
            width=512,
            height=512,
            count=4,              # <-- 一次出 4 张
            seed=42,              # <-- 固定种子便于复现
        ))

        print(f"  生成 {result.success_count} 张候选")
        for i, img in enumerate(result.results):
            out = OUTPUT_DIR / f"candidate_{i}_{img.seed}.png"
            out.write_bytes(img.image_bytes)
            print(f"  [{i}] seed={img.seed} -> {out.name}")


# ═══════════════════════════════════════════════════════════════════
# 3. 批量请求 — 多个不同资产一次跑完
# ═══════════════════════════════════════════════════════════════════

async def example_batch():
    """多个不同请求的批量生成。"""
    print("\n=== 示例 3: 批量请求 ===")

    # 模拟从 Manifest 读取的资产列表
    assets = [
        {"prompt": "game item icon, rusty dagger, chipped blade, corroded edge", "name": "rusty_dagger"},
        {"prompt": "game item icon, energy pistol, blue glowing core, short barrel", "name": "charge_pistol"},
        {"prompt": "game item icon, iron chest armor, rivets, leather straps", "name": "iron_armor"},
    ]

    requests = [
        GenerateRequest(
            prompt=a["prompt"],
            negative_prompt="text, watermark, signature",
            width=512,
            height=512,
            count=2,  # 每个资产 2 张候选
        )
        for a in assets
    ]

    async with ImageService() as svc:
        # delay_seconds: 请求间隔，真实 API 时用于防限流
        results = await svc.batch_generate(
            requests,
            delay_seconds=0.1,
            on_progress=lambda cur, total, batch: print(
                f"  进度: {cur}/{total} — {batch.success_count} 张成功"
            ),
        )

        for asset, batch in zip(assets, results):
            print(f"  {asset['name']}: {batch.success_count} 张, 错误: {batch.errors}")
            for i, img in enumerate(batch.results):
                out = OUTPUT_DIR / f"{asset['name']}_{i}.png"
                out.write_bytes(img.image_bytes)


# ═══════════════════════════════════════════════════════════════════
# 4. 使用 prompt 工具
# ═══════════════════════════════════════════════════════════════════

async def example_prompt_tools():
    """prompt 清洗和拼接。"""
    print("\n=== 示例 4: Prompt 工具 ===")

    # 风格基底 (来自美术风格基准)
    style = "subterranean fantasy adventure, whimsical yet ominous, steampunk machinery, brass and copper details, worn metal, soft painterly 2D game asset"

    # 主体描述
    subject = "game item icon, small cheap sedative ampoule, worn metal safety sleeve, cool blue liquid"

    # 构图
    composition = "centered single object, clean readable silhouette, transparent background"

    # 拼接
    prompt = prompt_tools.merge(style, subject, composition)
    print(f"  合并后: {prompt[:80]}...")

    # 校验长度
    ok, tokens = prompt_tools.validate_length(prompt, max_tokens=225)
    print(f"  token 数: {tokens}, 合法: {ok}")

    # 清洗脏数据
    dirty = "a,,, b, {}, [],  c,  "
    clean = prompt_tools.sanitize(dirty)
    print(f"  清洗: '{dirty}' -> '{clean}'")

    # 使用清洗后的 prompt 生图
    async with ImageService() as svc:
        result = await svc.generate(GenerateRequest(prompt=prompt, width=512, height=512))
        out = OUTPUT_DIR / "prompt_tools_demo.png"
        out.write_bytes(result.results[0].image_bytes)
        print(f"  保存: {out}")


# ═══════════════════════════════════════════════════════════════════
# 5. 使用后处理工具
# ═══════════════════════════════════════════════════════════════════

async def example_post_processing():
    """对生成结果进行后处理。"""
    print("\n=== 示例 5: 后处理 ===")

    async with ImageService() as svc:
        result = await svc.generate(GenerateRequest(
            prompt="monster portrait, mutant creature, exposed metal bones",
            width=1024,
            height=1024,
            count=1,
        ))

        raw_bytes = result.results[0].image_bytes
        img = post_processor.from_bytes(raw_bytes)
        print(f"  原始尺寸: {img.size}")

        # 裁切为 16:9
        cropped = post_processor.crop_to_aspect(img, 16 / 9)
        print(f"  裁切 16:9: {cropped.size}")

        # 缩放到 512x512
        resized = post_processor.resize(img, 512, 512)
        print(f"  缩放: {resized.size}")

        # 裁掉透明边缘 + 安全边距
        trimmed = post_processor.trim_transparent(img, padding_percent=10)
        print(f"  裁透明边: {trimmed.size}")

        # 适配安全边距
        fitted = post_processor.fit_safe_padding(img, 512, 512, padding_percent=10)
        print(f"  安全边距适配: {fitted.size}")

        # 导出
        out = OUTPUT_DIR / "post_processed.png"
        out.write_bytes(post_processor.to_bytes(fitted))
        print(f"  保存: {out}")


# ═══════════════════════════════════════════════════════════════════
# 6. 指定 provider
# ═══════════════════════════════════════════════════════════════════

async def example_specify_provider():
    """显式指定使用哪个 provider。"""
    print("\n=== 示例 6: 指定 Provider ===")

    async with ImageService() as svc:
        # 查看可用 provider
        print(f"  可用 providers: {svc.available_providers}")

        # 显式指定 mock
        result = await svc.generate(GenerateRequest(
            prompt="test with explicit provider",
            width=256,
            height=256,
            provider="mock",       # <-- 显式指定
        ))
        print(f"  使用: {result.results[0].provider_name}")

        # 请求不存在的 provider (会被记录到 errors)
        result = await svc.generate(GenerateRequest(
            prompt="test",
            provider="nonexistent",
        ))
        print(f"  不存在的 provider 错误: {result.errors}")


# ═══════════════════════════════════════════════════════════════════
# 7. 使用 YAML 配置文件
# ═══════════════════════════════════════════════════════════════════

async def example_with_config():
    """使用自定义配置文件。"""
    print("\n=== 示例 7: YAML 配置 ===")

    # 也可以用代码构造 config
    from ai_image_gateway.config import GatewayConfig, ProviderConfig, DefaultProviderConfig

    config = GatewayConfig(
        default_provider=DefaultProviderConfig(generate="mock"),
        providers={
            "mock": ProviderConfig(
                enabled=True,
                settings={
                    "bg_color": "#1a1a2e",       # 自定义占位图背景色
                    "text_color": "#00ff88",      # 自定义文字颜色
                },
            ),
        },
    )

    async with ImageService(config) as svc:
        result = await svc.generate(GenerateRequest(
            prompt="custom config demo",
            width=256,
            height=256,
        ))
        out = OUTPUT_DIR / "custom_config.png"
        out.write_bytes(result.results[0].image_bytes)
        print(f"  保存 (自定义配色): {out}")


# ═══════════════════════════════════════════════════════════════════
# 8. 保存 generation.json (对接美术流水线)
# ═══════════════════════════════════════════════════════════════════

async def example_generation_json():
    """生成 generation.json 记录，对接美术流水线。"""
    print("\n=== 示例 8: generation.json ===")

    async with ImageService() as svc:
        result = await svc.generate(GenerateRequest(
            prompt="tactical blade icon, steampunk",
            negative_prompt="text, watermark",
            width=512,
            height=512,
            count=3,
        ))

        # 构造 generation.json
        generation_record = {
            "BatchID": f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "RequestID": result.request_id,
            "Provider": result.results[0].provider_name if result.results else "unknown",
            "Model": result.results[0].model_name if result.results else "unknown",
            "TotalCost": result.total_cost,
            "Outputs": [
                {
                    "Filename": f"{img.seed}.png",
                    "Seed": img.seed,
                    "Params": img.generation_params,
                }
                for img in result.results
            ],
            "Errors": result.errors,
        }

        out = OUTPUT_DIR / "generation.json"
        out.write_text(json.dumps(generation_record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  generation.json -> {out}")
        print(f"  内容预览:")
        for k, v in generation_record.items():
            if k != "Outputs":
                print(f"    {k}: {v}")
        print(f"    Outputs: {len(generation_record['Outputs'])} 张")


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path(__file__).parent / "output"


async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"输出目录: {OUTPUT_DIR.resolve()}")

    await example_single()
    await example_multi_candidates()
    await example_batch()
    await example_prompt_tools()
    await example_post_processing()
    await example_specify_provider()
    await example_with_config()
    await example_generation_json()

    print(f"\n[OK] 全部完成! 查看 {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

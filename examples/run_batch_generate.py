"""Editable Python runner for text-to-image generation.

Edit the USER SETTINGS block, then run:

    python examples/run_batch_generate.py

This script generates one prompt with multiple candidates. Change COUNT to
control how many images to generate in one run.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from ai_image_gateway import GenerateRequest, ImageService


# ---------------------------------------------------------------------------
# USER SETTINGS
# ---------------------------------------------------------------------------

# Local gateway config. config.local.yaml is git-ignored and can contain keys.
CONFIG_PATH = "config.local.yaml"

# Current recommended provider for relay GPT text-to-image.
PROVIDER = "openai_images"

# Output folder. A timestamped subfolder will be created inside this directory.
OUTPUT_ROOT = r"F:\design\game\project\p3\tools\ai-image-gateway\examples\output\output_images"

# Main prompt. Replace this with the prompt you want to run.
PROMPT = """
幸运星中的柊かがみ，使用忠于原作的动画画风，经典日系萌系二次元动画风格（early 2000s anime aesthetic），Q版比例（大头小身、四肢简化、轮廓清晰），角色设计高度符号化且辨识度强，线条干净利落、粗细均一，低细节但高可读性。

画面采用“主立绘 + 多表情分镜拼贴（expression collage）”构图：主体为接近全身像，位于画面一侧或偏中心位置；周围环绕多个不同情绪与姿态的小型分镜（头像/半身），形成节奏化排版。分镜容器采用统一的几何符号造型（如星形、圆形或其他图标化轮廓），重复排列形成模块化视觉节奏，整体布局在对称中带轻微不对称变化，增强活泼感与设计感。

空间为二维平面化构成（flat composition），弱透视或无透视，背景为图形化设计而非真实环境（如色块拼接、渐变、简单图案网格），强调图形与角色的叠加关系。信息密度中等偏高，但通过统一轮廓与留白区域保持清晰结构。

光影为极简二次元平光（flat shading），几乎无复杂体积光与阴影，仅保留基础明暗分区；材质呈现为干净的数字赛璐璐上色（cel shading），无明显笔触与纹理噪点。

色彩采用高明度、低至中等饱和度的清爽配色体系（pastel + soft primary colors），主色块对比鲜明但不过度刺眼，常见暖黄、粉红、浅蓝等搭配；边缘使用纯色描边强化分离度。

情绪氛围轻松、日常、略带吐槽或微妙情绪反差（通过表情分镜体现），整体偏可爱与轻喜剧感。动态表现为“静态中的多情绪瞬时捕捉”，通过多个分镜传达时间流动与性格维度。

后期处理极简，仅保留干净的数字输出质感，无颗粒、无真实镜头效果；渲染参数偏向动画截图或设定图风格（orthographic-like framing, no depth of field, no motion blur）。

整体具有强烈的时代感（2000年代初期日本电视动画视觉语言），文化语境偏向轻松校园/日常题材的角色展示页，符号化特征明显（发饰、表情符号、几何分镜、背景图案化处理）。
将宽高比设为 3:4
""".strip()

# For GPT image models this is folded into the main prompt text. Leave empty if
# you prefer writing all avoid rules directly in PROMPT.
NEGATIVE_PROMPT = ""

# Image settings.
WIDTH = 1024
HEIGHT = 1024
COUNT = 3

# Optional seed. Set to None for random.
SEED: int | None = None


# ---------------------------------------------------------------------------
# RUNNER
# ---------------------------------------------------------------------------

def _image_extension(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return ".png"


async def main() -> int:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(OUTPUT_ROOT) / f"batch_generate_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    request = GenerateRequest(
        provider=PROVIDER,
        prompt=PROMPT,
        negative_prompt=NEGATIVE_PROMPT,
        width=WIDTH,
        height=HEIGHT,
        count=COUNT,
        seed=SEED,
    )

    async with ImageService(CONFIG_PATH) as service:
        batch = await service.generate(request)

    manifest = {
        "run_id": run_id,
        "config": CONFIG_PATH,
        "provider": PROVIDER,
        "prompt": PROMPT,
        "negative_prompt": NEGATIVE_PROMPT,
        "width": WIDTH,
        "height": HEIGHT,
        "count": COUNT,
        "seed": SEED,
        "success_count": batch.success_count,
        "errors": batch.errors,
        "files": [],
    }

    for index, result in enumerate(batch.results):
        ext = _image_extension(result.image_bytes)
        image_path = output_dir / f"generated_{index:02d}{ext}"
        meta_path = output_dir / f"generated_{index:02d}.json"
        image_path.write_bytes(result.image_bytes)
        metadata = {
            "provider": result.provider_name,
            "model": result.model_name,
            "seed": result.seed,
            "generation_params": result.generation_params,
            "cost": result.cost,
            "bytes": len(result.image_bytes),
        }
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["files"].append({
            "image": str(image_path),
            "metadata": str(meta_path),
        })

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(str(output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

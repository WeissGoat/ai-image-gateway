"""Inpaint 业务效果测试 — 表情差分 & 动作差分。

使用 doll_proto_0_stand.png 作为源图，程序化生成 mask，
调用真实 NovelAI API 验证 inpaint 修复后的实际效果。

Usage:
    cd f:\\design\\game\\project\\p3\\tools\\ai-image-gateway
    python examples/test_inpaint_business.py
    python examples/test_inpaint_business.py --test expression   # 只跑表情
    python examples/test_inpaint_business.py --test pose          # 只跑动作
    python examples/test_inpaint_business.py --count 3            # 每个差分生 3 张候选
    python examples/test_inpaint_business.py --seed 12345         # 固定种子
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Fix Windows GBK terminal encoding
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ai_image_gateway import ImageService, InpaintRequest
from ai_image_gateway.auth import resolve_novelai_access_token
from ai_image_gateway.config import (
    GatewayConfig,
    DefaultProviderConfig,
    ProviderConfig,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DOLL_SPRITE = Path(
    r"F:\design\game\project\p3\UnityClient\Assets\Art\Approved\Dolls"
    r"\doll_proto_0_stand.png"
)
DOLL_GENERATION_JSON = Path(
    r"F:\design\game\project\p3\UnityClient\Assets\Art\_IncomingAI"
    r"\doll_proto_0_stand\generation.json"
)
OUTPUT_DIR = Path(__file__).parent / "output" / "inpaint_business_test"

# ---------------------------------------------------------------------------
# Mask generators — programmatic masks for specific regions
# ---------------------------------------------------------------------------


def _create_ellipse_mask(
    img_size: tuple[int, int],
    center: tuple[float, float],
    radii: tuple[float, float],
) -> Image.Image:
    """Create a white ellipse on black background as an inpaint mask.

    Args:
        img_size: (width, height) of the full image.
        center: (cx, cy) as fractions of image size (0..1).
        radii: (rx, ry) as fractions of image size (0..1).
    """
    w, h = img_size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy = int(center[0] * w), int(center[1] * h)
    rx, ry = int(radii[0] * w), int(radii[1] * h)
    draw.ellipse(
        [(cx - rx, cy - ry), (cx + rx, cy + ry)],
        fill=255,
    )
    return mask


def _create_rect_mask(
    img_size: tuple[int, int],
    top_left: tuple[float, float],
    bottom_right: tuple[float, float],
) -> Image.Image:
    """Create a white rectangle on black background as an inpaint mask."""
    w, h = img_size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    x0, y0 = int(top_left[0] * w), int(top_left[1] * h)
    x1, y1 = int(bottom_right[0] * w), int(bottom_right[1] * h)
    draw.rectangle([(x0, y0), (x1, y1)], fill=255)
    return mask


def create_face_mask(img: Image.Image) -> Image.Image:
    """Mask covering the face area of doll_proto_0_stand.

    Based on visual inspection: the head is roughly centered horizontally,
    in the upper ~18% of the image. We use an ellipse to avoid sharp edges.
    """
    w, h = img.size
    # Face center and radii tuned for doll_proto_0_stand.png (832x1216)
    return _create_ellipse_mask(
        (w, h),
        center=(0.50, 0.135),    # center of face
        radii=(0.11, 0.065),     # slightly wider than tall
    )


def create_head_mask(img: Image.Image) -> Image.Image:
    """Larger mask covering the entire head (hair + face + ears)."""
    w, h = img.size
    return _create_ellipse_mask(
        (w, h),
        center=(0.50, 0.115),
        radii=(0.18, 0.11),
    )


def create_upper_body_mask(img: Image.Image) -> Image.Image:
    """Mask covering upper body + arms for pose changes."""
    w, h = img.size
    return _create_rect_mask(
        (w, h),
        top_left=(0.15, 0.18),
        bottom_right=(0.85, 0.55),
    )


def create_arms_mask(img: Image.Image) -> Image.Image:
    """Mask covering both arms for arm-pose changes."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    # Left arm region
    draw.rectangle(
        [(int(0.10 * w), int(0.20 * h)), (int(0.35 * w), int(0.55 * h))],
        fill=255,
    )
    # Right arm region
    draw.rectangle(
        [(int(0.65 * w), int(0.20 * h)), (int(0.90 * w), int(0.55 * h))],
        fill=255,
    )
    return mask


# ---------------------------------------------------------------------------
# Test definitions
# ---------------------------------------------------------------------------


def _load_original_prompts(
    generation_json: Path | None = None,
) -> tuple[str, str]:
    """Load original generation prompts from generation.json.

    Returns (prompt, negative_prompt). Falls back to a generic description
    if the file is unavailable.
    """
    import json as _json

    path = generation_json or DOLL_GENERATION_JSON
    if path.exists():
        data = _json.loads(path.read_text(encoding="utf-8"))
        prompt = data.get("PromptEN", "")
        negative = data.get("NegativePromptEN", "")
        if prompt:
            return prompt, negative

    # Fallback — generic description
    return (
        "1girl, mechanical doll, ball-jointed doll, blonde hair, long hair, "
        "cyan eyes, steampunk, brass joints, standing, white background, "
        "best quality, amazing quality, very aesthetic, absurdres",
        "lowres, text, watermark, signature, blurry, bad anatomy, "
        "extra limbs, extra fingers, missing fingers",
    )


# Loaded at module level; overridden by CLI --generation-json if provided.
DOLL_BASE_PROMPT, DOLL_BASE_NEGATIVE = _load_original_prompts()

EXPRESSION_TESTS: list[dict[str, Any]] = [
    {
        "id": "expr_smile",
        "label": "微笑 (Smile)",
        "mask_fn": create_face_mask,
        "prompt_addition": "gentle smile, happy expression, soft eyes",
        "strength": 0.65,
        "inpaint_i2i_strength": 0.8,
    },
    {
        "id": "expr_sad",
        "label": "悲伤 (Sad)",
        "mask_fn": create_face_mask,
        "prompt_addition": "sad expression, teary eyes, melancholy, looking down",
        "strength": 0.65,
        "inpaint_i2i_strength": 0.8,
    },
    {
        "id": "expr_angry",
        "label": "愤怒 (Angry)",
        "mask_fn": create_face_mask,
        "prompt_addition": "angry expression, furrowed brows, glaring eyes, fierce look",
        "strength": 0.65,
        "inpaint_i2i_strength": 0.8,
    },
    {
        "id": "expr_surprised",
        "label": "惊讶 (Surprised)",
        "mask_fn": create_face_mask,
        "prompt_addition": "surprised expression, wide eyes, open mouth, shocked",
        "strength": 0.65,
        "inpaint_i2i_strength": 0.8,
    },
    {
        "id": "expr_blush",
        "label": "害羞 (Shy/Blush)",
        "mask_fn": create_face_mask,
        "prompt_addition": "embarrassed, blush, looking away, shy expression",
        "strength": 0.65,
        "inpaint_i2i_strength": 0.8,
    },
]

POSE_TESTS: list[dict[str, Any]] = [
    {
        "id": "pose_arms_up",
        "label": "举手 (Arms Up)",
        "mask_fn": create_upper_body_mask,
        "prompt_addition": "arms raised, hands up, waving, reaching upward",
        "strength": 0.75,
        "inpaint_i2i_strength": 0.9,
    },
    {
        "id": "pose_arms_crossed",
        "label": "抱臂 (Arms Crossed)",
        "mask_fn": create_upper_body_mask,
        "prompt_addition": "arms crossed, arms folded across chest, confident pose",
        "strength": 0.75,
        "inpaint_i2i_strength": 0.9,
    },
    {
        "id": "pose_hand_on_chest",
        "label": "手放胸口 (Hand on Chest)",
        "mask_fn": create_upper_body_mask,
        "prompt_addition": "one hand on chest, touching heart, gentle gesture",
        "strength": 0.70,
        "inpaint_i2i_strength": 0.85,
    },
    {
        "id": "pose_combat_ready",
        "label": "战斗姿态 (Combat Ready)",
        "mask_fn": create_upper_body_mask,
        "prompt_addition": "combat stance, fighting pose, fists raised, ready to fight",
        "strength": 0.80,
        "inpaint_i2i_strength": 0.95,
    },
]


# ---------------------------------------------------------------------------
# Contact sheet builder
# ---------------------------------------------------------------------------


def _make_contact_sheet(
    source_img: Image.Image,
    results: list[dict[str, Any]],
    title: str,
    output_path: Path,
) -> None:
    """Build a visual comparison sheet: source | mask overlay | each candidate."""
    if not results:
        return

    # Use 512px wide thumbnails for contact sheet
    thumb_w = 420
    thumb_h = int(thumb_w * source_img.height / source_img.width)
    padding = 16
    label_h = 30

    all_images: list[tuple[Image.Image, str]] = []
    # Add source
    all_images.append((source_img.copy(), "原图 (Source)"))

    for entry in results:
        # Mask overlay
        mask_overlay = source_img.copy().convert("RGBA")
        mask_img = entry["mask"]
        red_overlay = Image.new("RGBA", mask_overlay.size, (255, 0, 0, 100))
        mask_alpha = mask_img.resize(mask_overlay.size, Image.Resampling.NEAREST)
        mask_overlay.paste(
            red_overlay, (0, 0),
            mask_alpha,
        )
        all_images.append((mask_overlay, f"Mask: {entry['label']}"))

        for i, img_bytes in enumerate(entry["outputs"]):
            out_img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
            label = f"{entry['label']} #{i}"
            if entry.get("seeds") and i < len(entry["seeds"]):
                label += f" (seed={entry['seeds'][i]})"
            all_images.append((out_img, label))

    # Layout: fit max 6 per row
    cols = min(len(all_images), 6)
    rows = (len(all_images) + cols - 1) // cols
    sheet_w = cols * (thumb_w + padding) + padding
    sheet_h = rows * (thumb_h + label_h + padding) + padding + 40  # +40 for title

    sheet = Image.new("RGB", (sheet_w, sheet_h), (32, 32, 48))
    draw = ImageDraw.Draw(sheet)

    # Title
    try:
        title_font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        title_font = ImageFont.load_default()
    draw.text((padding, 8), title, fill=(240, 200, 100), font=title_font)

    try:
        label_font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        label_font = ImageFont.load_default()

    for idx, (img, label) in enumerate(all_images):
        col = idx % cols
        row = idx // cols
        x = padding + col * (thumb_w + padding)
        y = 40 + padding + row * (thumb_h + label_h + padding)

        thumb = img.copy()
        thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        # Paste on white background for transparency
        bg = Image.new("RGB", (thumb_w, thumb_h), (255, 255, 255))
        offset_x = (thumb_w - thumb.width) // 2
        offset_y = (thumb_h - thumb.height) // 2
        if thumb.mode == "RGBA":
            bg.paste(thumb, (offset_x, offset_y), thumb)
        else:
            bg.paste(thumb, (offset_x, offset_y))
        sheet.paste(bg, (x, y))
        draw.text((x + 4, y + thumb_h + 2), label, fill=(200, 200, 200), font=label_font)

    sheet.save(output_path, quality=95)
    print(f"  📋 Contact sheet: {output_path}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _pil_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def run_test(
    test_defs: list[dict[str, Any]],
    source_img: Image.Image,
    source_bytes: bytes,
    service: ImageService,
    *,
    count: int = 1,
    base_seed: int | None = None,
    test_label: str = "Test",
) -> list[dict[str, Any]]:
    """Run a batch of inpaint tests and return results."""
    results: list[dict[str, Any]] = []

    for i, test_def in enumerate(test_defs):
        test_id = test_def["id"]
        label = test_def["label"]
        mask_fn = test_def["mask_fn"]
        prompt_add = test_def["prompt_addition"]
        strength = test_def.get("strength", 0.7)
        inpaint_strength = test_def.get("inpaint_i2i_strength", 1.0)

        print(f"\n  [{i+1}/{len(test_defs)}] {label} ({test_id})...")

        # Generate mask
        mask_img = mask_fn(source_img)
        mask_bytes = _pil_to_bytes(mask_img)

        # Save mask for debugging
        mask_out = OUTPUT_DIR / f"{test_id}_mask.png"
        mask_img.save(mask_out)

        full_prompt = f"{DOLL_BASE_PROMPT}, {prompt_add}"
        seed = (base_seed + i * count) if base_seed is not None else None

        t0 = time.time()
        batch = await service.inpaint(InpaintRequest(
            image=source_bytes,
            mask=mask_bytes,
            prompt=full_prompt,
            negative_prompt=DOLL_BASE_NEGATIVE,
            count=count,
            seed=seed,
            provider="novelai",
            extra={
                "strength": strength,
                "inpaint_i2i_strength": inpaint_strength,
                "noise": 0.0,
            },
        ))
        elapsed = time.time() - t0

        if batch.errors:
            print(f"    ⚠️ Errors: {batch.errors}")

        output_bytes: list[bytes] = []
        seeds: list[int] = []
        for j, result in enumerate(batch.results):
            # Save raw inpaint output
            raw_path = OUTPUT_DIR / f"{test_id}_raw_{j:02d}_seed{result.seed}.png"
            raw_path.write_bytes(result.image_bytes)

            # Composite: paste inpainted region back onto source
            from ai_image_gateway.workflows.p3_live2d_inpaint import (
                _composite_masked_output,
            )
            composited = _composite_masked_output(
                source_bytes=source_bytes,
                mask_bytes=mask_bytes,
                generated_bytes=result.image_bytes,
                mask_feather=2.0,
            )
            comp_path = OUTPUT_DIR / f"{test_id}_comp_{j:02d}_seed{result.seed}.png"
            comp_path.write_bytes(composited)

            output_bytes.append(composited)
            seeds.append(result.seed or 0)
            print(f"    ✅ Candidate {j}: seed={result.seed}, {len(result.image_bytes)} bytes, {elapsed:.1f}s")

        results.append({
            "id": test_id,
            "label": label,
            "mask": mask_img,
            "outputs": output_bytes,
            "seeds": seeds,
            "elapsed": elapsed,
        })

    return results


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inpaint business test: expression & pose variants")
    parser.add_argument("--test", choices=["expression", "pose", "all"], default="all",
                        help="Which test suite to run")
    parser.add_argument("--count", type=int, default=1, help="Candidates per variant")
    parser.add_argument("--seed", type=int, default=None, help="Base seed (None=random)")
    parser.add_argument("--source", type=str, default=None, help="Override source image path")
    parser.add_argument("--generation-json", type=str, default=None,
                        help="Path to generation.json for original prompts")
    args = parser.parse_args(argv)

    # Reload prompts from CLI-specified generation.json if provided
    global DOLL_BASE_PROMPT, DOLL_BASE_NEGATIVE  # noqa: PLW0603
    gen_json_path = Path(args.generation_json) if args.generation_json else None
    DOLL_BASE_PROMPT, DOLL_BASE_NEGATIVE = _load_original_prompts(gen_json_path)

    # Resolve source image
    source_path = Path(args.source) if args.source else DOLL_SPRITE
    if not source_path.exists():
        print(f"❌ Source image not found: {source_path}")
        return 1
    print(f"📷 Source: {source_path}")

    source_img = Image.open(source_path)
    print(f"   Size: {source_img.size}, Mode: {source_img.mode}")
    source_bytes = source_path.read_bytes()

    # Resolve NAI token
    access_token = resolve_novelai_access_token()
    if not access_token:
        print("❌ No NAI access token. Set NAI_ACCESS_TOKEN or point NAI_CLIENT_PY.")
        return 1
    print(f"🔑 NAI token resolved ({len(access_token)} chars)")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📁 Output: {OUTPUT_DIR}")
    print(f"📝 Base prompt ({len(DOLL_BASE_PROMPT)} chars): {DOLL_BASE_PROMPT[:100]}...")
    print(f"   Negative ({len(DOLL_BASE_NEGATIVE)} chars): {DOLL_BASE_NEGATIVE[:80]}...")

    config = GatewayConfig(
        default_provider=DefaultProviderConfig(
            generate="novelai",
            inpaint="novelai",
        ),
        providers={
            "novelai": ProviderConfig(
                enabled=True,
                auth={"access_token": access_token},
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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*60}")
    print(f"  Inpaint Business Test — {timestamp}")
    print(f"{'='*60}")

    async with ImageService(config) as service:
        # Test 1: Expression variants
        if args.test in ("expression", "all"):
            print(f"\n{'─'*60}")
            print(f"  🎭 Test 1: 表情差分 (Expression Variants)")
            print(f"{'─'*60}")

            expr_results = await run_test(
                EXPRESSION_TESTS,
                source_img,
                source_bytes,
                service,
                count=args.count,
                base_seed=args.seed,
                test_label="Expression",
            )

            _make_contact_sheet(
                source_img,
                expr_results,
                f"表情差分 (Expression Variants) — {timestamp}",
                OUTPUT_DIR / f"contact_expression_{timestamp}.png",
            )

        # Test 2: Pose variants
        if args.test in ("pose", "all"):
            print(f"\n{'─'*60}")
            print(f"  💪 Test 2: 动作差分 (Pose Variants)")
            print(f"{'─'*60}")

            pose_results = await run_test(
                POSE_TESTS,
                source_img,
                source_bytes,
                service,
                count=args.count,
                base_seed=(args.seed + 1000) if args.seed is not None else None,
                test_label="Pose",
            )

            _make_contact_sheet(
                source_img,
                pose_results,
                f"动作差分 (Pose Variants) — {timestamp}",
                OUTPUT_DIR / f"contact_pose_{timestamp}.png",
            )

    print(f"\n{'='*60}")
    print(f"  ✅ Done! Results in: {OUTPUT_DIR}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

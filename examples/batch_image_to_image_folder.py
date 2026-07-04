"""Batch image-to-image over a folder with one shared prompt.

Example:

python examples/batch_image_to_image_folder.py ^
  --config config.local.yaml ^
  --provider openai_images ^
  --input-dir F:\path\to\input_images ^
  --out-dir F:\path\to\outputs ^
  --prompt "Turn this into a polished blue crystal game icon, no text."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from ai_image_gateway import ImageService, ImageToImageRequest, resolve_image_input


DEFAULT_PATTERNS = ("*.png", "*.jpg", "*.jpeg", "*.webp")


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return stem or "image"


def _image_extension(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _collect_inputs(input_dir: Path, patterns: list[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        iterator = input_dir.rglob(pattern) if recursive else input_dir.glob(pattern)
        files.extend(path for path in iterator if path.is_file())
    return sorted(set(files))


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return args.prompt_file.read_text(encoding="utf-8").strip()
    return (args.prompt or "").strip()


async def _process_one(
    *,
    service: ImageService,
    provider: str,
    image_path: Path,
    output_dir: Path,
    prompt: str,
    negative_prompt: str,
    width: int | None,
    height: int | None,
    count: int,
) -> dict:
    resolved = await resolve_image_input(image_path)
    batch = await service.image_to_image(ImageToImageRequest(
        provider=provider,
        images=[resolved.image_bytes],
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        count=count,
    ))

    item_dir = output_dir / _safe_stem(image_path.stem)
    item_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "source": str(image_path),
        "source_mime": resolved.mime_type,
        "provider": provider,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "width": width,
        "height": height,
        "count": count,
        "success_count": batch.success_count,
        "errors": batch.errors,
        "files": [],
    }

    for index, result in enumerate(batch.results):
        ext = _image_extension(result.image_bytes)
        image_out = item_dir / f"{_safe_stem(image_path.stem)}_{index:02d}{ext}"
        image_out.write_bytes(result.image_bytes)
        meta_out = item_dir / f"{_safe_stem(image_path.stem)}_{index:02d}.json"
        metadata = {
            "source": str(image_path),
            "image": str(image_out),
            "provider": result.provider_name,
            "model": result.model_name,
            "seed": result.seed,
            "generation_params": result.generation_params,
            "cost": result.cost,
            "bytes": len(result.image_bytes),
        }
        meta_out.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        record["files"].append({
            "image": str(image_out),
            "metadata": str(meta_out),
        })

    (item_dir / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.local.yaml", help="Gateway config path.")
    parser.add_argument("--provider", default="openai_images", help="Image-to-image provider name.")
    parser.add_argument("--input-dir", type=Path, required=True, help="Folder of source images.")
    parser.add_argument("--out-dir", type=Path, help="Output folder. Defaults to examples/output/batch_i2i_<timestamp>.")
    parser.add_argument("--prompt", help="Shared image-to-image prompt.")
    parser.add_argument("--prompt-file", type=Path, help="Read the shared prompt from a UTF-8 text file.")
    parser.add_argument("--negative-prompt", default="", help="Optional shared negative prompt.")
    parser.add_argument("--pattern", action="append", help="Glob pattern. Can be repeated. Defaults to common image types.")
    parser.add_argument("--recursive", action="store_true", help="Scan input folder recursively.")
    parser.add_argument("--width", type=int, default=1024, help="Requested output width.")
    parser.add_argument("--height", type=int, default=1024, help="Requested output height.")
    parser.add_argument("--count", type=int, default=1, help="Outputs per source image.")
    parser.add_argument("--limit", type=int, help="Process only the first N matched images.")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds to wait between images.")
    parser.add_argument("--dry-run", action="store_true", help="List inputs and exit without calling providers.")
    args = parser.parse_args()

    prompt = _read_prompt(args)
    if not prompt:
        raise SystemExit("Provide --prompt or --prompt-file.")
    if not args.input_dir.exists() or not args.input_dir.is_dir():
        raise SystemExit(f"Input directory not found: {args.input_dir}")

    patterns = args.pattern or list(DEFAULT_PATTERNS)
    inputs = _collect_inputs(args.input_dir, patterns, args.recursive)
    if args.limit is not None:
        inputs = inputs[:args.limit]
    if not inputs:
        raise SystemExit(f"No input images found in {args.input_dir}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.out_dir or Path("examples") / "output" / f"batch_i2i_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at": timestamp,
        "config": str(args.config),
        "provider": args.provider,
        "input_dir": str(args.input_dir),
        "output_dir": str(output_dir),
        "patterns": patterns,
        "recursive": args.recursive,
        "prompt": prompt,
        "negative_prompt": args.negative_prompt,
        "width": args.width,
        "height": args.height,
        "count": args.count,
        "inputs": [str(path) for path in inputs],
        "results": [],
    }

    if args.dry_run:
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(output_dir))
        return 0

    async with ImageService(args.config) as service:
        for index, image_path in enumerate(inputs, start=1):
            print(f"[{index}/{len(inputs)}] {image_path}", flush=True)
            record = await _process_one(
                service=service,
                provider=args.provider,
                image_path=image_path,
                output_dir=output_dir,
                prompt=prompt,
                negative_prompt=args.negative_prompt,
                width=args.width,
                height=args.height,
                count=args.count,
            )
            manifest["results"].append(record)
            (output_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if index < len(inputs) and args.delay > 0:
                await asyncio.sleep(args.delay)

    print(str(output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

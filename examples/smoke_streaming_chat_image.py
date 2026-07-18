"""Run one transparent streaming chat-image request and save safe evidence."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Sequence

from PIL import Image

from ai_image_gateway import (
    GenerateRequest,
    ImageResult,
    ImageService,
    ImageToImageRequest,
    resolve_image_inputs,
)


_STREAM_EVIDENCE_KEYS = (
    "stream_requested",
    "stream_response_mode",
    "stream_event_count",
    "stream_first_event_elapsed_s",
    "stream_completed_by_done",
)
_DATA_URL_RE = re.compile(
    r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"Bearer\s+[^\s,;]+", re.IGNORECASE)
_LONG_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{128,}(?![A-Za-z0-9+/=])")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--provider", default="gemini_chat_image")
    parser.add_argument(
        "--mode",
        choices=("generate", "image_to_image"),
        required=True,
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=180)
    parser.add_argument("--output-dir", type=Path)
    return parser


def sanitize_error(value: str, *, limit: int = 1000) -> str:
    sanitized = _DATA_URL_RE.sub("<redacted-data-url>", value)
    sanitized = _BEARER_RE.sub("Bearer <redacted>", sanitized)
    sanitized = _LONG_BASE64_RE.sub("<redacted-base64>", sanitized)
    return sanitized[:limit]


def build_summary(
    *,
    mode: str,
    elapsed_s: float,
    result: ImageResult,
    output_path: str | Path,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": "ok",
        "mode": mode,
        "provider": result.provider_name,
        "model": result.model_name,
        "elapsed_s": round(elapsed_s, 3),
        "bytes": len(result.image_bytes),
        "output_path": str(output_path),
    }
    for key in _STREAM_EVIDENCE_KEYS:
        if key in result.generation_params:
            summary[key] = result.generation_params[key]
    return summary


def build_failure_summary(
    *,
    mode: str,
    provider: str,
    elapsed_s: float,
    error: Exception,
) -> dict[str, Any]:
    return {
        "status": "fail",
        "mode": mode,
        "provider": provider,
        "elapsed_s": round(elapsed_s, 3),
        "error_type": type(error).__name__,
        "error": sanitize_error(str(error)),
        "validation_limited": "stream_request_failed_before_success_evidence",
    }


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(tempfile.gettempdir()) / "P3StreamingImageSmoke" / stamp


def _image_suffix(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _verify_image(image_bytes: bytes) -> None:
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.verify()


async def run_smoke(args: argparse.Namespace) -> int:
    output_dir = args.output_dir or _default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    started_at = monotonic()

    try:
        async with ImageService(args.config) as service:
            if args.mode == "image_to_image":
                if not args.image:
                    raise ValueError("--image is required for image_to_image mode")
                resolved = await resolve_image_inputs(args.image)
                if any(item.mime_type == "image/gif" for item in resolved):
                    raise ValueError(
                        "GIF input is not accepted by this smoke; pass an extracted PNG frame"
                    )
                batch = await service.image_to_image(ImageToImageRequest(
                    images=[item.image_bytes for item in resolved],
                    prompt=args.prompt,
                    width=args.width,
                    height=args.height,
                    provider=args.provider,
                    extra={"stream": True},
                ))
            else:
                batch = await service.generate(GenerateRequest(
                    prompt=args.prompt,
                    width=args.width,
                    height=args.height,
                    provider=args.provider,
                    extra={"stream": True},
                ))

        if not batch.results:
            detail = "; ".join(batch.errors) or "Provider returned no image result"
            raise RuntimeError(detail)

        result = batch.results[0]
        _verify_image(result.image_bytes)
        output_path = output_dir / f"result{_image_suffix(result.image_bytes)}"
        output_path.write_bytes(result.image_bytes)
        summary = build_summary(
            mode=args.mode,
            elapsed_s=monotonic() - started_at,
            result=result,
            output_path=output_path.resolve(),
        )
        exit_code = 0
    except Exception as error:
        summary = build_failure_summary(
            mode=args.mode,
            provider=args.provider,
            elapsed_s=monotonic() - started_at,
            error=error,
        )
        exit_code = 1

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run_smoke(args))


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for the transparent streaming smoke command."""

from __future__ import annotations

import json

from ai_image_gateway.schema import ImageResult
from examples.smoke_streaming_chat_image import build_parser, build_summary, sanitize_error


def _fake_image_result(generation_params: dict) -> ImageResult:
    return ImageResult(
        image_bytes=b"\x89PNG\r\n\x1a\nfixture",
        provider_name="gemini_chat_image",
        model_name="gemini-3.1-flash-image",
        generation_params=generation_params,
    )


def test_build_summary_exposes_stream_evidence_without_prompt_or_base64():
    summary = build_summary(
        mode="image_to_image",
        elapsed_s=130.0,
        result=_fake_image_result({
            "stream_requested": True,
            "stream_response_mode": "sse",
            "stream_event_count": 4,
            "stream_first_event_elapsed_s": 2.5,
            "stream_completed_by_done": True,
            "prompt": "must not leak",
            "b64_json": "must not leak",
        }),
        output_path=(
            r"C:\Users\WhiteSheep\AppData\Local\Temp\P3StreamingImageSmoke"
            r"\20260718_150000\result.png"
        ),
    )

    assert summary["status"] == "ok"
    assert summary["stream_first_event_elapsed_s"] == 2.5
    assert "prompt" not in summary
    assert "b64_json" not in json.dumps(summary)


def test_parser_supports_repeatable_images_and_streaming_defaults():
    args = build_parser().parse_args([
        "--config",
        "config.local.yaml",
        "--mode",
        "image_to_image",
        "--prompt",
        "replace character",
        "--image",
        "reference.png",
        "--image",
        "frame.png",
    ])

    assert args.provider == "gemini_chat_image"
    assert args.width == 320
    assert args.height == 180
    assert args.image == ["reference.png", "frame.png"]


def test_sanitize_error_redacts_data_urls_bearer_tokens_and_long_base64():
    raw = (
        "Bearer secret-token data:image/png;base64,"
        + "A" * 300
        + " tail "
        + "B" * 300
    )

    sanitized = sanitize_error(raw)

    assert "secret-token" not in sanitized
    assert "data:image" not in sanitized
    assert "A" * 100 not in sanitized
    assert "B" * 100 not in sanitized

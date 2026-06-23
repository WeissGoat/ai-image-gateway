"""P3 Live2D inpaint candidate runner.

This workflow reads a Project P3 Live2D ``generation.json`` request package,
runs eligible inpaint requests serially, and writes candidate images back only
to the Live2D workspace. It deliberately does not touch Approved art, Manifest,
Registry, or generated handoff queues.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from ..config import GatewayConfig
from ..schema import InpaintRequest
from ..service import ImageService


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _resolve_project_path(path_ref: str | Path, generation_json: Path) -> Path:
    path = Path(path_ref)
    if path.is_absolute():
        return path

    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate

    for parent in [generation_json.parent, *generation_json.parents]:
        candidate = parent / path
        if candidate.exists():
            return candidate

    return generation_json.parent / path


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index:02d}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free output path near {path}")


def _mask_to_alpha(
    mask_bytes: bytes,
    target_size: tuple[int, int],
    *,
    feather: float = 2.0,
) -> Image.Image:
    """Convert an inpaint mask into a binary alpha mask for local compositing."""
    raw_mask = Image.open(io.BytesIO(mask_bytes)).convert("RGBA")
    alpha = np.array(raw_mask.getchannel("A"))
    luma = np.array(raw_mask.convert("L"))
    if alpha.min() < 255:
        binary = (alpha > 0).astype(np.uint8) * 255
    else:
        binary = (luma > 128).astype(np.uint8) * 255
    mask = Image.fromarray(binary, "L")
    if mask.size != target_size:
        mask = mask.resize(target_size, Image.Resampling.NEAREST)
    if feather > 0:
        softened = mask.filter(ImageFilter.GaussianBlur(feather))
        mask = ImageChops.multiply(softened, mask)
    return mask


def _composite_masked_output(
    *,
    source_bytes: bytes,
    mask_bytes: bytes,
    generated_bytes: bytes,
    mask_feather: float = 2.0,
) -> bytes:
    """Paste the provider result back into the original image only inside mask.

    After compositing, the source image's original alpha channel is restored
    so that originally-transparent regions stay transparent — the inpaint
    provider often fills those areas with solid colour (typically black).
    """
    source = Image.open(io.BytesIO(source_bytes)).convert("RGBA")
    generated = Image.open(io.BytesIO(generated_bytes)).convert("RGBA")
    if generated.size != source.size:
        generated = generated.resize(source.size, Image.Resampling.LANCZOS)
    mask = _mask_to_alpha(mask_bytes, source.size, feather=mask_feather)
    source_alpha = source.getchannel("A")
    composited = source.copy()
    composited.paste(generated, (0, 0), mask)
    # Restore original alpha — keeps transparent regions intact.
    composited.putalpha(source_alpha)
    buf = io.BytesIO()
    composited.save(buf, format="PNG")
    return buf.getvalue()


def _select_requests(
    generation_data: dict[str, Any],
    request_ids: Sequence[str] | None,
) -> list[dict[str, Any]]:
    requests = generation_data.get("CandidateRequests", [])
    if request_ids is None:
        return list(requests)
    wanted = set(request_ids)
    return [request for request in requests if request.get("Id") in wanted]


def _record_request_status(
    request: dict[str, Any],
    *,
    status: str,
    reason: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    output_files: list[str] | None = None,
    errors: list[str] | None = None,
) -> None:
    request["Status"] = status
    request["UpdatedAt"] = _utc_now_iso()
    if reason:
        request["StatusReason"] = reason
    else:
        request.pop("StatusReason", None)
    if provider:
        request["Provider"] = provider
    if model:
        request["Model"] = model
    if output_files is not None:
        request["OutputFiles"] = output_files
    elif status != "generated":
        request.pop("OutputFiles", None)
    if errors is not None:
        request["Errors"] = errors
    else:
        request.pop("Errors", None)


async def run_p3_live2d_inpaint(
    *,
    generation_json: str | Path,
    config: str | Path | GatewayConfig | None = None,
    source_image: str | Path | None = None,
    provider: str | None = None,
    request_ids: Sequence[str] | None = None,
    count: int = 1,
    seed: int | None = None,
    add_original_image: bool = False,
    composite_masked_output: bool = True,
    composite_mask_feather: float = 2.0,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run serial inpaint jobs for a P3 Live2D request package."""
    generation_path = Path(generation_json).resolve()
    generation_data = _read_json(generation_path)
    workspace_dir = generation_path.parent

    fallback = generation_data.get("Fallback", {})
    source_ref = source_image or fallback.get("ApprovedSpritePath")
    if not source_ref:
        raise ValueError("No source image was provided and Fallback.ApprovedSpritePath is missing.")
    source_path = _resolve_project_path(source_ref, generation_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Source image not found: {source_path}")
    source_bytes = source_path.read_bytes()

    selected_requests = _select_requests(generation_data, request_ids)
    summary: dict[str, Any] = {
        "generation_json": str(generation_path),
        "source_image": str(source_path),
        "dry_run": dry_run,
        "processed": 0,
        "generated": 0,
        "blocked": 0,
        "failed": 0,
        "outputs": [],
        "errors": [],
    }

    async with ImageService(config) as service:
        for index, request_data in enumerate(selected_requests):
            summary["processed"] += 1
            request_id = request_data.get("Id", f"request_{index}")
            mask_ref = request_data.get("MaskTarget")
            if not mask_ref:
                reason = "missing MaskTarget; inpaint requires an explicit mask."
                summary["blocked"] += 1
                summary["errors"].append(f"{request_id}: {reason}")
                if not dry_run:
                    _record_request_status(request_data, status="blocked_missing_mask_target", reason=reason)
                continue

            mask_path = workspace_dir / mask_ref
            if not mask_path.exists():
                reason = f"mask file not found: {mask_ref}"
                summary["blocked"] += 1
                summary["errors"].append(f"{request_id}: {reason}")
                if not dry_run:
                    _record_request_status(request_data, status="blocked_missing_mask", reason=reason)
                continue

            target_folder = workspace_dir / request_data.get("TargetFolder", "inpaint_candidates")
            if dry_run:
                summary["outputs"].append({
                    "request_id": request_id,
                    "target_folder": str(target_folder),
                    "status": "planned",
                })
                continue

            target_folder.mkdir(parents=True, exist_ok=True)
            request_seed = seed + (index * count) if seed is not None else None
            batch = await service.inpaint(InpaintRequest(
                image=source_bytes,
                mask=mask_path.read_bytes(),
                prompt=request_data.get("PromptEN", ""),
                negative_prompt=request_data.get("NegativePromptEN", ""),
                count=count,
                seed=request_seed,
                provider=provider,
                extra={
                    "add_original_image": add_original_image,
                    "inpaint_i2i_strength": request_data.get("InpaintImg2ImgStrength", 1.0),
                    "strength": request_data.get("Strength", 0.7),
                    "noise": request_data.get("Noise", 0.0),
                    "p3_request_id": request_id,
                },
            ))

            if batch.errors or not batch.results:
                errors = batch.errors or ["provider returned no image results"]
                summary["failed"] += 1
                summary["errors"].extend(f"{request_id}: {error}" for error in errors)
                existing_outputs = request_data.get("OutputFiles")
                if existing_outputs:
                    request_data["LatestAttempt"] = {
                        "Status": "failed",
                        "UpdatedAt": _utc_now_iso(),
                        "AddOriginalImage": add_original_image,
                        "Errors": errors,
                    }
                    _record_request_status(
                        request_data,
                        status=request_data.get("Status", "generated"),
                        provider=request_data.get("Provider") or provider,
                        model=request_data.get("Model"),
                        output_files=existing_outputs,
                    )
                else:
                    _record_request_status(
                        request_data,
                        status="failed",
                        reason="provider_error",
                        provider=provider,
                        errors=errors,
                    )
                continue

            output_files: list[str] = []
            model_name = batch.results[0].model_name
            provider_name = batch.results[0].provider_name
            for result_index, result in enumerate(batch.results):
                result_seed = result.seed if result.seed is not None else result_index
                filename = f"{request_id}_{result_index:02d}_seed{result_seed}.png"
                output_path = target_folder / filename
                if not overwrite:
                    output_path = _unique_path(output_path)
                image_bytes = result.image_bytes
                if composite_masked_output:
                    image_bytes = _composite_masked_output(
                        source_bytes=source_bytes,
                        mask_bytes=mask_path.read_bytes(),
                        generated_bytes=result.image_bytes,
                        mask_feather=composite_mask_feather,
                    )
                output_path.write_bytes(image_bytes)
                rel_path = output_path.relative_to(workspace_dir).as_posix()
                output_files.append(rel_path)
                summary["outputs"].append({
                    "request_id": request_id,
                    "path": rel_path,
                    "seed": result.seed,
                    "provider": provider_name,
                    "model": result.model_name,
                    "composite_masked_output": composite_masked_output,
                    "composite_mask_feather": composite_mask_feather if composite_masked_output else 0.0,
                })

            summary["generated"] += len(output_files)
            if request_data.get("ManualCleanupRequired") or request_data.get("ReviewStatus"):
                request_data.setdefault("ReviewStatus", "review_required")
            request_data["CompositeMaskedOutput"] = composite_masked_output
            request_data["CompositeMaskFeather"] = composite_mask_feather if composite_masked_output else 0.0
            _record_request_status(
                request_data,
                status="generated",
                provider=provider_name,
                model=model_name,
                output_files=output_files,
            )

    if not dry_run:
        generated_assets = generation_data.setdefault("GeneratedAssets", [])
        generated_assets.extend(summary["outputs"])
        total_generated = len(generated_assets)
        generation_status = "generated" if summary["generated"] else "blocked"
        review_required = any(
            request.get("ManualCleanupRequired") or request.get("ReviewStatus")
            for request in generation_data.get("CandidateRequests", [])
            if request.get("OutputFiles")
        )
        if summary["generated"] and review_required:
            generation_status = "generated_with_review_required"
        if not summary["generated"] and total_generated:
            generation_status = "generated_with_latest_attempt_failed"
        generation_data["Provider"] = provider or generation_data.get("Provider", "configured")
        if summary["outputs"]:
            generation_data["Model"] = summary["outputs"][0].get("model", generation_data.get("Model", ""))
        generation_data["CandidateGenerationStatus"] = {
            "Status": generation_status,
            "UpdatedAt": _utc_now_iso(),
            "GeneratedCount": summary["generated"] or total_generated,
            "LatestRunGeneratedCount": summary["generated"],
            "BlockedCount": summary["blocked"],
            "FailedCount": summary["failed"],
            "Provider": provider or "configured",
            "SourceImage": str(source_path),
            "AddOriginalImage": add_original_image,
            "CompositeMaskedOutput": composite_masked_output,
            "CompositeMaskFeather": composite_mask_feather if composite_masked_output else 0.0,
            "Note": "Outputs are Live2D source-support candidates only; do not sync raw candidates to Approved or Manifest.",
        }
        if review_required:
            generation_data["CandidateGenerationStatus"]["ReviewStatus"] = "review_required"
        _write_json(generation_path, generation_data)

    return summary


def _split_request_ids(values: Iterable[str] | None) -> list[str] | None:
    if not values:
        return None
    request_ids: list[str] = []
    for value in values:
        request_ids.extend(part.strip() for part in value.split(",") if part.strip())
    return request_ids or None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run P3 Live2D inpaint candidates serially.")
    parser.add_argument("--generation-json", required=True, help="Path to the Live2D generation.json request package.")
    parser.add_argument("--config", help="Gateway config YAML. Defaults to ImageService config resolution.")
    parser.add_argument("--source-image", help="Override source image. Defaults to Fallback.ApprovedSpritePath.")
    parser.add_argument("--provider", help="Provider name, for example novelai or mock.")
    parser.add_argument("--request-id", action="append", help="Request ID to run. May be repeated or comma-separated.")
    parser.add_argument("--count", type=int, default=1, help="Candidate count per request.")
    parser.add_argument("--seed", type=int, help="Base seed. Each request receives seed + request_index * count.")
    parser.add_argument("--add-original-image", action="store_true", help="Ask NovelAI to composite the original image into inpaint output. Defaults to false for ANR-style local inpaint.")
    parser.add_argument("--no-composite-mask", action="store_true", help="Save raw provider frames instead of locally pasting only the masked region back into the source image.")
    parser.add_argument("--composite-mask-feather", type=float, default=2.0, help="Feather radius used when locally compositing provider output inside the mask.")
    parser.add_argument("--dry-run", action="store_true", help="List runnable requests without writing files.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output files with matching names.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    summary = asyncio.run(run_p3_live2d_inpaint(
        generation_json=args.generation_json,
        config=args.config,
        source_image=args.source_image,
        provider=args.provider,
        request_ids=_split_request_ids(args.request_id),
        count=args.count,
        seed=args.seed,
        add_original_image=args.add_original_image,
        composite_masked_output=not args.no_composite_mask,
        composite_mask_feather=args.composite_mask_feather,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    ))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

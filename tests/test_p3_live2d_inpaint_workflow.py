import io
import json
from pathlib import Path

import pytest
from PIL import Image

from ai_image_gateway.config import DefaultProviderConfig, GatewayConfig
from ai_image_gateway.workflows.p3_live2d_inpaint import run_p3_live2d_inpaint


def _write_png(path: Path, size=(128, 192), color=(80, 120, 160, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


def _write_mask_png(path: Path, size=(128, 192), box=(48, 72, 80, 112)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("L", size, 0)
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            img.putpixel((x, y), 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


def _make_live2d_package(tmp_path: Path) -> Path:
    workspace = tmp_path / "UnityClient/Assets/Art/_IncomingAI/DollsLive2D/doll_proto_0"
    source = tmp_path / "UnityClient/Assets/Art/Approved/Dolls/doll_proto_0_stand.png"
    _write_png(source)
    masks = [
        "hair_face_hidden_fill.png",
        "face_expression_tight_fill.png",
        "motion_hit_react_fill.png",
        "motion_repair_react_fill.png",
        "motion_low_san_idle_fill.png",
    ]
    for mask in masks:
        _write_png(workspace / "masks" / mask, color=(255, 255, 255, 255))

    generation_json = workspace / "generation.json"
    generation_json.parent.mkdir(parents=True, exist_ok=True)
    generation_json.write_text(json.dumps({
        "DollID": "doll_proto_0",
        "Provider": "none",
        "Model": "source-intake-only",
        "GeneratedAssets": [],
        "Fallback": {
            "ApprovedSpritePath": "UnityClient/Assets/Art/Approved/Dolls/doll_proto_0_stand.png",
        },
        "CandidateRequests": [
            {
                "Id": "l2d_fill_hair_face_01",
                "TargetFolder": "inpaint_candidates/hair_face/",
                "MaskTarget": "masks/hair_face_hidden_fill.png",
                "PromptEN": "recover hidden hair",
                "NegativePromptEN": "identity drift",
                "ManualCleanupRequired": True,
                "Status": "planned_not_generated",
            },
            {
                "Id": "l2d_expr_blink_01",
                "Type": "expression_delta",
                "ExpressionId": "blink",
                "TargetFolder": "inpaint_candidates/expressions/blink/",
                "MaskTarget": "masks/face_expression_tight_fill.png",
                "PromptEN": "same doll identity, closed eyelids blink expression delta",
                "NegativePromptEN": "identity drift",
                "ManualCleanupRequired": True,
                "Status": "planned_not_generated",
            },
            {
                "Id": "l2d_expr_low_san_01",
                "Type": "expression_delta",
                "ExpressionId": "low_san",
                "TargetFolder": "inpaint_candidates/expressions/low_san/",
                "MaskTarget": "masks/face_expression_tight_fill.png",
                "PromptEN": "same doll identity, low sanity tense expression delta",
                "NegativePromptEN": "identity drift, horror gore",
                "ManualCleanupRequired": True,
                "Status": "planned_not_generated",
            },
            {
                "Id": "l2d_expr_relaxed_01",
                "Type": "expression_delta",
                "ExpressionId": "relaxed",
                "TargetFolder": "inpaint_candidates/expressions/relaxed/",
                "MaskTarget": "masks/face_expression_tight_fill.png",
                "PromptEN": "same doll identity, relaxed repaired expression delta",
                "NegativePromptEN": "identity drift, exaggerated smile",
                "ManualCleanupRequired": True,
                "Status": "planned_not_generated",
            },
            {
                "Id": "l2d_motion_hit_react_01",
                "Type": "motion_delta",
                "MotionId": "hit_react",
                "TargetFolder": "inpaint_candidates/motions/hit_react/",
                "MaskTarget": "masks/motion_hit_react_fill.png",
                "PromptEN": "same doll identity, hit reaction action delta, head and shoulders recoil",
                "NegativePromptEN": "identity drift, extra limbs, changed costume",
                "ManualCleanupRequired": True,
                "Status": "planned_not_generated",
            },
            {
                "Id": "l2d_motion_repair_react_01",
                "Type": "motion_delta",
                "MotionId": "repair_react",
                "TargetFolder": "inpaint_candidates/motions/repair_react/",
                "MaskTarget": "masks/motion_repair_react_fill.png",
                "PromptEN": "same doll identity, repair reaction action delta, softened posture",
                "NegativePromptEN": "identity drift, extra limbs, changed costume",
                "ManualCleanupRequired": True,
                "Status": "planned_not_generated",
            },
            {
                "Id": "l2d_motion_low_san_idle_01",
                "Type": "motion_delta",
                "MotionId": "low_san_idle",
                "TargetFolder": "inpaint_candidates/motions/low_san_idle/",
                "MaskTarget": "masks/motion_low_san_idle_fill.png",
                "PromptEN": "same doll identity, low sanity idle action delta, guarded posture",
                "NegativePromptEN": "identity drift, extra limbs, changed costume",
                "ManualCleanupRequired": True,
                "Status": "planned_not_generated",
            },
            {
                "Id": "l2d_expr_missing_mask_01",
                "Type": "expression_delta",
                "ExpressionId": "missing_mask_contract",
                "TargetFolder": "inpaint_candidates/expressions/missing_mask/",
                "PromptEN": "closed eyelids",
                "NegativePromptEN": "identity drift",
                "Status": "planned_not_generated",
            },
        ],
    }), encoding="utf-8")
    return generation_json


def _request_by_id(data: dict, request_id: str) -> dict:
    return next(request for request in data["CandidateRequests"] if request["Id"] == request_id)


def _assert_generated_request(
    generation_json: Path,
    request: dict,
    *,
    expected_type: str,
    expected_group: str,
    expected_outputs: list[str],
) -> None:
    assert request["Status"] == "generated"
    assert request["Type"] == expected_type
    assert request["Provider"] == "mock"
    assert request["Model"] == "mock-v1"
    assert request["ReviewStatus"] == "review_required"
    assert "Errors" not in request
    assert "StatusReason" not in request
    assert request["OutputFiles"] == expected_outputs
    for rel_path in request["OutputFiles"]:
        assert rel_path.startswith(expected_group)
        assert "Approved/" not in rel_path
        assert "Manifest" not in rel_path
        assert (generation_json.parent / rel_path).exists()


@pytest.mark.asyncio
async def test_run_p3_live2d_inpaint_with_mock_provider(tmp_path):
    generation_json = _make_live2d_package(tmp_path)
    config = GatewayConfig(default_provider=DefaultProviderConfig(inpaint="mock"))

    summary = await run_p3_live2d_inpaint(
        generation_json=generation_json,
        config=config,
        provider="mock",
        request_ids=["l2d_fill_hair_face_01"],
        count=2,
        seed=10,
    )

    assert summary["generated"] == 2
    assert summary["failed"] == 0
    assert summary["blocked"] == 0

    data = json.loads(generation_json.read_text(encoding="utf-8"))
    request = data["CandidateRequests"][0]
    assert request["Status"] == "generated"
    assert request["Provider"] == "mock"
    assert "Errors" not in request
    assert "StatusReason" not in request
    assert request["OutputFiles"] == [
        "inpaint_candidates/hair_face/l2d_fill_hair_face_01_00_seed10.png",
        "inpaint_candidates/hair_face/l2d_fill_hair_face_01_01_seed11.png",
    ]
    for rel_path in request["OutputFiles"]:
        assert (generation_json.parent / rel_path).exists()

    assert request["ReviewStatus"] == "review_required"
    assert data["CandidateGenerationStatus"]["Status"] == "generated_with_review_required"
    assert data["CandidateGenerationStatus"]["GeneratedCount"] == 2
    assert data["CandidateGenerationStatus"]["AddOriginalImage"] is False
    assert data["CandidateGenerationStatus"]["CompositeMaskedOutput"] is True
    assert data["CandidateGenerationStatus"]["CompositeMaskFeather"] == 2.0
    assert data["CandidateGenerationStatus"]["ReviewStatus"] == "review_required"


@pytest.mark.asyncio
async def test_run_p3_live2d_inpaint_composites_provider_result_inside_mask(tmp_path):
    generation_json = _make_live2d_package(tmp_path)
    workspace = generation_json.parent
    source = tmp_path / "UnityClient/Assets/Art/Approved/Dolls/doll_proto_0_stand.png"
    _write_png(source, color=(10, 20, 30, 255))
    _write_mask_png(workspace / "masks" / "hair_face_hidden_fill.png")
    config = GatewayConfig(default_provider=DefaultProviderConfig(inpaint="mock"))

    summary = await run_p3_live2d_inpaint(
        generation_json=generation_json,
        config=config,
        provider="mock",
        request_ids=["l2d_fill_hair_face_01"],
        count=1,
        seed=10,
    )

    assert summary["generated"] == 1
    output_path = workspace / summary["outputs"][0]["path"]
    output = Image.open(output_path).convert("RGBA")
    source_img = Image.open(source).convert("RGBA")

    assert output.getpixel((10, 10)) == source_img.getpixel((10, 10))
    assert output.getpixel((64, 90)) != source_img.getpixel((64, 90))

    data = json.loads(generation_json.read_text(encoding="utf-8"))
    request = _request_by_id(data, "l2d_fill_hair_face_01")
    assert request["CompositeMaskedOutput"] is True
    assert request["CompositeMaskFeather"] == 2.0
    assert data["GeneratedAssets"][0]["composite_masked_output"] is True
    assert data["GeneratedAssets"][0]["composite_mask_feather"] == 2.0


@pytest.mark.asyncio
async def test_run_p3_live2d_inpaint_can_save_raw_provider_result(tmp_path):
    generation_json = _make_live2d_package(tmp_path)
    workspace = generation_json.parent
    source = tmp_path / "UnityClient/Assets/Art/Approved/Dolls/doll_proto_0_stand.png"
    _write_png(source, color=(10, 20, 30, 255))
    _write_mask_png(workspace / "masks" / "hair_face_hidden_fill.png")
    config = GatewayConfig(default_provider=DefaultProviderConfig(inpaint="mock"))

    summary = await run_p3_live2d_inpaint(
        generation_json=generation_json,
        config=config,
        provider="mock",
        request_ids=["l2d_fill_hair_face_01"],
        count=1,
        seed=10,
        composite_masked_output=False,
    )

    output_path = workspace / summary["outputs"][0]["path"]
    output = Image.open(output_path).convert("RGBA")
    source_img = Image.open(source).convert("RGBA")

    assert output.getpixel((10, 10)) != source_img.getpixel((10, 10))
    data = json.loads(generation_json.read_text(encoding="utf-8"))
    request = _request_by_id(data, "l2d_fill_hair_face_01")
    assert request["CompositeMaskedOutput"] is False
    assert request["CompositeMaskFeather"] == 0.0
    assert data["CandidateGenerationStatus"]["CompositeMaskedOutput"] is False
    assert data["CandidateGenerationStatus"]["CompositeMaskFeather"] == 0.0


@pytest.mark.asyncio
async def test_run_p3_live2d_inpaint_generates_expression_delta_business_group(tmp_path):
    generation_json = _make_live2d_package(tmp_path)
    config = GatewayConfig(default_provider=DefaultProviderConfig(inpaint="mock"))

    request_ids = [
        "l2d_expr_blink_01",
        "l2d_expr_low_san_01",
        "l2d_expr_relaxed_01",
    ]
    summary = await run_p3_live2d_inpaint(
        generation_json=generation_json,
        config=config,
        provider="mock",
        request_ids=request_ids,
        count=1,
        seed=20261001,
    )

    assert summary["processed"] == 3
    assert summary["generated"] == 3
    assert summary["failed"] == 0
    assert summary["blocked"] == 0

    data = json.loads(generation_json.read_text(encoding="utf-8"))
    expected = {
        "l2d_expr_blink_01": (
            "blink",
            ["inpaint_candidates/expressions/blink/l2d_expr_blink_01_00_seed20261001.png"],
        ),
        "l2d_expr_low_san_01": (
            "low_san",
            ["inpaint_candidates/expressions/low_san/l2d_expr_low_san_01_00_seed20261002.png"],
        ),
        "l2d_expr_relaxed_01": (
            "relaxed",
            ["inpaint_candidates/expressions/relaxed/l2d_expr_relaxed_01_00_seed20261003.png"],
        ),
    }
    for request_id, (expression_id, outputs) in expected.items():
        request = _request_by_id(data, request_id)
        assert request["ExpressionId"] == expression_id
        assert request["ManualCleanupRequired"] is True
        assert request["MaskTarget"] == "masks/face_expression_tight_fill.png"
        _assert_generated_request(
            generation_json,
            request,
            expected_type="expression_delta",
            expected_group="inpaint_candidates/expressions/",
            expected_outputs=outputs,
        )

    assert data["CandidateGenerationStatus"]["Status"] == "generated_with_review_required"
    assert data["CandidateGenerationStatus"]["GeneratedCount"] == 3
    assert data["CandidateGenerationStatus"]["LatestRunGeneratedCount"] == 3
    assert data["CandidateGenerationStatus"]["AddOriginalImage"] is False
    assert data["CandidateGenerationStatus"]["ReviewStatus"] == "review_required"
    assert [asset["request_id"] for asset in data["GeneratedAssets"]] == request_ids


@pytest.mark.asyncio
async def test_run_p3_live2d_inpaint_generates_motion_delta_business_group(tmp_path):
    generation_json = _make_live2d_package(tmp_path)
    config = GatewayConfig(default_provider=DefaultProviderConfig(inpaint="mock"))

    request_ids = [
        "l2d_motion_hit_react_01",
        "l2d_motion_repair_react_01",
        "l2d_motion_low_san_idle_01",
    ]
    summary = await run_p3_live2d_inpaint(
        generation_json=generation_json,
        config=config,
        provider="mock",
        request_ids=request_ids,
        count=2,
        seed=20262001,
    )

    assert summary["processed"] == 3
    assert summary["generated"] == 6
    assert summary["failed"] == 0
    assert summary["blocked"] == 0

    data = json.loads(generation_json.read_text(encoding="utf-8"))
    expected = {
        "l2d_motion_hit_react_01": (
            "hit_react",
            "masks/motion_hit_react_fill.png",
            [
                "inpaint_candidates/motions/hit_react/l2d_motion_hit_react_01_00_seed20262001.png",
                "inpaint_candidates/motions/hit_react/l2d_motion_hit_react_01_01_seed20262002.png",
            ],
        ),
        "l2d_motion_repair_react_01": (
            "repair_react",
            "masks/motion_repair_react_fill.png",
            [
                "inpaint_candidates/motions/repair_react/l2d_motion_repair_react_01_00_seed20262003.png",
                "inpaint_candidates/motions/repair_react/l2d_motion_repair_react_01_01_seed20262004.png",
            ],
        ),
        "l2d_motion_low_san_idle_01": (
            "low_san_idle",
            "masks/motion_low_san_idle_fill.png",
            [
                "inpaint_candidates/motions/low_san_idle/l2d_motion_low_san_idle_01_00_seed20262005.png",
                "inpaint_candidates/motions/low_san_idle/l2d_motion_low_san_idle_01_01_seed20262006.png",
            ],
        ),
    }
    for request_id, (motion_id, mask_target, outputs) in expected.items():
        request = _request_by_id(data, request_id)
        assert request["MotionId"] == motion_id
        assert request["ManualCleanupRequired"] is True
        assert request["MaskTarget"] == mask_target
        _assert_generated_request(
            generation_json,
            request,
            expected_type="motion_delta",
            expected_group="inpaint_candidates/motions/",
            expected_outputs=outputs,
        )

    assert data["CandidateGenerationStatus"]["Status"] == "generated_with_review_required"
    assert data["CandidateGenerationStatus"]["GeneratedCount"] == 6
    assert data["CandidateGenerationStatus"]["LatestRunGeneratedCount"] == 6
    assert data["CandidateGenerationStatus"]["AddOriginalImage"] is False
    assert data["CandidateGenerationStatus"]["ReviewStatus"] == "review_required"
    assert [asset["request_id"] for asset in data["GeneratedAssets"]] == [
        "l2d_motion_hit_react_01",
        "l2d_motion_hit_react_01",
        "l2d_motion_repair_react_01",
        "l2d_motion_repair_react_01",
        "l2d_motion_low_san_idle_01",
        "l2d_motion_low_san_idle_01",
    ]


@pytest.mark.asyncio
async def test_run_p3_live2d_inpaint_blocks_missing_mask_target(tmp_path):
    generation_json = _make_live2d_package(tmp_path)
    config = GatewayConfig(default_provider=DefaultProviderConfig(inpaint="mock"))

    summary = await run_p3_live2d_inpaint(
        generation_json=generation_json,
        config=config,
        provider="mock",
        request_ids=["l2d_expr_missing_mask_01"],
        count=1,
    )

    assert summary["generated"] == 0
    assert summary["blocked"] == 1

    data = json.loads(generation_json.read_text(encoding="utf-8"))
    request = _request_by_id(data, "l2d_expr_missing_mask_01")
    assert request["Status"] == "blocked_missing_mask_target"
    assert data["CandidateGenerationStatus"]["Status"] == "blocked"

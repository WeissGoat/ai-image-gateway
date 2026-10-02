# Task 3 Report

## Status

DONE

## Scope

Implemented Task 3 only in `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway`:

- created `ai_image_gateway/facade/__init__.py`
- created `ai_image_gateway/facade/image_service.py`
- created `ai_image_gateway/facade/batch_service.py`
- updated `ai_image_gateway/service.py` to remain a compatibility shim
- updated `ai_image_gateway/__init__.py` to export `BatchService`
- added `tests/test_image_service.py`
- added `tests/test_batch_service.py`

## TDD Notes

### Red

Command:

```bash
uv run python -m pytest tests/test_image_service.py -v
```

Observed failure:

```text
ModuleNotFoundError: No module named 'ai_image_gateway.facade'
```

### Green

Command:

```bash
uv run python -m pytest tests/test_image_service.py tests/test_batch_service.py tests/test_mock_provider.py -v
```

Observed result:

```text
12 passed
```

## Implementation Notes

- Moved single-request facade behavior into `ai_image_gateway.facade.image_service.ImageService`.
- Moved batch orchestration into `ai_image_gateway.facade.batch_service.BatchService`.
- Preserved old entry compatibility by:
  - keeping `ai_image_gateway.service.ImageService` as an import alias to the new facade module
  - keeping `ImageService.batch_generate`, `batch_inpaint`, and `batch_image_to_image` as delegating wrappers around `BatchService`
- Exported `BatchService` from package root alongside `ImageService`.

## Self-Review

- Verified old `ImageService` batch entrypoints still work through `tests/test_mock_provider.py`.
- Kept the split scoped to the facade layer and public exports only.
- Left unrelated untracked items in `docs/2026-07-05-gateway-refactor-boundary.md`, `outputs/`, and `uv.lock` untouched.

## Concerns

None.

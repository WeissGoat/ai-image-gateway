# ai-image-gateway

`ai-image-gateway` is a provider gateway / transport toolkit for AI image
generation.

It keeps two entry paths stable:

- facade path for lightweight callers
- raw path for callers that already own provider-specific payload construction

## Facade Path

Use the package root for the stable high-level entry point:

```python
from ai_image_gateway import GenerateRequest, ImageService
```

`ImageService` is the lightweight facade. It accepts gateway contracts such as
`GenerateRequest`, `ImageToImageRequest`, and `InpaintRequest`.

## Raw Path

Use the raw client when the caller already has a provider-native payload and
needs transport, retry, and decode without the gateway rewriting request data:

```python
from ai_image_gateway.providers.novelai.raw_client import NovelAIRawClient
from ai_image_gateway.contracts.raw import NovelAIRawPayload
```

`NovelAIRawClient.generate_raw()` sends `NovelAIRawPayload` as-is and returns
structured raw results plus retry metadata.

## Public Imports

Stable package-root imports include:

```python
from ai_image_gateway import (
    GenerateRequest,
    ImageService,
    ImageToImageRequest,
    InpaintRequest,
    NovelAIRawPayload,
    NovelAIRawResult,
    RetryRecord,
)
```

## Architecture Notes

The refactor boundary, architecture spec, and implementation plan live in:

- `docs/2026-07-05-gateway-refactor-boundary.md`
- `docs/2026-07-06-ai-image-gateway-architecture-refactor-spec.md`
- `docs/2026-07-06-ai-image-gateway-architecture-refactor-implementation-plan.md`

Use the facade path for simple generation workflows. Use the raw path for
PromptAtelier-style integrations that must preserve exact provider payloads.

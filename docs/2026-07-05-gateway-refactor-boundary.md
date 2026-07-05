# ai-image-gateway responsibility boundary

Date: 2026-07-05

Branch: `codex/refactor-raw-transport-boundary`

Follow-up architecture docs:

- `docs/2026-07-06-ai-image-gateway-architecture-refactor-spec.md`
- `docs/2026-07-06-ai-image-gateway-architecture-refactor-implementation-plan.md`

This document defines the boundary between PromptAtelier and
`ai-image-gateway`. For the latest package-internal layering, public import
surface, and execution sequence, treat the 2026-07-06 spec and implementation
plan as the primary references. This file remains the background explanation for
why that boundary exists.

## Background

`PromptAtelier / tags_machine_core` already owns the higher-level image
generation workflow:

```text
node reading -> composer -> PromptPolicyPipeline -> renderer -> RenderRequest -> executor -> GenerationResult
```

That business logic includes prompt composition, artist/style assembly,
NovelAI v4/v4.5 prompt shaping, reference/vibe parameters, batch evidence, and
PNG metadata handling. `ai-image-gateway` should not take those responsibilities
back on.

## Current Problem

Before the refactor, the gateway mixed together:

- provider auth and HTTP lifecycle
- retry / timeout behavior
- NovelAI payload construction
- facade-friendly generate flows
- raw transport flows
- project-specific workflow assumptions

That made the package act like both a transport toolkit and a business-logic
renderer.

## Target Responsibility

`ai-image-gateway` should stay focused on:

- provider auth
- HTTP transport lifecycle
- retry / timeout / rate limiting
- sending provider-native requests
- decoding provider-native responses
- exposing a light facade for simple callers
- exposing raw transport for callers that already own payload construction

It should not own:

- PromptAtelier node semantics
- artist / character / action prompt logic
- PromptPolicyPipeline
- RenderRequest / GenerationResult business shaping

## Raw vs Facade

The facade path remains for lightweight callers:

```text
ImageService.generate(GenerateRequest)
```

The raw path exists for integrations that need exact provider payload control:

```text
caller builds NovelAI payload
-> NovelAIRawClient.generate_raw(payload)
-> raw result with decoded image bytes and retry evidence
```

## Refactor Outcome

The refactor direction is therefore:

1. keep business-specific prompt/render logic in PromptAtelier
2. keep provider transport and decoding in `ai-image-gateway`
3. preserve a simple facade entry point without making it the primary path for
   payload-sensitive integrations

For the concrete package layout and task-by-task execution details, continue
from the 2026-07-06 architecture spec and implementation plan listed above.

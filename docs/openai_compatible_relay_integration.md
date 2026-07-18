---
title: OpenAI-Compatible Relay Image Integration
status: active
last_verified: 2026-07-18
---

# OpenAI-Compatible Relay Image Integration

This document records the current `ai-image-gateway` integration for relay
services that expose OpenAI-compatible image APIs.

No API keys or relay secrets should be written here. Use environment variables
such as `AI_IMAGE_PROXY_KEY` and provider-specific `base_url` settings.

## Scope

The relay station describes three image routes:

- GPT image generation: image endpoint only.
- Gemini / Nano Banana style image generation: chat completions endpoint only.
- Grok image generation: chat completions endpoint only.

The gateway implements those routes as standard OpenAI-compatible API surfaces,
not as relay-channel-specific hacks:

- `POST /v1/images/generations`
- `POST /v1/images/edits`
- `POST /v1/chat/completions`

The provider selection is configuration-driven. A model that must use
`chat/completions` should be configured on a chat image provider. A model that
must use `images/generations` or `images/edits` should be configured on the
Images API provider.

## Capability Model

The gateway now separates three image capabilities:

- `generate`: text-to-image generation.
- `image_to_image`: reference-image generation / image-to-image.
- `inpaint`: true masked local repaint.

Important semantic rule:

`/v1/images/edits` and chat requests that include reference images are modeled
as `image_to_image`, not as `inpaint`. `InpaintRequest` is reserved for real
mask-based local repaint workflows such as NovelAI inpaint.

Relevant request types:

- `GenerateRequest`
- `ImageToImageRequest`
- `InpaintRequest`

Relevant service entry points:

- `ImageService.generate()`
- `ImageService.image_to_image()`
- `ImageService.inpaint()`

## Provider Architecture

The router maps a capability to a default provider:

```yaml
default_provider:
  generate: openai_images
  image_to_image: gemini_chat_image
  inpaint: novelai
  upscale: mock
```

Registered providers:

- `openai_images`: standard Images API provider.
- `openai_chat_image`: generic chat-completions image provider.
- `gemini_chat_image`: alias for Gemini / Nano Banana style chat image models.
- `grok_chat_image`: alias for Grok chat image models.
- `novelai`: true inpaint and NovelAI generation workflows.
- `mock`: local deterministic test provider.

The OpenAI-compatible providers use raw `httpx` requests instead of the OpenAI
SDK. This keeps the integration proxy-friendly: only `base_url`, `api_key`,
`model`, endpoint settings, and provider selection need to change.

## Endpoint Mapping

### GPT Image

Use `openai_images`.

Text-to-image:

- Endpoint: `/v1/images/generations`
- Gateway capability: `generate`
- Current tested model: `gpt-image-2`

Reference image / image-to-image:

- Endpoint: `/v1/images/edits`
- Gateway capability: `image_to_image`
- Current relay status: not enabled as the default route.

The gateway sends standard multipart requests for image edits. The current
relay station rejected standard multipart attempts during smoke testing, so GPT
image-to-image should stay disabled for this relay until the relay confirms the
expected edit payload shape.

### Gemini / Nano Banana Style Image

Use `gemini_chat_image`.

Text-to-image:

- Endpoint: `/v1/chat/completions`
- Gateway capability: `generate`
- Current tested model: `gemini-3.1-flash-image`

Reference image / image-to-image:

- Endpoint: `/v1/chat/completions`
- Gateway capability: `image_to_image`
- Current relay status: usable.

Reference images are sent as OpenAI-style chat content parts with data URLs.

### Grok Image

Use `grok_chat_image`.

Text-to-image:

- Endpoint: `/v1/chat/completions`
- Gateway capability: `generate`
- Current tested model: `grok-imagine-image-lite`

Reference image / image-to-image:

- Endpoint: `/v1/chat/completions`
- Gateway capability: `image_to_image`
- Current relay status: not enabled as the default route.

The current relay returned a server-side SSE error for reference-image requests,
so Grok should only be used for text-to-image until the relay behavior changes.

## Chat Payload Rules

Chat image providers intentionally use conservative payloads:

- Do not send Images API string `response_format` values such as `b64_json` to
  `/v1/chat/completions`.
- Do not send `n` by default.
- Allow explicit `n` only when the caller passes it through `extra`.
- Keep `model`, `messages`, and safe passthrough fields such as `temperature`.
- Streaming is disabled by default. `settings.stream: true` enables it, while
  request `extra["stream"]` has final precedence and can enable or disable it.

This avoids relay bans or request rejection caused by using Images API fields on
chat-completions-only image models.

## Response Parsing

Chat image responses are parsed from several common proxy formats:

- `data[].b64_json`
- `data[].url`
- nested JSON fields
- SSE `data:` events
- `data:image/...;base64,...` URLs
- Markdown image links
- bare HTTP(S) image URLs

If an SSE response contains a provider error message, the gateway surfaces that
message instead of returning a generic "No image data found" error.

## Transparent Streaming Transport

When the final chat payload contains `stream: true`, the gateway opens the
request with `httpx.AsyncClient.stream()` and incrementally decodes SSE through
`httpx-sse`. This changes only the provider's internal transport: callers still
await `ImageService.generate()` or `ImageService.image_to_image()` and receive
the existing `BatchResult` / `ImageResult` models.

Transport rules:

- `text/event-stream` is decoded incrementally, including multiline `data:`
  fields and `[DONE]`.
- A clean EOF after valid events is accepted even when a relay omits `[DONE]`.
- Ordinary JSON returned to a stream request is read from the same connection.
- SSE comment heartbeats are consumed by `httpx-sse` to maintain the
  connection, but its public API does not expose a comment heartbeat count.
- A stream failure never triggers an automatic buffered re-submit, avoiding
  duplicate generation and duplicate billing.
- HTTP error excerpts are bounded and secrets, prompts, Base64 payloads, and
  data URLs are not added to transport evidence.

Successful stream requests add these fields to `generation_params`:

```text
stream_requested
stream_response_mode
stream_event_count
stream_first_event_elapsed_s
stream_completed_by_done
```

Focused real-service smoke:

```powershell
python examples/smoke_streaming_chat_image.py `
  --config config.local.yaml `
  --provider gemini_chat_image `
  --mode image_to_image `
  --prompt "Keep the second image composition and replace only its character using the first reference image." `
  --image "C:\path\to\character-reference.png" `
  --image "C:\path\to\extracted-frame.png" `
  --width 320 `
  --height 180
```

The smoke rejects GIF inputs; callers must pass an extracted still frame. Its
temporary output contains the decoded image plus a `summary.json` with elapsed
time and the non-sensitive stream fields above.

## Reference Image Inputs

`ImageToImageRequest` stores provider-facing reference images as bytes.
User-facing runners can normalize common input forms through:

- `resolve_image_input()`
- `resolve_image_inputs()`

Supported inputs:

- raw bytes
- local file paths
- HTTP(S) image URLs
- `data:image/...;base64,...` URLs

## Current Relay Smoke Result

Model list discovered through the standard model-list endpoint:

- `gpt-image-2`
- `gemini-3.1-flash-image`
- `grok-imagine-image-lite`

Known-good routes:

- `openai_images.generate()` with `gpt-image-2`
- `gemini_chat_image.generate()` with `gemini-3.1-flash-image`
- `gemini_chat_image.image_to_image()` with `gemini-3.1-flash-image`
- `grok_chat_image.generate()` with `grok-imagine-image-lite`

Known-bad or not-yet-default routes:

- `openai_images.image_to_image()` with `/v1/images/edits` on the current relay.
- `grok_chat_image.image_to_image()` with reference images on the current relay.

Transparent streaming verification on 2026-07-18:

- Gemini text-to-image succeeded through true SSE streaming in 109.672 seconds.
  The first business event arrived at 0.0 seconds, five events were collected,
  `[DONE]` was received, and the 113,200-byte JPEG decoded successfully.
- Gemini two-reference image-to-image kept the stream connection alive beyond
  the previous 524 window, but the upstream peer closed an incomplete chunked
  response after 292.906 seconds. No decodable image was returned. This is
  `validation_limited:stream_request_failed_before_success_evidence`; it does
  not prove the image-to-image route is end-to-end stable yet.

Recommended default for this relay:

```yaml
default_provider:
  generate: openai_images
  image_to_image: gemini_chat_image
  inpaint: novelai
  upscale: mock
```

Callers that want Grok text-to-image should pass `provider="grok_chat_image"`
explicitly or use a separate config profile.

## Verification

Fresh verification performed for this integration:

- `python -m pytest tests/test_sse_transport.py tests/test_openai_compatible_provider.py tests/test_streaming_smoke_cli.py -q`
- `python -m pytest tests/test_openai_compatible_provider.py tests/test_image_inputs.py -q`
- `python -m pytest tests -q`
- `python -m compileall -q ai_image_gateway examples/smoke_streaming_chat_image.py`
- project docs validation from the P3 root

The relay smoke tests used environment-provided credentials only. No generated
relay images, API keys, or relay secrets were committed.

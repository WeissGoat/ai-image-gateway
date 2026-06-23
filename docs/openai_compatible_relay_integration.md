---
title: OpenAI-Compatible Relay Image Integration
status: active
last_verified: 2026-06-23
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
- Keep `model`, `messages`, `stream: false`, and safe passthrough fields such
  as `temperature`.

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

- `python -m pytest tests/test_openai_compatible_provider.py tests/test_image_inputs.py -q`
- `python -m pytest tests -q`
- project docs validation from the P3 root

The relay smoke tests used environment-provided credentials only. No generated
relay images, API keys, or relay secrets were committed.

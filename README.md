# AI Image Gateway

Provider-agnostic image generation, image-to-image, and inpaint gateway used by
Project P3 asset tooling.

## NovelAI Auth

NovelAI credentials are resolved without writing secrets to config or logs:

1. `config.auth.access_token`
2. `NAI_ACCESS_TOKEN`
3. `NAI_CLIENT_PY`
4. `F:\my_project\new\tags_machine\novelai\client.py`
5. `config.auth.access_key`
6. `config.auth.username` + `config.auth.password`

The `client.py` fallback is parsed for a literal `get_access_token()` return
value. Do not copy the token into this repository.

## NovelAI 4.5 Inpaint

Use the base model in config, for example `nai-diffusion-4-5-full`. The provider
submits inpaint as `action=infill` with the API model
`nai-diffusion-4-5-full-inpainting`.

The request shape follows `F:\my_project\Auto-NovelAI-Refactor`: inpaint starts
from the img2img parameter set, then switches to `action=infill` and adds:

- `parameters.image`
- `parameters.mask`
- `parameters.strength`
- `parameters.noise`
- `parameters.extra_noise_seed`
- `parameters.color_correct`
- `parameters.inpaintImg2ImgStrength`
- `parameters.add_original_image = false` by default

For inpaint payloads the provider applies any free-tier size limit before
encoding the source image and mask, so `parameters.width`,
`parameters.height`, `parameters.image`, and `parameters.mask` stay aligned.
Masks are encoded as full-size binary PNGs, matching the ANR local inpaint
path, instead of the older downscaled RGBA NAI-mask helper used by ComfyUI.

## OpenAI-Compatible Proxy Providers

Use these providers for relay services that expose standard OpenAI-style HTTP
surfaces. They use raw `httpx` requests instead of the OpenAI SDK, so only
`base_url`, `api_key`, and `model` need to change between proxy vendors.

Current relay integration notes and smoke-test results are documented in
`docs/openai_compatible_relay_integration.md`.

- `openai_images`: `POST /v1/images/generations` for text-to-image and
  `POST /v1/images/edits` for reference image edits / image-to-image; use for
  GPT image models exposed through the Images API.
- `openai_chat_image`: `POST /v1/chat/completions`; generic chat image route.
- `gemini_chat_image`: chat image alias for Gemini / Nano Banana style models.
- `grok_chat_image`: chat image alias for Grok image models.

The providers do not fallback across API surfaces. If a proxy requires a model
to use `chat/completions`, configure a chat provider; if it requires
`images/generations` or `images/edits`, configure `openai_images`.

`image_to_image` is distinct from `inpaint`: `/images/edits` and chat requests
with reference images are modeled as reference-image generation. True local
masked repaint remains `InpaintRequest` / `Capability.INPAINT`, currently used
by providers such as NovelAI.

Example:

```yaml
default_provider:
  generate: openai_images
  image_to_image: openai_images
  inpaint: novelai

providers:
  openai_images:
    enabled: true
    auth:
      api_key: ${AI_IMAGE_PROXY_KEY}
    settings:
      base_url: https://proxy.example.com/v1
      model: gpt-image-2
      response_format: b64_json
      size: 1024x1024
      edit_endpoint: /images/edits
      quality: high
      output_format: png

  gemini_chat_image:
    enabled: true
    auth:
      api_key: ${AI_IMAGE_PROXY_KEY}
    settings:
      base_url: https://proxy.example.com/v1
      model: gemini-3.1-flash-image
```

Chat image responses are parsed from common proxy formats: JSON
`b64_json` / `url`, nested JSON fields, SSE `data:` events, data URLs,
Markdown image URLs, and bare HTTP(S) image URLs.

Chat image providers intentionally keep payloads conservative: they do not send
Images API string `response_format` values such as `b64_json`, and they do not
send `n` unless it is explicitly provided through `extra`. Some OpenAI-compatible
image relays reject those fields on `/v1/chat/completions`.

## Reference Image Inputs

Gateway request models keep provider-facing inputs simple: `ImageToImageRequest`
accepts reference images as `bytes`. Runners that accept user-facing references
can use `resolve_image_input()` / `resolve_image_inputs()` to normalize local
paths, HTTP(S) image URLs, raw bytes, or `data:image/...` URLs into bytes plus
MIME metadata before constructing the request.

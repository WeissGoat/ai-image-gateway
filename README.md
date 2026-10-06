# AI 图片网关

`ai-image-gateway` 是图片生成、图生图和局部重绘的 provider 网关 / transport 工具包，
目前由 Project P3 美术流水线和 PromptAtelier（tags_machine）共用。它把不同后端包装成
一致的请求模型，并保持两条稳定入口：

- facade 入口：给轻量调用方按 provider 切换后端，例如 P3 美术流水线和批量脚本。
- raw 入口：给已经自己构造 provider 原生 payload 的调用方，例如 PromptAtelier。

## Facade 入口

稳定的高层入口从包根目录导入：

```python
from ai_image_gateway import GenerateRequest, ImageService
```

`ImageService` 是轻量 facade，接收 `GenerateRequest`、`ImageToImageRequest` 和
`InpaintRequest` 等网关契约。

## Raw 入口

调用方已经持有 provider 原生 payload，只需要网关负责传输、重试和解码、不改写请求
内容时，使用 raw client：

```python
from ai_image_gateway.providers.novelai.raw_client import NovelAIRawClient
from ai_image_gateway.contracts.raw import NovelAIRawPayload
```

`NovelAIRawClient.generate_raw()` 原样发送 `NovelAIRawPayload`，返回结构化的 raw
结果和重试记录。

## 公开导入

包根目录的稳定导入包括：

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

## NovelAI 鉴权

NovelAI 凭据按下面顺序解析，避免把密钥写入配置、文档或日志：

1. `config.auth.access_token`
2. `NAI_ACCESS_TOKEN`
3. `NAI_CLIENT_PY`
4. `F:\my_project\new\tags_machine\novelai\client.py`
5. `config.auth.access_key`
6. `config.auth.username` + `config.auth.password`

`client.py` fallback 只解析字面量 `get_access_token()` 返回值。不要把 token 复制进仓库。

## NovelAI 4.5 局部重绘

配置里使用基础模型，例如 `nai-diffusion-4-5-full`。执行 inpaint 时，provider
会以 `action=infill` 提交请求，并使用 API 模型
`nai-diffusion-4-5-full-inpainting`。

请求结构参考 `F:\my_project\Auto-NovelAI-Refactor`：inpaint 从 img2img 参数集开始，
再切换到 `action=infill`，并补充：

- `parameters.image`
- `parameters.mask`
- `parameters.strength`
- `parameters.noise`
- `parameters.extra_noise_seed`
- `parameters.color_correct`
- `parameters.inpaintImg2ImgStrength`
- `parameters.add_original_image = true`，默认叠加原图（Overlay Original Image），蒙版外保持原图像素；可通过 `extra.add_original_image=false` 关闭

inpaint payload 会先按免费档尺寸限制处理源图和 mask，再编码提交，保证
`parameters.width`、`parameters.height`、`parameters.image` 和 `parameters.mask`
保持对齐。mask 使用全尺寸二值 PNG，匹配 ANR 本地 inpaint 路径，不再使用旧的
ComfyUI 降采样 RGBA mask helper。

## OpenAI 兼容中转 provider

这些 provider 用于接入暴露 OpenAI 风格 HTTP 接口的中转站。实现使用原始 `httpx`
请求，不依赖 OpenAI SDK；跨中转站迁移时通常只需要改 `base_url`、`api_key`、
`model` 和 provider 选择。

当前中转接入说明、后端选择指南和 smoke 结果见
`docs/openai_compatible_relay_integration.md`。Agent 批量出图前应先阅读其中的
后端选择指南，再决定使用 NovelAI、Gemini / Nano Banana 或 GPT image 后端。

- `openai_images`：`POST /v1/images/generations` 用于文生图，`POST /v1/images/edits`
  用于参考图编辑 / 图生图；适合通过 Images API 暴露的 GPT image 模型。
- `openai_chat_image`：`POST /v1/chat/completions`，通用 chat image 路由。
- `gemini_chat_image`：Gemini / Nano Banana 风格 chat image provider；请求宽高会
  映射为 Flow2API 声明的 `generationConfig.imageConfig`，不再依赖 Prompt 尺寸文字。
- `grok_chat_image`：Grok image 模型的 chat image provider 别名。

provider 不会跨 API surface 自动 fallback。如果某个模型只能用 `chat/completions`，
就配置 chat image provider；如果某个模型需要 `images/generations` 或
`images/edits`，就配置 `openai_images`。

`image_to_image` 和 `inpaint` 是不同能力：`/images/edits` 以及带参考图的 chat
请求都归类为参考图生成 / 图生图；真正的遮罩局部重绘仍然使用 `InpaintRequest` /
`Capability.INPAINT`，当前主要由 NovelAI provider 承接。

示例：

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
      stream: true
```

chat image 响应会解析常见中转格式：JSON `b64_json` / `url`、嵌套 JSON 字段、
SSE `data:` 事件、data URL、Markdown 图片链接，以及裸 HTTP(S) 图片 URL。

chat image provider 会刻意保持保守 payload：不会把 Images API 的字符串
`response_format`，例如 `b64_json`，发送到 `/v1/chat/completions`；也不会默认发送
`n`。如果确实要发送 `n`，必须由调用方通过 `extra` 显式传入。部分 OpenAI 兼容
图片中转会拒绝 chat/completions 上的 Images API 字段。

`gemini_chat_image` 是例外的 provider 专属扩展：当请求同时提供正数宽高时，网关
发送 `generationConfig.imageConfig.aspectRatio` 和 `imageSize`，例如
`1024x1536 -> three-four + 2k`，并把实际字段记录到
`generation_params.provider_image_parameters`。这些参数控制画幅与近似输出档位，不
保证精确像素尺寸；relay 不支持任意 `2:3`，所以正式资产仍应在后处理阶段规范化。
`openai_chat_image` 和
`grok_chat_image` 不会收到这些字段。

当最终 chat payload 包含 `stream: true` 时，provider 使用
`httpx.AsyncClient.stream()` 和 `httpx-sse` 增量消费 SSE 事件。如果中转在
stream 请求下仍返回普通 JSON，网关会从同一响应连接解析，不会再次提交 buffered
请求。SSE 注释心跳可以维持连接，但 `httpx-sse` 的公开 API 不暴露心跳计数。

除非 provider settings 或请求 `extra` 启用，否则流式模式保持关闭。请求级
`extra={"stream": false}` 可以覆盖 provider 默认值。成功的流式请求只会把以下
有界传输证据加入 `generation_params`：

- `stream_requested`
- `stream_response_mode`
- `stream_event_count`
- `stream_first_event_elapsed_s`
- `stream_completed_by_done`

可使用以下专项 smoke 命令，在系统临时目录保存一张可解码图片和不含密钥的
`summary.json`：

```powershell
python examples/smoke_streaming_chat_image.py `
  --config config.local.yaml `
  --provider gemini_chat_image `
  --mode generate `
  --prompt "Generate one tiny blue crystal dot on a white background. No text." `
  --width 64 `
  --height 64
```

## 参考图输入

网关请求模型保持 provider-facing 输入简单：`ImageToImageRequest` 接收 `bytes`
形式的参考图。面向用户的 runner 可以使用 `resolve_image_input()` /
`resolve_image_inputs()` 把本地路径、HTTP(S) 图片 URL、原始 bytes 或
`data:image/...` URL 统一解析成图片 bytes 和 MIME 元数据，再构造请求。

## 架构说明

重构边界、架构规格和实施计划见：

- `docs/2026-07-05-gateway-refactor-boundary.md`
- `docs/2026-07-06-ai-image-gateway-architecture-refactor-spec.md`
- `docs/2026-07-06-ai-image-gateway-architecture-refactor-implementation-plan.md`

简单的生成流程走 facade 入口；需要原样保留 provider payload 的集成，例如
PromptAtelier，走 raw 入口。

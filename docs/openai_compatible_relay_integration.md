---
title: OpenAI 兼容中转图片接入
status: active
last_verified: 2026-07-18
---

# OpenAI 兼容中转图片接入

本文记录 `ai-image-gateway` 当前对 OpenAI 兼容图片中转服务的接入方式。

不要把 API key 或中转站密钥写进本文档。密钥应放在环境变量，例如
`AI_IMAGE_PROXY_KEY`，或本地忽略配置里的 provider 专属 `base_url` / `api_key`。

## 范围

当前中转站描述了三类图片路由：

- GPT 生图：只走 image 接口。
- Gemini / Nano Banana 风格生图：只走 chat completions 接口。
- Grok 生图：只走 chat completions 接口。

网关按标准 OpenAI 兼容 API surface 接入这些路由，不做中转站专属 hack：

- `POST /v1/images/generations`
- `POST /v1/images/edits`
- `POST /v1/chat/completions`

provider 选择由配置驱动。必须走 `chat/completions` 的模型应配置到 chat image
provider；必须走 `images/generations` 或 `images/edits` 的模型应配置到
Images API provider。

## 能力模型

网关把图片能力拆成三类：

- `generate`：文生图。
- `image_to_image`：参考图生成 / 图生图。
- `inpaint`：真正的遮罩局部重绘。

重要语义规则：

`/v1/images/edits` 以及带参考图的 chat 请求都归类为 `image_to_image`，不是
`inpaint`。`InpaintRequest` 只保留给真实遮罩局部重绘，例如 NovelAI inpaint。

相关请求类型：

- `GenerateRequest`
- `ImageToImageRequest`
- `InpaintRequest`

相关服务入口：

- `ImageService.generate()`
- `ImageService.image_to_image()`
- `ImageService.inpaint()`

## Provider 架构

router 会把能力映射到默认 provider：

```yaml
default_provider:
  generate: openai_images
  image_to_image: gemini_chat_image
  inpaint: novelai
  upscale: mock
```

已注册 provider：

- `openai_images`：标准 Images API provider。
- `openai_chat_image`：通用 chat completions 图片 provider。
- `gemini_chat_image`：Gemini / Nano Banana 风格 chat image 模型别名。
- `grok_chat_image`：Grok chat image 模型别名。
- `novelai`：NovelAI 生成、真 inpaint 等工作流。
- `mock`：本地确定性测试 provider。

OpenAI 兼容 provider 使用原始 `httpx` 请求，不依赖 OpenAI SDK。这样更适合中转站：
跨供应商迁移时通常只需要改 `base_url`、`api_key`、`model`、endpoint 设置和
provider 选择。

## 后端选择指南

Agent 需要选择生图后端时，按下面口径判断：

- `novelai`：二次元特化后端。适合强二次元、日系动画、角色立绘、萌系 /
  赛璐璐风格，以及需要 NovelAI 原生 prompt / negative prompt 语义的任务。
  当前真遮罩局部重绘也优先走 NovelAI inpaint。
- `gemini_chat_image`：一致性优先后端。适合 `image_to_image`、参考图改图、
  差分生成和保主体任务；当需要尽量保留原图身份、构图、角色设计，只调整风格、
  姿态、质感或局部细节时，优先选择 Gemini / Nano Banana 路线。
- `openai_images`：新的通用图片生成后端。适合从文本直接生成通用资产、
  创意探索、概念图，以及不强依赖二次元风格或参考图一致性的高泛化任务。

默认经验规则：

- 从文本生成新的通用资产：优先 `openai_images`。
- 二次元特化资产或真 inpaint：优先 `novelai`。
- 图生图一致性、参考图差分、保角色 / 保构图：优先 `gemini_chat_image`。

## Endpoint 映射

### GPT Image

使用 `openai_images`。

文生图：

- Endpoint：`/v1/images/generations`
- 网关能力：`generate`
- 当前已测模型：`gpt-image-2`

参考图 / 图生图：

- Endpoint：`/v1/images/edits`
- 网关能力：`image_to_image`
- 当前中转状态：已接入，但是否可用取决于中转站是否启用 edits。

网关会按标准 multipart 方式提交 image edits 请求。如果某个中转站拒绝标准
multipart payload，应先确认该中转站要求的 edit payload 形态，再决定是否启用
GPT 图生图默认路由。

### Gemini / Nano Banana 风格图片

使用 `gemini_chat_image`。

文生图：

- Endpoint：`/v1/chat/completions`
- 网关能力：`generate`
- 当前已测模型：`gemini-3.1-flash-image`

参考图 / 图生图：

- Endpoint：`/v1/chat/completions`
- 网关能力：`image_to_image`
- 当前中转状态：可用。

参考图会以 OpenAI 风格 chat content part 的 data URL 形式发送。

Gemini adapter 会把请求宽高约分为结构化画幅参数。例如 `1024x1536` 序列化为：

```json
{
  "generationConfig": {
    "responseModalities": ["IMAGE"],
    "imageConfig": {
      "aspectRatio": "three-four",
      "imageSize": "2k"
    }
  }
}
```

该字段只发送给 `gemini_chat_image`。Gemini 的 Prompt content 不再追加 `Target size`；
通用 `openai_chat_image` 和 `grok_chat_image` 保持原有 Prompt 尺寸提示兼容行为。
当前 relay 模型别名只声明 `landscape / portrait / square / four-three / three-four` 和
`2k / 4k`。网关选择与请求宽高最接近的画幅别名；结构化画幅只约束比例，实际输出
像素仍需由调用方检查并按资产合同后处理。

### Grok Image

使用 `grok_chat_image`。

文生图：

- Endpoint：`/v1/chat/completions`
- 网关能力：`generate`
- 当前已测模型：`grok-imagine-image-lite`

参考图 / 图生图：

- Endpoint：`/v1/chat/completions`
- 网关能力：`image_to_image`
- 当前中转状态：不作为默认路由。

当前中转在参考图请求上曾返回服务端 SSE 错误。因此在中转行为确认前，Grok 更适合只用于文生图。

## Chat Payload 规则

chat image provider 会刻意保持保守 payload：

- 不把 Images API 的字符串 `response_format`，例如 `b64_json`，发送到
  `/v1/chat/completions`。
- 默认不发送 `n`。
- 只有调用方通过 `extra` 显式传入时才发送 `n`。
- 保留 `model`、`messages` 和安全透传字段，例如 `temperature`。
- `gemini_chat_image` 额外发送由请求宽高派生的
  `generationConfig.imageConfig.aspectRatio/imageSize`；这些字段不是通用
  passthrough，不会发给 OpenAI 或 Grok chat provider。
- 流式模式默认关闭；`settings.stream: true` 可以启用，请求级 `extra["stream"]`
  拥有最终优先级，可以显式启用或关闭。

这样可以避免把 Images API 字段错发到 chat-completions-only 图片模型上，导致中转拒绝请求或触发风控。

Gemini 请求成功时，实际发送的 provider 专属字段会记录在：

```text
generation_params.provider_image_parameters
```

记录只包含非敏感结构化参数，不包含密钥、Prompt、Base64 或 data URL。

## 响应解析

chat image 响应会解析以下常见中转格式：

- `data[].b64_json`
- `data[].url`
- 嵌套 JSON 字段
- SSE `data:` 事件
- `data:image/...;base64,...` URL
- Markdown 图片链接
- 裸 HTTP(S) 图片 URL

如果 SSE 响应里包含 provider 错误信息，网关会透出该错误，而不是只返回泛化的
`No image data found`。

## 透明流式传输

当最终 chat payload 包含 `stream: true` 时，网关通过
`httpx.AsyncClient.stream()` 打开请求，并使用 `httpx-sse` 增量解码 SSE。
这只改变 provider 内部传输方式：调用方仍等待 `ImageService.generate()` 或
`ImageService.image_to_image()`，并接收现有 `BatchResult` / `ImageResult` 模型。

传输规则：

- 增量解码 `text/event-stream`，支持多行 `data:` 和 `[DONE]`。
- 中转遗漏 `[DONE]` 时，只要已经收到合法事件，也接受正常 EOF。
- stream 请求收到普通 JSON 时，从同一连接读取，不重新发请求。
- SSE 注释心跳由 `httpx-sse` 消费并维持连接，但其公开 API 不提供注释心跳计数。
- 流式失败不会自动改发 buffered 请求，避免重复生成和重复计费。
- HTTP 错误正文会被截断；密钥、Prompt、Base64 和 data URL 不进入传输证据。

成功的流式请求会在 `generation_params` 中增加：

```text
stream_requested
stream_response_mode
stream_event_count
stream_first_event_elapsed_s
stream_completed_by_done
```

真实服务专项 smoke：

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

smoke 会拒绝 GIF 输入；调用方必须传入已拆出的静态帧。临时输出包含可解码图片和
`summary.json`，其中记录总耗时以及上述非敏感流式字段。

## 参考图输入

`ImageToImageRequest` 使用 bytes 保存 provider-facing 参考图。面向用户的 runner
可以通过下面工具统一解析常见输入：

- `resolve_image_input()`
- `resolve_image_inputs()`

支持输入：

- 原始 bytes
- 本地文件路径
- HTTP(S) 图片 URL
- `data:image/...;base64,...` URL

## 当前中转 smoke 结果

通过标准模型列表接口发现的模型：

- `gpt-image-2`
- `gemini-3.1-flash-image`
- `grok-imagine-image-lite`

已知可用路由：

- `openai_images.generate()` + `gpt-image-2`
- `gemini_chat_image.generate()` + `gemini-3.1-flash-image`
- `gemini_chat_image.image_to_image()` + `gemini-3.1-flash-image`
- `grok_chat_image.generate()` + `grok-imagine-image-lite`

已知不适合作为默认路由或需按中转站复测的路由：

- `openai_images.image_to_image()` + `/v1/images/edits`
- `grok_chat_image.image_to_image()` + 参考图

2026-07-18 透明流式验证：

- Gemini 文生图通过真实 SSE 流式成功，总耗时 49.859 秒；从 HTTP 请求发起前
  开始计时，首个业务事件在 0.312 秒到达，共收集 5 个事件并收到 `[DONE]`，
  107,560 字节 JPEG 可正常解码。
- Gemini 双参考图图生图的流式连接保持时间超过了此前的 524 窗口，但上游在
  292.906 秒后关闭了不完整的 chunked response，未返回可解码图片。该结果记录为
  `validation_limited:stream_request_failed_before_success_evidence`，不能据此声明
  图生图端到端已经稳定。

当前推荐默认配置：

```yaml
default_provider:
  generate: openai_images
  image_to_image: gemini_chat_image
  inpaint: novelai
  upscale: mock
```

如果调用方想使用 Grok 文生图，应显式传入 `provider="grok_chat_image"`，或使用单独的配置 profile。

## 验证

当前接入曾使用以下命令做过验证：

- `python -m pytest tests/test_sse_transport.py tests/test_openai_compatible_provider.py tests/test_streaming_smoke_cli.py -q`
- `python -m pytest tests/test_openai_compatible_provider.py tests/test_image_inputs.py -q`
- `python -m pytest tests -q`
- `python -m compileall -q ai_image_gateway examples/smoke_streaming_chat_image.py`
- 在 P3 根目录执行项目文档校验

中转 smoke 测试只使用环境或本地忽略配置提供的凭据。生成图片、API key 和中转密钥都不得提交进仓库。

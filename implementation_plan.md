# AI 图片处理聚合模块 (ai_image_service)

与 P3 项目和 ThreeState 完全解耦的独立 Python 包，对外提供统一的 AI 图片生成/修复能力，内部通过 Provider 适配器模式接入多种 AI 服务。

## User Review Required

> [!IMPORTANT]
> **项目存放位置**：建议放在 `f:\tools\ai_image_service\`，作为独立 Python 包，P3 的美术工具脚本通过 `pip install -e` 或 `sys.path` 引用。是否同意此位置？

> [!IMPORTANT]
> **首批 Provider**：计划先实现 NovelAI 和 Gemini (Nano Banana) 两个适配器。ComfyUI 本地适配器是否也需要在首批实现？

## Open Questions

1. **Gemini API Key**：你是否已有 Google AI Studio 的 API Key？Nano Banana 走的是 `google-genai` SDK。
2. **NovelAI 认证方式**：新模块是否仍通过账号密码登录（走 `novelai` 社区 SDK），还是切换为 Token/Cookie 方式？
3. **并发策略**：是否需要多账号轮转（NovelAI 多账号池）？还是 MVP 阶段先单账号串行？
4. **P3 批量脚本**：批量生成脚本（读 Manifest → 调 Service → 写 `_IncomingAI`）是否也包含在本次范围内？还是先只做 `ai_image_service` 包本身？

---

## 架构总览

```
f:\tools\ai_image_service\
├── pyproject.toml                 # 包定义, 依赖管理
├── config.example.yaml            # 配置模板
├── ai_image_service/
│   ├── __init__.py                # 对外暴露: ImageService, GenerateRequest, ImageResult
│   ├── schema.py                  # 统一数据模型 (Pydantic)
│   ├── service.py                 # ImageService 主入口 (Facade)
│   ├── router.py                  # Provider 选择/路由逻辑
│   ├── config.py                  # YAML 配置加载, 多账号管理
│   ├── errors.py                  # 统一异常体系
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py                # ABC: BaseImageProvider
│   │   ├── novelai.py             # NovelAI 适配器
│   │   └── gemini.py              # Gemini/NanoBanana 适配器
│   └── processing/
│       ├── __init__.py
│       ├── prompt_tools.py        # prompt 清洗/拼接工具
│       └── post_processor.py      # 后处理: resize/crop/去背景
└── tests/
    ├── test_schema.py
    ├── test_novelai_provider.py
    └── test_gemini_provider.py
```

---

## Proposed Changes

### Schema 层 — 统一数据模型

#### [NEW] [schema.py](file:///f:/tools/ai_image_service/ai_image_service/schema.py)

所有对外交互的数据结构，基于 Pydantic v2：

```python
class GenerateRequest(BaseModel):
    """生图请求"""
    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    count: int = 1                          # 单次生成张数
    seed: int | None = None                 # None = 随机
    provider: str | None = None             # 指定 provider, None = 自动路由
    style_preset: str | None = None         # provider 特定的风格预设
    extra: dict[str, Any] = {}              # provider 特有参数透传

class InpaintRequest(BaseModel):
    """修图/局部重绘请求"""
    image: bytes                            # 原图二进制
    mask: bytes                             # mask 二进制
    prompt: str
    negative_prompt: str = ""
    width: int | None = None
    height: int | None = None
    provider: str | None = None
    extra: dict[str, Any] = {}

class ImageResult(BaseModel):
    """单张生成结果"""
    image_bytes: bytes
    seed: int | None = None
    provider_name: str
    model_name: str
    generation_params: dict[str, Any] = {}  # 完整生成参数快照
    cost: float = 0.0                       # Anlas / Token 消耗

class BatchResult(BaseModel):
    """批量结果"""
    request_id: str                         # UUID
    results: list[ImageResult]
    errors: list[str] = []
    total_cost: float = 0.0
```

---

### Provider 层 — 适配器协议

#### [NEW] [base.py](file:///f:/tools/ai_image_service/ai_image_service/providers/base.py)

```python
class Capability(str, Enum):
    GENERATE = "generate"
    INPAINT = "inpaint"
    UPSCALE = "upscale"

class BaseImageProvider(ABC):
    name: str                               # "novelai" / "gemini"
    
    @abstractmethod
    async def initialize(self) -> None: ...
    
    @abstractmethod
    async def generate(self, request: GenerateRequest) -> list[ImageResult]: ...
    
    @abstractmethod
    async def inpaint(self, request: InpaintRequest) -> list[ImageResult]: ...
    
    @abstractmethod
    def supports(self, cap: Capability) -> bool: ...
    
    @abstractmethod
    async def close(self) -> None: ...
```

#### [NEW] [novelai.py](file:///f:/tools/ai_image_service/ai_image_service/providers/novelai.py)

- 封装 `novelai` 社区 SDK（`NAIClient`）或直接 `httpx` 调 NAI API
- 负责 NAI 特有的参数映射（`Resolution` 枚举、`Sampler`、`ucPreset`）
- 处理 `ConcurrentError` 重试和登录刷新
- 不包含任何 prompt 内容清洗逻辑（交给 `prompt_tools.py` 或上层）

#### [NEW] [gemini.py](file:///f:/tools/ai_image_service/ai_image_service/providers/gemini.py)

- 封装 `google-genai` SDK 的 `models.generate_images` 接口
- 支持 Imagen 4 和 Gemini Flash Image 两类模型
- 参数映射：`aspect_ratio`、`number_of_images` 等

---

### Service 层 — 对外 Facade

#### [NEW] [service.py](file:///f:/tools/ai_image_service/ai_image_service/service.py)

```python
class ImageService:
    """对外统一入口, 使用方只需与此类交互"""
    
    def __init__(self, config_path: str | Path | None = None):
        self._config = load_config(config_path)
        self._router = ProviderRouter(self._config)
    
    async def generate(self, request: GenerateRequest) -> BatchResult: ...
    async def inpaint(self, request: InpaintRequest) -> BatchResult: ...
    
    async def batch_generate(
        self, 
        requests: list[GenerateRequest],
        concurrency: int = 1,
        delay_seconds: float = 2.0,       # 请求间隔，防限流
        on_progress: Callable | None = None
    ) -> list[BatchResult]: ...
    
    async def close(self) -> None: ...
    async def __aenter__(self): ...
    async def __aexit__(self, *args): ...
```

#### [NEW] [router.py](file:///f:/tools/ai_image_service/ai_image_service/router.py)

Provider 选择策略：

| 场景 | 路由规则 |
|---|---|
| `request.provider` 已指定 | 直接分发到指定 provider |
| 未指定 + `generate` | 按配置文件的 `default_provider.generate` |
| 未指定 + `inpaint` | 按配置文件的 `default_provider.inpaint` |
| Provider 不支持该能力 | 抛 `ProviderCapabilityError` |

---

### Config 层

#### [NEW] [config.example.yaml](file:///f:/tools/ai_image_service/config.example.yaml)

```yaml
default_provider:
  generate: novelai
  inpaint: gemini

providers:
  novelai:
    enabled: true
    auth:
      username: ${NAI_USERNAME}          # 支持环境变量引用
      password: ${NAI_PASSWORD}
    settings:
      use_nai4: true
      sampler: k_euler
      default_uc_preset: 3
      sleep_range: [2, 5]                # 请求间隔范围(秒)
      max_retries: 5
      
  gemini:
    enabled: true
    auth:
      api_key: ${GEMINI_API_KEY}
    settings:
      model: imagen-4.0-generate-001
      safety_filter: block_none

logging:
  level: INFO
  file: ./logs/ai_image_service.log
```

---

### Processing 层

#### [NEW] [prompt_tools.py](file:///f:/tools/ai_image_service/ai_image_service/processing/prompt_tools.py)

通用 prompt 工具（不含 ThreeState 特有的 artist 过滤等业务逻辑）：

- `sanitize(prompt)` — 清理多余逗号/空格/空括号
- `merge(base_style, subject, composition, background)` — 结构化拼接
- `validate_length(prompt, max_tokens)` — token 长度校验

#### [NEW] [post_processor.py](file:///f:/tools/ai_image_service/ai_image_service/processing/post_processor.py)

基于 Pillow 的后处理：

- `resize(image, width, height)`
- `crop_to_aspect(image, ratio)`
- `trim_transparent(image, padding_percent)`
- `remove_background(image)` — 可接入 `rembg`

---

### 依赖管理

#### [NEW] [pyproject.toml](file:///f:/tools/ai_image_service/pyproject.toml)

```toml
[project]
name = "ai-image-service"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "Pillow>=10.0",
    "loguru>=0.7",
]

[project.optional-dependencies]
novelai = ["novelai>=0.2"]
gemini = ["google-genai>=1.0"]
rembg = ["rembg>=2.0"]
dev = ["pytest", "pytest-asyncio"]
```

---

## 调用示例

### 最简用法

```python
from ai_image_service import ImageService, GenerateRequest

async with ImageService("config.yaml") as svc:
    result = await svc.generate(GenerateRequest(
        prompt="game item icon, rusty dagger, steampunk, transparent background",
        negative_prompt="text, watermark",
        width=512, height=512, count=4
    ))
    for img in result.results:
        Path(f"output/{img.seed}.png").write_bytes(img.image_bytes)
```

### P3 批量脚本对接（后续）

```python
# tools/美术工具/Batch-GenerateArt.py
from ai_image_service import ImageService, GenerateRequest
import json

manifest = json.load(open("美术文档/_generated/art_manifest.json"))
async with ImageService("config.yaml") as svc:
    for entry in manifest["Entries"]:
        if entry["Status"] != "prompted":
            continue
        result = await svc.generate(GenerateRequest(
            prompt=entry["PromptEN"],
            negative_prompt=entry["NegativePromptEN"],
            width=entry["Spec"]["Width"],
            height=entry["Spec"]["Height"],
            count=4,
        ))
        # 写入 _IncomingAI/<VisualID>/raw/ ...
```

---

## 实施顺序

| 阶段 | 内容 | 产出 |
|---|---|---|
| 1 | `schema.py` + `base.py` + `config.py` + `errors.py` | 数据模型与协议 |
| 2 | `novelai.py` provider | NAI 生图能力 |
| 3 | `gemini.py` provider | Gemini 生图能力 |
| 4 | `service.py` + `router.py` | 统一入口与路由 |
| 5 | `prompt_tools.py` + `post_processor.py` | 预/后处理 |
| 6 | 集成测试 + CLI smoke test | 端到端验证 |

---

## Verification Plan

### Automated Tests

```bash
cd f:\tools\ai_image_service
pytest tests/ -v
```

- `test_schema.py`：数据模型序列化/反序列化
- `test_novelai_provider.py`：Mock SDK，验证参数映射和重试逻辑
- `test_gemini_provider.py`：Mock SDK，验证参数映射

### Manual Verification

- 使用真实 API Key 对每个 provider 执行单张生图 smoke test
- 验证输出 PNG 可正常打开且符合预期尺寸

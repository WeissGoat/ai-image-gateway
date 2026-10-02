# ai-image-gateway Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `ai-image-gateway` 重构为清晰分层的 provider gateway / transport toolkit，并在不破坏 PromptAtelier 现有 NovelAI raw 接入的前提下，完成 contracts、providers、facade、workflow 的职责收敛。

**Architecture:** 本计划采用“先立新结构、再迁引用、最后删旧入口”的迁移方式。核心原则是把 raw client 与 facade 分开，把 provider core 与 workflow 分开，同时保持 `ImageService` 对外用法稳定、PromptAtelier 只依赖 raw client。

**Tech Stack:** Python 3.10+, Pydantic v2, httpx, Pillow, numpy, pytest, pytest-asyncio

## Global Constraints

- `ai-image-gateway` 是 provider gateway / transport toolkit，不是提示词业务系统。
- PromptAtelier 主链路只认 raw client，不认 provider facade。
- provider core 不能 import workflow。
- transport 不能 import provider facade。
- contracts 不能 import service / router / provider 实现。
- raw client 不改写调用方准备好的 payload。
- `openai_compatible` 当前可以 facade-first。
- 真实出图验收优先于目录重构完成度。

---

## File Map

### 现有文件

- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/schema.py`
  - 当前通用 contract 与 raw contract 混合定义
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/service.py`
  - 当前单次入口与批量入口混合
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/__init__.py`
  - 当前对外导出入口
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai.py`
  - 当前 NovelAI 全家桶实现
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible.py`
  - 当前 OpenAI-compatible provider 实现
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/mock.py`
  - 当前 mock provider
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/auth/novelai_token.py`
  - NovelAI token 解析
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/workflows/p3_live2d_inpaint.py`
  - 现有 workflow
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_schema.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_novelai_provider.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_openai_compatible_provider.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_mock_provider.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_p3_live2d_inpaint_workflow.py`

### 本轮新增目标文件

- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/__init__.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/common.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/generate.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/raw.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/facade/__init__.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/facade/image_service.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/facade/batch_service.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/__init__.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/raw_client.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/facade.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/decode.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/payloads.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible/__init__.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible/facade.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible/decode.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/mock/__init__.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/mock/provider.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_contracts.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_image_service.py`
- `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_batch_service.py`

---

### Task 1: 建立 contracts 层并保持旧导出兼容

**Files:**
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/__init__.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/common.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/generate.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/contracts/raw.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/schema.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/__init__.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_contracts.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_schema.py`

**Interfaces:**
- Consumes: existing `Capability`, `GenerateRequest`, `ImageResult`, `NovelAIRawPayload` names from `ai_image_gateway.schema`
- Produces:
  - `ai_image_gateway.contracts.common.Capability`
  - `ai_image_gateway.contracts.generate.GenerateRequest`
  - `ai_image_gateway.contracts.raw.NovelAIRawPayload`
  - `ai_image_gateway.schema` compatibility re-exports

- [ ] **Step 1: 写 contracts 拆分的失败测试**

```python
from ai_image_gateway.contracts.common import Capability, BatchResult
from ai_image_gateway.contracts.generate import GenerateRequest, InpaintRequest
from ai_image_gateway.contracts.raw import NovelAIRawPayload, RetryRecord


def test_contract_modules_expose_expected_types():
    request = GenerateRequest(prompt="test")
    payload = NovelAIRawPayload(input="p", model="m", parameters={})
    record = RetryRecord(attempt=1, retryable=False)
    batch = BatchResult()

    assert request.prompt == "test"
    assert payload.model == "m"
    assert record.attempt == 1
    assert batch.success_count == 0
    assert Capability.GENERATE.value == "generate"


def test_schema_keeps_backward_compatible_exports():
    from ai_image_gateway.schema import GenerateRequest as SchemaGenerateRequest
    from ai_image_gateway.schema import NovelAIRawPayload as SchemaNovelAIRawPayload

    assert SchemaGenerateRequest(prompt="ok").prompt == "ok"
    assert SchemaNovelAIRawPayload(input="p", model="m", parameters={}).model == "m"
```

- [ ] **Step 2: 运行失败测试确认新模块尚不存在**

Run:

```bash
uv run python -m pytest tests/test_contracts.py -v
```

Expected:

```text
FAIL ... ModuleNotFoundError: No module named 'ai_image_gateway.contracts'
```

- [ ] **Step 3: 实现 contracts 模块与 schema 兼容导出**

```python
# ai_image_gateway/contracts/common.py
from enum import Enum
from pydantic import BaseModel, Field
import uuid


class Capability(str, Enum):
    GENERATE = "generate"
    IMAGE_TO_IMAGE = "image_to_image"
    INPAINT = "inpaint"
    UPSCALE = "upscale"


class ImageFormat(str, Enum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class ImageResult(BaseModel):
    image_bytes: bytes
    seed: int | None = None
    provider_name: str
    model_name: str = ""
    generation_params: dict = Field(default_factory=dict)
    cost: float = 0.0


class BatchResult(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    results: list[ImageResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
```

```python
# ai_image_gateway/contracts/generate.py
from pydantic import BaseModel, Field
from .common import ImageFormat


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    count: int = Field(default=1, ge=1, le=16)
    seed: int | None = None
    provider: str | None = None
    output_format: ImageFormat = ImageFormat.PNG
    extra: dict = Field(default_factory=dict)
```

```python
# ai_image_gateway/schema.py
from .contracts.common import *
from .contracts.generate import *
from .contracts.raw import *
```

- [ ] **Step 4: 运行 contracts 与 schema 测试确认通过**

Run:

```bash
uv run python -m pytest tests/test_contracts.py tests/test_schema.py -v
```

Expected:

```text
PASS tests/test_contracts.py
PASS tests/test_schema.py
```

- [ ] **Step 5: 提交**

```bash
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway add \
  ai_image_gateway/contracts \
  ai_image_gateway/schema.py \
  ai_image_gateway/__init__.py \
  tests/test_contracts.py
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway commit -m "refactor: split contract modules"
```

### Task 2: 拆出 NovelAI raw client / decode / payloads 并保持现有行为

**Files:**
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/__init__.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/raw_client.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/decode.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/payloads.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai/facade.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/novelai.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/__init__.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_novelai_provider.py`

**Interfaces:**
- Consumes:
  - `ai_image_gateway.contracts.raw.NovelAIRawPayload`
  - `ai_image_gateway.auth.novelai_token.resolve_novelai_access_token`
- Produces:
  - `ai_image_gateway.providers.novelai.raw_client.NovelAIRawClient.generate_raw(payload: NovelAIRawPayload) -> NovelAIRawResult`
  - `ai_image_gateway.providers.novelai.facade.NovelAIFacadeProvider.generate(request: GenerateRequest) -> list[ImageResult]`
  - compatibility alias `ai_image_gateway.providers.novelai.NovelAIProvider`

- [ ] **Step 1: 为 raw client 和 facade 拆分写失败测试**

```python
from ai_image_gateway.providers.novelai.raw_client import NovelAIRawClient
from ai_image_gateway.providers.novelai.facade import NovelAIFacadeProvider


def test_novelai_split_modules_exist():
    assert NovelAIRawClient is not None
    assert NovelAIFacadeProvider is not None
```

```python
@pytest.mark.asyncio
async def test_raw_client_sends_exact_payload():
    provider = _make_provider()
    await provider.initialize()
    payload = NovelAIRawPayload(input="raw", model="nai-diffusion-4-5-full", parameters={"seed": 1})
    ...
    assert sent_payload == payload.model_dump()
```

- [ ] **Step 2: 运行 NovelAI 拆分测试确认失败**

Run:

```bash
uv run python -m pytest tests/test_novelai_provider.py::test_novelai_split_modules_exist -v
```

Expected:

```text
FAIL ... ModuleNotFoundError: No module named 'ai_image_gateway.providers.novelai.raw_client'
```

- [ ] **Step 3: 新建 NovelAI 子目录并迁移 raw/decode/payload 逻辑**

```python
# ai_image_gateway/providers/novelai/raw_client.py
class NovelAIRawClient:
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._provider = _NovelAITransportProvider(config)

    async def initialize(self) -> None:
        await self._provider.initialize()

    async def close(self) -> None:
        await self._provider.close()

    async def generate_raw(self, payload: NovelAIRawPayload) -> NovelAIRawResult:
        return await self._provider.generate_raw(payload)
```

```python
# ai_image_gateway/providers/novelai/facade.py
class NovelAIFacadeProvider(BaseImageProvider):
    name = "novelai"

    async def generate(self, request: GenerateRequest) -> list[ImageResult]:
        params = build_generate_parameters(request=request, defaults=self._defaults)
        payload = NovelAIRawPayload(
            input=request.prompt,
            model=params.model,
            action=params.action,
            parameters=params.parameters,
        )
        raw = await self._raw_client.generate_raw(payload)
        return decode_generate_results(raw, provider_name=self.name, request=request)
```

```python
# ai_image_gateway/providers/novelai.py
from .novelai.facade import NovelAIFacadeProvider as NovelAIProvider
from .novelai.raw_client import NovelAIRawClient
```

- [ ] **Step 4: 跑 NovelAI provider 测试确认 raw 与 facade 行为不回归**

Run:

```bash
uv run python -m pytest tests/test_novelai_provider.py -v
```

Expected:

```text
PASS tests/test_novelai_provider.py
```

- [ ] **Step 5: 提交**

```bash
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway add \
  ai_image_gateway/providers/novelai \
  ai_image_gateway/providers/novelai.py \
  ai_image_gateway/providers/__init__.py \
  tests/test_novelai_provider.py
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway commit -m "refactor: split novelai raw and facade providers"
```

### Task 3: 拆出 facade/image_service 与 facade/batch_service 并保持旧入口兼容

**Files:**
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/facade/__init__.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/facade/image_service.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/facade/batch_service.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/service.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/__init__.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_image_service.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_batch_service.py`

**Interfaces:**
- Consumes:
  - `ProviderRouter.get_provider(requested_name: str | None, capability: Capability)`
  - provider `generate` / `inpaint` / `image_to_image`
- Produces:
  - `ai_image_gateway.facade.image_service.ImageService`
  - `ai_image_gateway.facade.batch_service.BatchService`
  - compatibility alias `ai_image_gateway.service.ImageService`

- [ ] **Step 1: 为 facade 拆分写失败测试**

```python
from ai_image_gateway.facade.image_service import ImageService
from ai_image_gateway.facade.batch_service import BatchService


def test_facade_modules_export_services():
    assert ImageService is not None
    assert BatchService is not None
```

- [ ] **Step 2: 运行 facade 拆分测试确认失败**

Run:

```bash
uv run python -m pytest tests/test_image_service.py -v
```

Expected:

```text
FAIL ... ModuleNotFoundError: No module named 'ai_image_gateway.facade'
```

- [ ] **Step 3: 拆 service.py 为 image_service.py 和 batch_service.py**

```python
# ai_image_gateway/facade/image_service.py
class ImageService:
    def __init__(self, config: str | Path | GatewayConfig | None = None) -> None:
        self._config = load_config(config) if not isinstance(config, GatewayConfig) else config
        self._router = ProviderRouter(self._config)

    async def generate(self, request: GenerateRequest) -> BatchResult:
        ...

    async def inpaint(self, request: InpaintRequest) -> BatchResult:
        ...
```

```python
# ai_image_gateway/facade/batch_service.py
class BatchService:
    def __init__(self, image_service: ImageService) -> None:
        self._image_service = image_service

    async def batch_generate(self, requests: list[GenerateRequest], *, concurrency: int = 1, delay_seconds: float = 2.0):
        ...
```

```python
# ai_image_gateway/service.py
from .facade.image_service import ImageService
from .facade.batch_service import BatchService
```

- [ ] **Step 4: 运行 facade 与既有服务测试**

Run:

```bash
uv run python -m pytest tests/test_image_service.py tests/test_batch_service.py tests/test_mock_provider.py -v
```

Expected:

```text
PASS tests/test_image_service.py
PASS tests/test_batch_service.py
PASS tests/test_mock_provider.py
```

- [ ] **Step 5: 提交**

```bash
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway add \
  ai_image_gateway/facade \
  ai_image_gateway/service.py \
  ai_image_gateway/__init__.py \
  tests/test_image_service.py \
  tests/test_batch_service.py
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway commit -m "refactor: split image and batch facade services"
```

### Task 4: 收敛 openai_compatible、mock 与 workflow 依赖方向

**Files:**
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible/__init__.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible/facade.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible/decode.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/mock/__init__.py`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/mock/provider.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/openai_compatible.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/mock.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/workflows/p3_live2d_inpaint.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_openai_compatible_provider.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_mock_provider.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_p3_live2d_inpaint_workflow.py`

**Interfaces:**
- Consumes:
  - `ImageService`
  - `BatchService`
  - existing provider classes in `openai_compatible.py` and `mock.py`
- Produces:
  - compatibility aliases for old flat modules
  - workflow imports that only point at facade layer

- [ ] **Step 1: 为目录迁移和依赖方向写失败测试**

```python
from ai_image_gateway.providers.mock.provider import MockProvider
from ai_image_gateway.providers.openai_compatible.facade import OpenAIImagesProvider


def test_provider_subpackages_export_existing_classes():
    assert MockProvider is not None
    assert OpenAIImagesProvider is not None
```

```python
def test_workflow_only_uses_facade_imports():
    source = Path("ai_image_gateway/workflows/p3_live2d_inpaint.py").read_text(encoding="utf-8")
    assert "from ai_image_gateway.providers." not in source
```

- [ ] **Step 2: 运行迁移测试确认失败**

Run:

```bash
uv run python -m pytest tests/test_openai_compatible_provider.py::test_provider_subpackages_export_existing_classes -v
```

Expected:

```text
FAIL ... ModuleNotFoundError
```

- [ ] **Step 3: 新建 openai_compatible/mock 子目录并把 workflow 调整到 facade 依赖**

```python
# ai_image_gateway/providers/mock/provider.py
from ..base import BaseImageProvider


class MockProvider(BaseImageProvider):
    ...
```

```python
# ai_image_gateway/providers/mock.py
from .mock.provider import MockProvider
```

```python
# ai_image_gateway/workflows/p3_live2d_inpaint.py
from ai_image_gateway.facade.image_service import ImageService
```

- [ ] **Step 4: 运行 provider 与 workflow 相关测试**

Run:

```bash
uv run python -m pytest \
  tests/test_openai_compatible_provider.py \
  tests/test_mock_provider.py \
  tests/test_p3_live2d_inpaint_workflow.py -v
```

Expected:

```text
PASS tests/test_openai_compatible_provider.py
PASS tests/test_mock_provider.py
PASS tests/test_p3_live2d_inpaint_workflow.py
```

- [ ] **Step 5: 提交**

```bash
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway add \
  ai_image_gateway/providers/openai_compatible \
  ai_image_gateway/providers/openai_compatible.py \
  ai_image_gateway/providers/mock \
  ai_image_gateway/providers/mock.py \
  ai_image_gateway/workflows/p3_live2d_inpaint.py \
  tests/test_openai_compatible_provider.py \
  tests/test_mock_provider.py \
  tests/test_p3_live2d_inpaint_workflow.py
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway commit -m "refactor: align provider packages and workflow dependencies"
```

### Task 5: 收尾清理、README/导出入口更新、全量验证

**Files:**
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/__init__.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/ai_image_gateway/providers/__init__.py`
- Modify: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/docs/2026-07-05-gateway-refactor-boundary.md`
- Create: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/README.md`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_config.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_image_inputs.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_prompt_tools.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests/test_post_processor.py`
- Test: `F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway/tests`

**Interfaces:**
- Consumes:
  - newly created `contracts`, `providers.novelai`, `facade`
- Produces:
  - stable public imports from `ai_image_gateway`
  - README examples for facade path and raw path
  - updated docs that point to the architecture spec and this plan

- [ ] **Step 1: 写 README 与公共导出回归测试**

```python
def test_package_root_exports_public_api():
    from ai_image_gateway import ImageService, GenerateRequest, NovelAIRawPayload

    assert ImageService is not None
    assert GenerateRequest(prompt="ok").prompt == "ok"
    assert NovelAIRawPayload(input="p", model="m", parameters={}).model == "m"
```

```python
def test_readme_mentions_facade_and_raw_paths():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "ImageService" in text
    assert "NovelAIRawClient" in text
```

- [ ] **Step 2: 运行公共导出测试确认失败**

Run:

```bash
uv run python -m pytest tests/test_contracts.py::test_schema_keeps_backward_compatible_exports -v
```

Expected:

```text
FAIL if exports or README are not yet aligned
```

- [ ] **Step 3: 更新 README、公共导出和边界文档索引**

```markdown
# ai-image-gateway

## Facade Path

```python
from ai_image_gateway import ImageService, GenerateRequest
```

## Raw Path

```python
from ai_image_gateway.providers.novelai.raw_client import NovelAIRawClient
from ai_image_gateway.contracts.raw import NovelAIRawPayload
```
```

```python
# ai_image_gateway/__init__.py
from .facade.image_service import ImageService
from .contracts.generate import GenerateRequest, InpaintRequest, ImageToImageRequest
from .contracts.raw import NovelAIRawPayload, NovelAIRawResult, RetryRecord
```

- [ ] **Step 4: 运行全量测试与一次真实业务验证**

Run:

```bash
uv run python -m pytest
```

Expected:

```text
all tests passed
```

Run:

```bash
uv run python -m pytest tests/test_novelai_provider.py -v
```

Expected:

```text
PASS tests/test_novelai_provider.py
```

Business verification:

```bash
uv run python -m pytest tests/test_p3_live2d_inpaint_workflow.py -v
```

Expected:

```text
PASS tests/test_p3_live2d_inpaint_workflow.py
```

- [ ] **Step 5: 提交**

```bash
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway add \
  ai_image_gateway/__init__.py \
  ai_image_gateway/providers/__init__.py \
  README.md \
  docs/2026-07-05-gateway-refactor-boundary.md
git -C F:/my_project/new/tags_machine/refactor/vendor/ai-image-gateway commit -m "docs: finalize gateway refactor public surface"
```

## Self-Review

### 1. Spec coverage

- `contracts`：Task 1
- `providers/novelai` raw + facade split：Task 2
- `facade` split：Task 3
- `openai_compatible` / `mock` / `workflow` dependency cleanup：Task 4
- README、公共导出、边界文档收尾：Task 5

无未覆盖项。

### 2. Placeholder scan

- 未使用 `TODO`、`TBD`、`later`
- 每个任务均给出文件、接口、测试命令、提交命令

### 3. Type consistency

- raw contract 统一使用 `NovelAIRawPayload` / `NovelAIRawResult`
- facade 统一使用 `GenerateRequest` / `ImageService`
- NovelAI raw 入口统一命名为 `NovelAIRawClient.generate_raw`

无命名冲突。

---

Plan complete and saved to `docs/2026-07-06-ai-image-gateway-architecture-refactor-implementation-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**

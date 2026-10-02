# ai-image-gateway 架构重构开发文档

日期：2026-07-06

分支：`codex/refactor-raw-transport-boundary`

关联文档：

- `docs/2026-07-05-gateway-refactor-boundary.md`

本文档定位：

- 上一版 `gateway-refactor-boundary` 主要用于确认 PromptAtelier 与 `ai-image-gateway` 的职责边界。
- 本文档在此基础上，进一步落实 `ai-image-gateway` 自身的目标架构、文件迁移方式、阶段计划与开发约束。
- 如果两份文档出现粒度差异，以本文档为 `ai-image-gateway` 后续开发的直接执行依据，以 `2026-07-05` 文档为边界背景说明。

---

## 1. 文档目标与边界

本文档同时覆盖三件事：

1. `ai-image-gateway` 的目标架构。
2. `ai-image-gateway` 当前实现中的职责混杂点。
3. 从当前实现迁移到目标结构的具体阶段计划。

本文档会设计并规划：

- `contracts`
- `transports`
- `auth`
- `providers`
- `facade`
- `workflows/examples`
- 与 PromptAtelier 的接入边界

本文档不展开：

- PromptAtelier 的业务节点规则
- PromptAtelier renderer 的内部实现
- 旧 `tags_machine` 的业务兼容细节

一句话概括：

`ai-image-gateway` 要被收敛成一个稳定的“生图 provider 网关内核 + 轻量 facade + workflow 外围层”，而不是新的提示词业务系统。

---

## 2. 背景与现状

PromptAtelier 当前已经有稳定的业务主链路：

```text
节点读取 -> Composer -> PromptPolicyPipeline -> Renderer -> RenderRequest -> Executor -> GenerationResult
```

其中与画什么、如何表达给模型有关的逻辑，已经主要沉淀在 PromptAtelier 一侧，包括：

- artist 节点和画风拼接
- 角色、动作、背景节点组合
- NovelAI `characterPrompts`
- reference / vibe
- PromptPolicyPipeline
- batch 编排
- PNG 参数归档与验收对比

与此同时，`ai-image-gateway` 已经具备以下能力雏形：

- provider 路由
- NovelAI provider
- OpenAI-compatible provider
- mock provider
- inpaint / image-to-image
- workflow 脚本

当前问题不在于“有没有能力”，而在于职责层次还不够清晰。

---

## 3. 当前主要问题

### 3.1 `providers/novelai.py` 过重

当前 `providers/novelai.py` 同时承担：

- auth 初始化
- HTTP client 生命周期
- retry / timeout
- payload 组织
- facade generate
- raw generate
- inpaint
- 响应 zip 解码
- 模型参数默认值和兼容逻辑

这会导致该文件同时像：

- transport
- provider facade
- provider raw client
- payload builder
- decode utility

继续堆叠下去，后续接入 PromptAtelier、OpenAI-compatible 或新增 provider 时，都会让改动越来越耦合。

### 3.2 `schema.py` 混合了多层契约

当前 `schema.py` 同时放了：

- 通用 facade request / result
- provider raw payload / raw result
- retry evidence

短期可用，但长期会让 facade 和 raw 两条链路互相污染。

### 3.3 `service.py` 同时承担 facade 和 batch 调度

`service.py` 既像：

- 单次调用入口
- 批量执行入口
- Provider facade 汇总入口

这使它容易变成新的“大一统中心文件”。

### 3.4 workflow 与 provider core 的边界还不够明确

当前 `workflows/p3_live2d_inpaint.py` 是合理的场景脚本，但缺少明确架构约束时，后续很容易出现：

- 某个项目的路径规则进入 provider core
- 某个 workflow 字段约定进入通用契约
- 为了某个 workflow 的方便，把特殊逻辑塞回 provider 文件

### 3.5 raw path 与 facade path 仍处于过渡态

现在已经补出了 NovelAI raw transport 能力，但整体结构上仍然带有明显过渡态特征：

- raw contract 仍和 facade contract 混在一起
- raw generate 与 facade generate 还在同一个 provider 大文件里
- PromptAtelier 侧仍需要 bridge 才能更清晰地接入

这些都说明：边界方向已经正确，但目录和职责还没有完全收敛。

---

## 4. 目标定位

`ai-image-gateway` 的目标定位是：

```text
AI image provider gateway / transport toolkit
```

它负责：

- provider auth
- HTTP transport
- timeout / retry / rate limit
- provider 原始请求发送
- provider 响应解码
- 为轻量调用方提供 facade
- 为复杂调用方提供 raw client

它不负责：

- 解释 PromptAtelier 的业务节点
- 拼接角色、动作、画风 prompt
- 决定是否启用 `characterPrompts`
- 根据 artist 节点修改模型参数
- 执行 PromptPolicyPipeline
- 生成 `PromptBundle`
- 生成 PromptAtelier 的 `GenerationResult`

一句话：

- PromptAtelier 决定“画什么”
- `ai-image-gateway` 决定“怎么把请求发到 provider，并拿回结果”

---

## 5. 目标目录结构

建议的目标结构如下：

```text
ai_image_gateway/
  contracts/
    common.py
    generate.py
    raw.py

  transports/
    http_client.py
    retry.py
    errors.py

  auth/
    novelai.py

  providers/
    base.py

    novelai/
      raw_client.py
      facade.py
      decode.py
      payloads.py

    openai_compatible/
      raw_client.py
      facade.py
      decode.py

    mock/
      provider.py

  facade/
    image_service.py
    batch_service.py

  workflows/
    p3_live2d_inpaint.py

  router.py
  config.py
  __init__.py
```

说明：

- `router.py`、`config.py` 仍可先保留为顶层文件。
- 第一轮迁移不要求一次把所有旧文件删除，可以先新建目录，再逐步迁引用。
- `openai_compatible` 可以先 facade-first，raw 结构先预留，不要求第一阶段全部完成。

---

## 6. 分层职责

### 6.1 `contracts`

职责：

- 定义稳定的数据契约
- 提供 facade request / result
- 提供 raw request / result
- 提供 retry evidence

建议拆分：

- `common.py`
  - `Capability`
  - `ImageFormat`
  - `ImageResult`
  - `BatchResult`
- `generate.py`
  - `GenerateRequest`
  - `ImageToImageRequest`
  - `InpaintRequest`
- `raw.py`
  - `NovelAIRawPayload`
  - `NovelAIRawResult`
  - `RetryRecord`
  - 后续其他 provider 的 raw 契约

要求：

- `contracts` 不反向 import provider 实现
- 上层调用方可以只依赖 `contracts` 而不接触内部目录

### 6.2 `transports`

职责：

- HTTP client 管理
- timeout
- retry
- rate limit
- 基础异常映射

要求：

- 不理解 prompt
- 不理解角色、动作、artist
- 不理解 provider facade 业务

### 6.3 `auth`

职责：

- provider 认证材料解析
- token / access key / fallback 解析

当前重点：

- NovelAI token 解析
- 环境变量
- 本地 `client.py` fallback

### 6.4 `providers`

职责：

- 适配 provider 协议
- 提供 raw client
- 提供 facade adapter
- provider 响应 decode

每个 provider 尽量拆成以下角色：

- `raw_client.py`
  - 面向已经准备好原始 payload 的调用方
  - 不改 payload
- `facade.py`
  - 面向 `GenerateRequest` 这类通用请求
  - 内部构造 provider payload
- `decode.py`
  - 响应格式解码
- `payloads.py`
  - provider facade 使用的 payload builder

### 6.5 `facade`

职责：

- 提供对外统一、轻量、友好的调用入口

建议拆分：

- `image_service.py`
  - `generate`
  - `image_to_image`
  - `inpaint`
- `batch_service.py`
  - `batch_generate`
  - `batch_image_to_image`
  - `batch_inpaint`

要求：

- facade 是壳，不是新的业务中心
- facade 不应承担 provider-specific 大量细节

### 6.6 `workflows`

职责：

- 项目化工作流脚本
- 场景工具
- 示例流程

要求：

- workflow 可以依赖 facade
- provider core 不能反向依赖 workflow

---

## 7. 目标数据流

### 7.1 轻量 facade 调用链

适合简单脚本、workflow、独立小工具：

```text
caller
  -> facade.ImageService
  -> router
  -> provider facade
  -> provider raw client
  -> transport
  -> provider result
  -> facade result
```

### 7.2 上层业务系统 raw 调用链

适合 PromptAtelier 这类已准备好 provider payload 的系统：

```text
PromptAtelier renderer
  -> provider raw payload
  -> gateway raw client
  -> transport
  -> raw result
  -> PromptAtelier archive/result
```

要求：

- PromptAtelier 主链路优先走 raw client
- facade path 不用于严格 payload 一致性验证场景

---

## 8. 当前文件到目标结构的映射

### 8.1 `ai_image_gateway/schema.py`

现状：

- 混合通用 request/result 与 NovelAI raw contract

迁移目标：

```text
contracts/common.py
contracts/generate.py
contracts/raw.py
```

迁移原则：

- facade 契约与 raw 契约分开
- provider-specific raw contract 先统一放 `raw.py`
- provider 增多后，再按 provider 拆 finer-grained contracts

### 8.2 `ai_image_gateway/service.py`

现状：

- 同时承担 facade 和 batch 调度

迁移目标：

```text
facade/image_service.py
facade/batch_service.py
```

迁移原则：

- 单次调用和批量调度分离
- 对外行为保持稳定

### 8.3 `ai_image_gateway/router.py`

现状：

- 路由职责整体合理

迁移目标：

- 暂时保留单文件
- 负责 capability -> provider name
- 负责 provider name -> provider factory
- 负责 provider 生命周期缓存

迁移原则：

- `router.py` 不是本轮优先重构点
- 优先保证 provider 目录结构先清晰

### 8.4 `ai_image_gateway/providers/novelai.py`

现状：

- 最大的职责混杂点

迁移目标：

```text
providers/novelai/raw_client.py
providers/novelai/facade.py
providers/novelai/decode.py
providers/novelai/payloads.py
```

职责划分：

- `raw_client.py`
  - `generate_raw`
  - 后续可扩展 `inpaint_raw`
  - 不改 payload
- `facade.py`
  - `generate`
  - `image_to_image`
  - `inpaint`
  - 把通用 request 转为 NovelAI payload
- `decode.py`
  - zip 解包
  - multipart 响应解码
- `payloads.py`
  - `GenerateRequest -> NovelAI payload`
  - `InpaintRequest -> NovelAI payload`

关键规则：

- PromptAtelier 主链路只依赖 `raw_client.py`
- `facade.py` 是轻量入口，不是 PromptAtelier 的主调用层

### 8.5 `ai_image_gateway/providers/openai_compatible.py`

现状：

- 已经承担多种 provider 兼容逻辑

迁移目标：

```text
providers/openai_compatible/raw_client.py
providers/openai_compatible/facade.py
providers/openai_compatible/decode.py
```

迁移原则：

- 当前优先做 facade-first
- raw 结构先预留扩展点
- 不强求第一阶段与 NovelAI 完全对称

### 8.6 `ai_image_gateway/providers/mock.py`

现状：

- 作为 mock provider 基本合理

迁移目标：

```text
providers/mock/provider.py
```

迁移原则：

- mock 只模拟 provider 外形
- 不额外承载业务规则

### 8.7 `ai_image_gateway/auth/novelai_token.py`

现状：

- 方向正确，职责可继续保留

迁移目标：

- 统一为 `auth/novelai.py` 或保留现名，但职责写死

职责要求：

- 只负责 token / access key / fallback 解析
- 不参与 payload 组织
- 不参与 retry / decode

### 8.8 `ai_image_gateway/workflows/p3_live2d_inpaint.py`

现状：

- 合理的场景 workflow

迁移目标：

- 保留在 `workflows/`
- 明确其为外围层

规则：

- workflow 可以依赖 facade
- provider core 不能依赖 workflow

### 8.9 `ai_image_gateway/processing/*`

现状：

- 更像通用处理工具箱

迁移目标：

- provider-specific decode 下沉到 provider `decode.py`
- 通用后处理保留 `processing/`
- workflow 独享逻辑迁到 `workflows/`

迁移原则：

- 第一轮先做归属清理规则
- 第二轮再按必要性真正拆分

---

## 9. 迁移阶段计划

整个重构按四个阶段推进。

### Phase 1：站稳 NovelAI raw 内核

目标：

- 明确 NovelAI raw transport 的结构和语义
- 让 PromptAtelier 可以稳定依赖 raw path
- 不破坏现有 facade 使用方式

主要改动：

1. 保留现有可运行逻辑。
2. 将 raw transport 概念从 `providers/novelai.py` 中抽清。
3. 固化 raw contract：
   - `NovelAIRawPayload`
   - `NovelAIRawResult`
   - `RetryRecord`
4. 文档写死 raw path 约束：
   - 不改 payload
   - 只做 auth / HTTP / retry / decode
5. PromptAtelier 侧继续通过 bridge 接入，保持运行稳定。

涉及文件：

- `schema.py` 或拆分后的 `contracts/raw.py`
- `providers/novelai.py` 或新 `providers/novelai/raw_client.py`

验收：

- 同一份 NovelAI payload，发出的 JSON 与输入完全一致。
- retry records 可记录 429 / 5xx。
- PromptAtelier 经 gateway raw path 的真实出图通过业务验收。
- 现有 facade 行为不回归。

### Phase 2：拆 contracts / facade / provider

目标：

- 完成第一次结构收敛
- 让 schema、service、novelai provider 各自回到正确层次

主要改动：

1. 拆 `contracts/`
2. 拆 `facade/`
3. 拆 `providers/novelai/`

迁移策略：

- 先新建目录和新文件
- 旧文件保留薄转发或兼容导出
- 再迁内部引用
- 最后删除旧聚合文件

验收：

- 对外 import 变化最小或可控。
- `ImageService.generate()` 行为保持一致。
- PromptAtelier raw 接入行为保持一致。
- NovelAI raw 与 facade 两条链路均可运行。

### Phase 3：收敛 OpenAI-compatible / mock / processing / workflows

目标：

- 把外围能力迁移到正确位置
- 修正依赖方向

主要改动：

1. `openai_compatible.py` 拆为 provider 子目录。
2. `mock.py` 迁到 `providers/mock/provider.py`。
3. `processing/` 按 provider-specific / common / workflow-specific 重新归属。
4. 明确 `workflows/` 只依赖 facade，不进入 provider core。

验收：

- `openai_compatible` 现有调用不回归。
- mock 仍可支撑本地链路验证。
- workflow 继续可运行。
- provider core 不再 import workflow-specific 代码。

### Phase 4：收尾清理与开发者入口定稿

目标：

- 去掉过渡态痕迹
- 固化 README 和扩展规则

主要改动：

1. 清理旧入口与旧 import。
2. 补充 README：
   - facade 用法
   - raw client 用法
   - provider 扩展示例
   - workflow 定位
3. 固化开发约束：
   - 何时放 `contracts`
   - 何时放 provider `facade`
   - 何时必须走 raw client
4. 与 PromptAtelier 的边界文档对齐。

验收：

- 新开发者只看 README 和架构文档即可理解扩展方式。
- 不再需要依赖历史上下文猜职责。
- PromptAtelier 可明确知道应该依赖 gateway 的哪一层。

---

## 10. 迁移过程中的稳定性策略

整个重构必须遵守以下策略：

1. 先加新结构，再迁引用，最后删旧结构。
2. 保留 facade 对外行为稳定。
3. PromptAtelier 主链路只认 raw client，不认 provider facade。
4. 真实出图验收优先于目录重构完成度。

说明：

- 对 PromptAtelier 来说，最重要的是 NovelAI 主链路不回退。
- 对 gateway 来说，最重要的是职责清晰，而不是一次性改成最优目录。

---

## 11. 与 PromptAtelier 的接入边界

### 短期

- PromptAtelier 继续通过 bridge 接入 gateway raw path。

### 中期

- PromptAtelier 直接依赖 gateway 的 NovelAI raw client。
- bridge 逐步变薄。

### 长期

- PromptAtelier 只依赖 gateway 的 raw contract 和 raw client。
- 不依赖 gateway facade、workflow、processing 内部细节。

原则：

- 如果调用方已经决定好了 prompt、negative、model、provider-specific params，就必须走 raw client。
- 如果调用方只需要通用 prompt 出图，才可以走 facade。

---

## 12. 开发规则清单

本节为强约束，而不是建议。

### 12.1 依赖方向规则

依赖方向固定为：

```text
contracts
  <- transports
  <- auth
  <- providers
  <- facade
  <- workflows
```

硬规则：

- provider core 不能 import workflow
- transport 不能 import provider facade
- contracts 不能 import service / router / provider 实现

### 12.2 职责归属规则

应放在 `contracts/` 的：

- 稳定输入输出模型
- 通用 request / result
- raw payload / result
- retry evidence

应放在 `transports/` 的：

- HTTP 请求发送
- timeout
- retry
- rate limit
- 通用错误分类

应放在 `auth/` 的：

- token 解析
- access key / login
- 环境变量 / 本地 fallback

应放在 `providers/*/raw_client.py` 的：

- 原始 payload 请求发送
- provider 原始响应解码入口
- provider 级别错误封装
- 不改 payload 的 raw 调用

应放在 `providers/*/facade.py` 的：

- `GenerateRequest -> provider payload`
- 轻量调用入口
- provider 特定参数默认值

应放在 `providers/*/decode.py` 的：

- zip 解包
- multipart 解码
- provider 响应格式解析

应放在 `workflows/` 的：

- 项目化脚本
- 具体业务流程
- 示例工作流

### 12.3 禁止事项

禁止在 gateway 里理解 PromptAtelier 业务节点，包括但不限于：

- character node
- action node
- artist node
- PromptBundle
- PromptPolicyPipeline
- `selected_keys`
- `character_scope`

禁止 raw client 改写调用方准备好的 payload，包括但不限于：

- 自动补 prompt
- 自动删空数组
- 自动补 `characterPrompts`
- 自动改 `params_version`
- 自动改 seed / size / sampler / steps

禁止 workflow 反向污染 provider core，包括但不限于：

- 项目路径约定
- 某业务包字段名
- 特定调用方本地目录结构

禁止 facade 重新长成新的“大一统业务层”。

### 12.4 新增 provider 的标准模板

新增 provider 时，优先使用如下模板：

```text
providers/
  provider_x/
    raw_client.py
    facade.py
    decode.py
```

最低要求：

- 若 provider 有稳定 raw API，应实现 `raw_client.py`
- 至少实现 facade generate 入口
- 响应解码逻辑应单独放入 `decode.py`

不建议：

- 再引入新的超大单文件 `providers/provider_x.py`

### 12.5 OpenAI-compatible 的特别规则

- NovelAI 是当前 raw-first 的重点 provider。
- OpenAI-compatible 当前可以 facade-first。
- 是否补 raw client，取决于未来是否出现严格 payload 控制的上层调用方。

即：

- 架构统一
- 实现深度可以不同

---

## 13. 验收策略

每个阶段至少从三个层次验收：

### 13.1 结构验收

- 文件职责是否收敛
- import 方向是否符合规则

### 13.2 接口验收

- facade 是否仍可用
- raw contract 是否稳定

### 13.3 业务验收

- NovelAI 真实出图
- PromptAtelier 接入不回归
- 参数一致性可验证
- 结果证据链完整

对 NovelAI 主链路，业务验收优先级高于代码层对称性和目录形式完整度。

---

## 14. 本轮范围结论

本轮 `ai-image-gateway` 架构重构的核心不是“大改目录”，而是完成三件事：

1. 把 raw client 与 facade 明确分开。
2. 把 provider core 与 workflow 明确分开。
3. 把 gateway 与 PromptAtelier 的边界写死。

这样后续无论是继续接入 PromptAtelier、扩展 NovelAI、接 OpenAI-compatible，还是补充 inpaint / image-to-image，都不会再次把 gateway 长成新的业务大仓库。

最终原则：

**`ai-image-gateway` 是 provider gateway / transport toolkit，不是提示词业务系统。**

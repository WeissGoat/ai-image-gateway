# 批量文生图模板

当你想指定一个提示词并生成多张候选图时，使用这个模板。脚本会在本地循环调用后端，
每次只请求 1 张图；这样对 Images API 后端和 Gemini / Grok 这类 chat image relay
都更通用。

可编辑 runner：

```text
examples/run_batch_generate.py
```

后端选择口径见：

```text
docs/openai_compatible_relay_integration.md#后端选择指南
```

修改脚本顶部变量：

```python
PROVIDER = "openai_images"
OUTPUT_ROOT = r"F:\design\game\project\p3\UnityClient\Assets\Art\_IncomingAI\TextToImageRuns"

PROMPT = """
生成一个精致的幻想游戏道具图标：发光的蓝色水晶罗盘。
单个主体居中，轮廓清晰，柔和灰色背景，不要文字，不要水印。
""".strip()

COUNT = 4
DELAY_SECONDS = 2
WIDTH = 1024
HEIGHT = 1024
```

运行：

```powershell
cd F:\design\game\project\p3\tools\ai-image-gateway
python examples\run_batch_generate.py
```

输出目录：

```text
UnityClient/Assets/Art/_IncomingAI/TextToImageRuns/batch_generate_<timestamp>/
```

每次运行会写入：

- `generated_00.png`、`generated_01.png` 等生成图。
- 每张图对应的 metadata JSON。
- `manifest.json`。

注意：

- `COUNT` 控制本地循环调用后端的次数。每次调用只请求 1 张图，因此 Gemini /
  Grok 这类 chat image relay 不需要原生支持 `n`。
- `DELAY_SECONDS` 控制两次后端调用之间的等待时间。
- 对 GPT image provider，`NEGATIVE_PROMPT` 会合并进主 prompt 文本；它不是原生
  negative prompt 通道。
- 如果需要真正的 negative prompt 语义，优先使用 `novelai` provider。

# 批量文件夹图生图模板

当你想把同一个提示词应用到文件夹内的每张图片时，使用这个模板。脚本会在本地循环调用后端，
每次只请求 1 张图；这样对 Images API 后端和 Gemini / Grok 这类 chat image relay
都更通用。

当前中转的默认推荐图生图 provider：

- `gemini_chat_image`
- 模型：`gemini-3.1-flash-image`
- endpoint：`/v1/chat/completions`

如果要使用 GPT image edits，可把 provider 改成 `openai_images`，但需要确认当前中转站已启用
`/v1/images/edits`。

后端选择口径见：

```text
docs/openai_compatible_relay_integration.md#后端选择指南
```

## Prompt 文件

先创建一个 prompt 文本文件，例如 `tmp/batch_i2i_prompt.txt`：

```text
以源图作为主体和构图参考。
把它转换成精修游戏资产插画。
保持主体居中、可读、干净。
使用精致的蓝色水晶幻想风格。
不要文字，不要水印，不要 logo，不要额外角色。
```

## Dry Run

只列出匹配到的输入图并写入 manifest，不调用真实 provider：

```powershell
cd F:\design\game\project\p3\tools\ai-image-gateway

python examples\batch_image_to_image_folder.py `
  --config config.local.yaml `
  --provider gemini_chat_image `
  --input-dir F:\path\to\input_images `
  --out-dir F:\path\to\output_images `
  --prompt-file F:\path\to\batch_i2i_prompt.txt `
  --dry-run
```

## 真实运行

如果你更喜欢改 Python 脚本，而不是每次手写命令行参数，修改下面文件顶部变量：

```text
examples/run_batch_i2i_folder.py
```

然后运行：

```powershell
python examples\run_batch_i2i_folder.py
```

第一次运行建议保持 `DRY_RUN = True`，确认输入匹配和输出目录无误后，再改成
`DRY_RUN = False` 调用真实 provider。

底层命令示例：

```powershell
cd F:\design\game\project\p3\tools\ai-image-gateway

python examples\batch_image_to_image_folder.py `
  --config config.local.yaml `
  --provider gemini_chat_image `
  --input-dir F:\path\to\input_images `
  --out-dir F:\path\to\output_images `
  --prompt-file F:\path\to\batch_i2i_prompt.txt `
  --width 1024 `
  --height 1024 `
  --count 3 `
  --delay 2
```

## 直接传入 prompt

```powershell
python examples\batch_image_to_image_folder.py `
  --config config.local.yaml `
  --provider gemini_chat_image `
  --input-dir F:\path\to\input_images `
  --out-dir F:\path\to\output_images `
  --prompt "把每张源图转换成精修蓝色水晶游戏图标，主体居中，不要文字，不要水印。"
```

## 常用选项

- `--recursive`：递归扫描子文件夹。
- `--pattern *.png --pattern *.webp`：覆盖默认匹配文件类型。
- `--limit 3`：只测试前 3 张输入图。
- `--count 3`：每张源图循环调用后端 3 次。每次调用只请求 1 张图，因此 Gemini /
  Grok 这类 chat image relay 不需要原生支持 `n`。
- `--delay 5`：放慢请求节奏，降低中转限流风险。
- `--negative-prompt "text, watermark, logo, blurry"`：共享 negative prompt。

每张源图都会得到独立输出子文件夹，包含：

- 生成图。
- 每张图对应的 metadata JSON。
- `record.json`。

根输出目录还会写入 `manifest.json`。

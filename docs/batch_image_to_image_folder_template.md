# Batch Image-to-Image Folder Template

Use this template when you want to apply one prompt to every image in a folder.

Default recommended provider for the current relay:

- `openai_images`
- model: `gpt-image-2`
- endpoint: `/v1/images/edits`

## Prompt File

Create a prompt text file, for example `tmp/batch_i2i_prompt.txt`:

```text
Use the source image as the main composition reference.
Transform it into a polished game asset illustration.
Keep the subject centered, readable, and clean.
Use a refined blue crystal fantasy style.
No text, no watermark, no logo, no extra characters.
```

## Dry Run

List matched inputs and write a manifest without calling the provider:

```powershell
cd F:\design\game\project\p3\tools\ai-image-gateway

python examples\batch_image_to_image_folder.py `
  --config config.local.yaml `
  --provider openai_images `
  --input-dir F:\path\to\input_images `
  --out-dir F:\path\to\output_images `
  --prompt-file F:\path\to\batch_i2i_prompt.txt `
  --dry-run
```

## Real Run

If you prefer editing a Python script instead of command-line arguments, change
the variables at the top of:

```text
examples/run_batch_i2i_folder.py
```

Then run:

```powershell
python examples\run_batch_i2i_folder.py
```

Keep `DRY_RUN = True` in the script for the first run. Set `DRY_RUN = False`
when the matched inputs and output folder look correct.

The lower-level command is:

```powershell
cd F:\design\game\project\p3\tools\ai-image-gateway

python examples\batch_image_to_image_folder.py `
  --config config.local.yaml `
  --provider openai_images `
  --input-dir F:\path\to\input_images `
  --out-dir F:\path\to\output_images `
  --prompt-file F:\path\to\batch_i2i_prompt.txt `
  --width 1024 `
  --height 1024 `
  --count 1 `
  --delay 2
```

## Inline Prompt

```powershell
python examples\batch_image_to_image_folder.py `
  --config config.local.yaml `
  --provider openai_images `
  --input-dir F:\path\to\input_images `
  --out-dir F:\path\to\output_images `
  --prompt "Turn each source into a polished blue crystal game icon, centered, no text, no watermark."
```

## Useful Options

- `--recursive`: scan subfolders.
- `--pattern *.png --pattern *.webp`: override matched file types.
- `--limit 3`: test only the first three inputs.
- `--delay 5`: slow down requests to reduce relay pressure.
- `--negative-prompt "text, watermark, logo, blurry"`: shared negative prompt.

Every source image gets its own output subfolder containing:

- generated image file(s)
- per-image metadata JSON
- `record.json`

The root output folder also contains `manifest.json`.

# Batch Text-to-Image Template

Use this template when you want to provide one prompt and generate several
candidate images.

Editable runner:

```text
examples/run_batch_generate.py
```

Change the variables at the top:

```python
PROVIDER = "openai_images"
OUTPUT_ROOT = r"F:\design\game\project\p3\UnityClient\Assets\Art\_IncomingAI\TextToImageRuns"

PROMPT = """
Create a polished fantasy game item icon of a luminous blue crystal compass.
Single centered object, clean silhouette, soft gray background, no text, no watermark.
""".strip()

COUNT = 4
WIDTH = 1024
HEIGHT = 1024
```

Run:

```powershell
cd F:\design\game\project\p3\tools\ai-image-gateway
python examples\run_batch_generate.py
```

Outputs are written to:

```text
UnityClient/Assets/Art/_IncomingAI/TextToImageRuns/batch_generate_<timestamp>/
```

Each run writes:

- `generated_00.png`, `generated_01.png`, ...
- per-image metadata JSON
- `manifest.json`

Notes:

- `COUNT` controls how many images to request.
- For GPT image providers, `NEGATIVE_PROMPT` is merged into the main prompt text;
  it is not a native negative prompt channel.
- For NovelAI, use the `novelai` provider if you want true negative prompt
  semantics.

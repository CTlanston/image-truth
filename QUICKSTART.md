# Quickstart

Audit your first manifest in under five minutes.

## 1. Install

```bash
pip install "image-truth[all] @ git+https://github.com/CTlanston/image-truth"
```

(Install is from source today — a PyPI package is planned. The `[all]` extra adds OCR, the Claude SDK, and YAML support.)

The duplicate and watermark checks need the **tesseract** OCR binary:

```bash
# macOS
brew install tesseract
# Debian / Ubuntu
sudo apt-get install -y tesseract-ocr
```

(Without tesseract the watermark check reports `UNVERIFIED` and everything else
still runs.)

## 2. Write a manifest

`image-truth` reads JSON, YAML, or a markdown table. The simplest is a JSON list:

```json
[
  { "image": "images/hero.jpg",     "claimed_location": "Big Sur, California", "caption": "Bixby Bridge at sunset", "page": "index" },
  { "image": "images/gallery-1.jpg", "claimed_location": "Big Sur, California", "caption": "coastal cliffs",         "page": "gallery" }
]
```

Only `image` is required; `claimed_location` and `caption` unlock the C3/C4
vision checks. Local paths (relative to the manifest) and `https://` image URLs
both work.

## 3. Run it

```bash
image-truth check manifest.json
```

You'll get a summary in the terminal and two files next to it:

- **`report.md`** — a contact sheet: counts on top, one card per image, rejects
  first, one-line reason each.
- **`report.json`** — the same data, machine-readable, with per-check detail.

## 4. Turn on the vision checks (optional)

C3 (location match) and C4 (caption match) call a vision model. Set **any one**
of these keys and image-truth picks it up automatically:

| Provider | Key env | Default model | Get a key |
|---|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | `gemini-3.1-flash-lite` | https://aistudio.google.com/app/apikey (free tier) |
| 阿里云百炼 DashScope | `DASHSCOPE_API_KEY` | `qwen3-vl-flash` | https://bailian.console.aliyun.com/?apiKey=1 |
| 火山方舟 Ark (豆包) | `ARK_API_KEY` | `doubao-seed-2-0-mini-260428` | https://console.volcengine.com/ark |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `claude-sonnet-5` | https://console.anthropic.com/settings/keys |

```bash
export GEMINI_API_KEY=...          # cheapest managed option; free tier covers daily use
image-truth check manifest.json

# pick explicitly, or upgrade quality:
image-truth check manifest.json --provider anthropic --model claude-haiku-4-5
```

When several keys are configured, auto-detection prefers
`gemini → dashscope → ark → anthropic`; `--provider` / `IMAGE_TRUTH_PROVIDER`
overrides it. DashScope International users: set `IMAGE_TRUTH_BASE_URL` to
`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`.

Responses are cached by image content hash in `.image-truth-cache/`, so a
second run is instant and identical. Add `--no-cache` to force fresh calls.
Override the model with `--model` or `IMAGE_TRUTH_VISION_MODEL`.

## 5. Gate your deploy

Add `--ci` and wire it into CI. It exits `1` on any REJECT (failing the job),
`0` when everything is KEEP or ADVISE, and `2` on a broken manifest.

```yaml
# .github/workflows/images.yml
name: images
on: [push, pull_request]
jobs:
  image-truth:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: sudo apt-get update && sudo apt-get install -y tesseract-ocr
      - run: pip install "image-truth[all] @ git+https://github.com/CTlanston/image-truth"
      - run: image-truth check IMAGE_CREDITS.md --ci
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}         # optional (or ANTHROPIC/DASHSCOPE/ARK key)
```

That's the whole gate: copy those ten lines, point them at your manifest, and a
duplicated, watermarked, or wrong-location image can't reach production.

## Already have an `IMAGE_CREDITS.md`?

If your site tracks images in a markdown table with `Place` / `Subject` /
`Local path` columns (the `IMAGE_CREDITS.md` convention), point `image-truth`
straight at it — no reformatting:

```bash
image-truth check IMAGE_CREDITS.md --ci
```

The parser maps common column headers automatically, follows markdown links and
backticked paths, and ignores non-image source links.

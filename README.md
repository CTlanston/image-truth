# image-truth

[![ci](https://github.com/CTlanston/image-truth/actions/workflows/ci.yml/badge.svg)](https://github.com/CTlanston/image-truth/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A CI gate that stops wrong images from shipping.** Point it at a manifest of
your site's images and it flags the four ways image selection goes wrong in
production: the *same photo reused on two pages*, a *stock watermark* nobody
noticed, a *hero captioned as one place that is actually somewhere else*, and a
*caption that doesn't match the picture*. It exits non-zero so your deploy
stops.

This exists because these bugs are real and repeated: a travel site shipped the
same beach photo as two different "days", a Santa Barbara slot holding an LA
palm photo, watermarked and wrong-location heroes going live — the kind of thing
a human reviewer catches on a good day and misses on a deadline. `image-truth`
catches them every time, in CI.

```bash
pip install "image-truth[all] @ git+https://github.com/CTlanston/image-truth"
image-truth check IMAGE_CREDITS.md --ci   # exit 1 if any image is rejected
```

> Install is from source today (PyPI package planned). The `[all]` extra pulls in
> OCR (pytesseract), the Claude SDK, and YAML manifest support.

## What it checks

| Check | What it catches | How | Verdict |
|-------|-----------------|-----|---------|
| **C1 duplicate** | the same photo reused across pages/slots — even resized, cropped, re-encoded, or lightly color-graded | perceptual hash (pHash + dHash) with center-crop hash families | REJECT |
| **C2 watermark** | stock-agency watermarks, "SAMPLE"/© overlays, tiled logos | OCR (corner, diagonal, and tiled passes) + a stock-vocabulary matcher | REJECT |
| **C3 location** | a photo captioned as one place that is visibly somewhere else | a vision model answers "is this plausibly a photo of {place}?" | REJECT / UNSURE |
| **C4 caption** | a caption whose key subjects aren't actually in the picture | a vision model checks the caption's nouns against the image | REJECT / UNSURE |
| **C5 aesthetic** | too-low resolution, extreme aspect, blur — advisory only, never blocks | resolution / aspect / sharpness heuristics | ADVISE |

C1, C2, and C5 are deterministic and run offline. C3 and C4 call the Claude API
(`claude-sonnet-5` by default) and cache every response by image content hash,
so re-runs are reproducible and free. **No API key? C3/C4 report `UNVERIFIED`
and the rest of the gate still runs** — they never fake a verdict.

## Sample report

`image-truth` writes a `report.md` that reads like a photo editor's contact
sheet — summary counts on top, one card per image, rejects first:

```
# image-truth report

**10 images** · ✅ 4 keep · ❌ 3 reject · ⚠️ 3 advise

## ❌ REJECT — 07-potato-chip-rock.png
> c1: duplicate of potato-chip-rock.png

## ❌ REJECT — 00-bay-area-night-skyline.jpg
> c3: downtown San Francisco (Salesforce Tower, Bay Bridge, City Hall dome
>     visible), not Sunnyvale

## ⚠️ ADVISE — 05-grand-central-market.jpg
> c5: low resolution (500×375) for full-bleed display
```

Every card also carries the per-check breakdown; `report.json` has the full
machine-readable detail. These are real findings from the audit in
[`examples/`](examples/) — the tool rediscovered a live duplicate and a
mislabeled hero on an actual travel site, unaided.

## Use it as a deploy gate

```yaml
# .github/workflows/images.yml
- run: pip install "image-truth[all] @ git+https://github.com/CTlanston/image-truth"
- run: image-truth check IMAGE_CREDITS.md --ci
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}   # optional — enables C3/C4
```

`--ci` exits `1` on any REJECT (fails the job), `0` when everything is
KEEP/ADVISE, and `2` on a bad manifest. Advisories never block.

## Manifest formats

JSON, YAML, or a markdown table — including the **`IMAGE_CREDITS.md`
convention** (`Place` / `Subject` / `Local path` columns) with zero config:

```markdown
| Page | Place | Subject | Local path |
|------|-------|---------|------------|
| index.html | Big Sur, California | Bixby Bridge at sunset | images/heroes/00-hero.jpg |
```

Local paths and remote image URLs both work (URLs are downloaded with a
timeout). See [QUICKSTART.md](QUICKSTART.md) to audit your first manifest in
under five minutes.

## License

MIT — see [LICENSE](LICENSE). Fixture base images are CC0 / public-domain from
Wikimedia Commons (see [`fixtures/SOURCES.md`](fixtures/SOURCES.md)).

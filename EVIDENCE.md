# EVIDENCE

Every "done" claim below is backed by a fresh run artifact (command + output).
Nothing without a log counts as done.

Vision checks (C3/C4) run on the Claude API, model `claude-sonnet-5`
(user-mandated cheaper model), key stored in git-ignored `.env`.

---
## Iteration 1 — fixture set (pre-G1) · 2026-07-09

**Claim**: 120 labeled fixture cases built from 43 CC0/public-domain Wikimedia
Commons bases; every base visually verified against its labels.

```
$ python3 fixtures/make_fixtures.py --download
downloaded bases: 43/43        # licenses verified via Commons extmetadata

$ python3 fixtures/make_fixtures.py --derive
cases: 120  counts: {'duplicate': 30, 'watermark': 20, 'location_mismatch': 25,
'clean': 25, 'caption_mismatch': 20}  bundles: 3

$ python3 -m pytest tests/ -q
8 passed in 0.08s
```

Visual QA (multi-agent, each base image inspected against expected
location/caption): round 1 = 30/44 MATCH, 14 flagged (3 outright mismatches:
a sand cat labeled "Sahara dunes", an astronaut portrait labeled "Chicago
bean", a museum pavilion labeled "Matterhorn"). 12 subjects re-queried or
replaced, 4 captions corrected to match visible content. Round 2 on 11
new/changed bases = 9 MATCH + 2 caption-corrected. Final: 43/43 verified.

Notes: subjects whose free-license images don't exist on Commons (Big Ben,
Taj Mahal, Petra, Santorini, Sydney Opera, Cloud Gate...) were dropped in
favor of reliably public-domain US NPS/government subjects.

## Iteration 2 — checks C1–C5 (G1) · 2026-07-09

**Claim**: five independent check modules, each unit-tested against the
labeled fixtures.

```
$ python3 -m pytest tests/ -q          # C1/C2/C5 + fixtures (pre-C3/C4)
19 passed, 1 skipped in 122.08s
$ python3 -m pytest tests/test_c3_c4.py -q     # vision checks, live API
7 passed in 29.44s
```

- **C1 (pHash+dHash)**: thresholds calibrated on fixtures — true derived
  pairs max (phash 4, dhash 5); closest distinct-photo impostors (14, 17).
  Locked at ≤10/≤14 with per-image hash families (full + 90/80/70% center
  crops). 30/30 derived duplicates detected, 0 false positives across
  bundles.
- **C2 (OCR watermark)**: 20/20 synthetic watermarks detected
  (corner/diagonal/tile), 0/25 clean false positives — including Times
  Square/Shibuya billboard scenes. Pass pipeline: gray → edge strips +
  corner patches (2×, despeckled luminance masks) → full-frame masks →
  rotated center-band high-pass (catches diagonal SAMPLE bands over
  textured scenes). Fuzzy vocab (edit-distance 1) + line joining for OCR
  fragmentation. Degrades to UNVERIFIED without tesseract.
- **C3/C4 (vision, claude-sonnet-5)**: live smoke 6/6 correct at ≥0.97
  confidence (incl. hard case: Golden Gate photo claimed as Brooklyn Bridge
  → FAIL "the bridge visible in the fog is the Golden Gate Bridge").
  Prompt made medium-agnostic after a real catch: vintage Stonehenge
  postcard art was rejected as "not a photograph" — C3 judges place, not
  medium. Disk cache by (content hash, model, check, prompt): cache-hit
  determinism test green. No key → UNVERIFIED.
- **C5 (aesthetic advisory)**: resolution floor / aspect / blur heuristics,
  WARN-only (never blocks); tested on synthetic + curated bases.

## Iteration 3 — CLI pipeline (G2) · 2026-07-09

**Claim**: `image-truth check <manifest> --ci` works end-to-end on a mixed
10-case manifest; exit codes honor the CI contract; legacy IMAGE_CREDITS.md
tables parse unmodified.

```
$ image-truth check fixtures/g2_manifest.md --ci
image-truth: 12 images — 2 keep, 10 reject, 0 advise   (exit 1)
# all 10 case verdicts match ground truth (dup/wm/loc/cap REJECT, clean KEEP)
# 39s cold C2 cache; warm re-run ~12s

$ python3 -m pytest tests/test_g2_cli.py -q
11 passed in 1.60s
```

- Manifest parser: .md tables (header-alias mapping), .json, .yaml, and the
  IMAGE_CREDITS.md convention (Place/Subject/Local path columns, markdown
  links, backticked paths) — parser test reproduces the twelve-days-west
  table shapes.
- Aggregator: FAIL(c1–c4) → REJECT; UNSURE/WARN → ADVISE (never blocks);
  unreadable/missing image → REJECT "could not be loaded" (fixed: local
  missing files previously slipped through as UNVERIFIED).
- report.md is a contact sheet: summary counts, rejects first, one-line
  reason per card, per-check detail beneath.
- Exit codes: 0 without --ci even when rejects exist; 1 with --ci and any
  REJECT; 2 on manifest errors. C2 OCR results disk-cached by content hash
  (same store as vision cache).

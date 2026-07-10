"""C2 — watermark / text-overlay detection via OCR + heuristics.

Distinguishes overlay watermarks from legitimate scene text (billboards,
signs): a FAIL requires watermark *signals* — © marks, stock-agency
vocabulary (fuzzy-matched, OCR mangles letters), "sample"/"proof" overlays,
or the same token tiled across the frame — not merely the presence of text.

Layered passes, cheapest / highest-yield first, early exit on a hit:
  1. full-frame grayscale (strong overlay text)
  2. top/bottom strips + corner patches at 2x, luminance masks with
     despeckling (corner credit marks, small tiles)
  3. full-frame high-luminance masks (white semi-transparent overlays)
  4. rotated center-band high-pass masks (diagonal watermark bands over
     textured scenes — sharp strokes survive despeckling, texture doesn't)

Requires the tesseract binary + pytesseract; degrades to UNVERIFIED without.
"""

from __future__ import annotations

import re
from collections import Counter

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ..model import FAIL, PASS, UNVERIFIED, CheckResult

try:
    import pytesseract

    pytesseract.get_tesseract_version()
    HAVE_OCR = True
except Exception:  # noqa: BLE001 — missing binary, missing module, broken install
    HAVE_OCR = False

CHECK = "c2"

# vocabulary fuzzy-matched against OCR words (edit distance <= 1, or substring)
VOCAB = (
    "sample", "stockphoto", "photobank", "shutterstock", "gettyimages",
    "istock", "istockphoto", "alamy", "dreamstime", "depositphotos",
    "bigstock", "fotolia", "adobestock", "watermark", "copyright",
    "preview", "proof", "123rf", "sampleimage",
)
COPYRIGHT_RE = re.compile(r"©|\(c\)\s|\bcopyright\b", re.IGNORECASE)
MIN_WORD_CONF = 40
TILE_REPEATS = 3
LUM_THRESHOLDS = (170, 200, 225)


def _edit1(a: str, b: str) -> bool:
    """True if edit distance(a, b) <= 1 (same-ish length fast path)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:  # one substitution
        return sum(x != y for x, y in zip(a, b)) <= 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    # one insertion into a
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
        elif diff:
            return False
        else:
            diff = 1
        j += 1
    return True


# ordinary English words one edit away from vocab entries (scene-text FP risk)
NOT_WATERMARKS = frozenset({"simple", "sampled", "sampler", "samples", "roof", "prof"})


def _vocab_hit(word: str):
    w = re.sub(r"[^a-z0-9]", "", word.lower())
    if len(w) < 4 or w in NOT_WATERMARKS:
        return None
    for v in VOCAB:
        if v in w:
            return v
        if len(w) >= 5 and _edit1(w, v):
            return v
    return None


def _ocr_words(img: Image.Image) -> list:
    data = pytesseract.image_to_data(
        img, config="--psm 11", output_type=pytesseract.Output.DICT
    )
    words = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        conf = float(data["conf"][i]) if data["conf"][i] not in ("", "-1") else -1.0
        if len(text) >= 3 and conf >= MIN_WORD_CONF:
            cx = data["left"][i] + data["width"][i] / 2
            cy = data["top"][i] + data["height"][i] / 2
            line = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            words.append((text, conf, cx, cy, line))
    return words


def _lines(words: list) -> list:
    """Words joined per OCR line — catches 'Sam PLE IMAGE' fragmentation."""
    grouped = {}
    for w in words:
        grouped.setdefault(w[4], []).append(w[0])
    return [" ".join(ws) for ws in grouped.values()]


def _lum_mask(img: Image.Image, thresh: int) -> Image.Image:
    a = np.asarray(img.convert("L"))
    return ImageOps.invert(Image.fromarray(((a >= thresh) * 255).astype(np.uint8)))


def _shrink(img: Image.Image, cap: int = 1400) -> Image.Image:
    if max(img.size) > cap:
        img = img.copy()
        img.thumbnail((cap, cap))
    return img


def _signals(words: list, frame_size: tuple) -> tuple:
    """(vocab_hits, copyright_hits, tiled) from one OCR pass."""
    vocab_hits = [w[0] for w in words if _vocab_hit(w[0])]
    vocab_hits += [ln for ln in _lines(words) if len(ln.split()) > 1 and _vocab_hit(ln)]
    copyright_hits = [w[0] for w in words if COPYRIGHT_RE.search(w[0])]
    tiled = []
    counts = Counter(re.sub(r"[^a-z0-9]", "", w[0].lower()) for w in words)
    for token, n in counts.items():
        if n >= TILE_REPEATS and len(token) >= 4 and not token.isdigit():
            pts = [
                (w[2], w[3])
                for w in words
                if re.sub(r"[^a-z0-9]", "", w[0].lower()) == token
            ]
            if (
                max(p[0] for p in pts) - min(p[0] for p in pts) > frame_size[0] * 0.3
                or max(p[1] for p in pts) - min(p[1] for p in pts) > frame_size[1] * 0.3
            ):
                tiled.append((token, n))
    return vocab_hits, copyright_hits, tiled


def _edge_strips(img: Image.Image):
    """Bottom/top strips at 2x (corner credit marks live there), despeckled."""
    w, h = img.size
    for label, box in (
        ("bottom", (0, int(h * 0.72), w, h)),
        ("top", (0, 0, w, int(h * 0.28))),
    ):
        p = img.crop(box)
        p = p.resize((p.width * 2, p.height * 2))  # bicubic: lanczos ringing hurts OCR here
        yield f"{label}-gray", ImageOps.autocontrast(p.convert("L"), cutoff=1)
        m = _lum_mask(p, 195)
        yield f"{label}-mask", m
        yield f"{label}-mask-k3", m.filter(ImageFilter.MaxFilter(3))
        yield f"{label}-mask-k5", m.filter(ImageFilter.MaxFilter(5))


def _corner_patches(img: Image.Image):
    """4 corner quadrant patches at 2x — small tiles get lost in full-frame noise."""
    w, h = img.size
    cw, ch = int(w * 0.35), int(h * 0.35)
    for i, box in enumerate((
        (0, h - ch, cw, h), (w - cw, h - ch, w, h),
        (0, 0, cw, ch), (w - cw, 0, w, ch),
    )):
        p = img.crop(box)
        p = p.resize((p.width * 2, p.height * 2))
        yield f"corner{i}", ImageOps.autocontrast(p.convert("L"), cutoff=1)
        yield f"corner{i}-mask", _lum_mask(p, 200)


def _highpass_mask(img: Image.Image, radius: int, delta: int, despeckle: int) -> Image.Image:
    """Sharp bright strokes (overlay text) survive; smooth texture doesn't."""
    g = img.convert("L")
    a = np.asarray(g, dtype=np.float64)
    b = np.asarray(g.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float64)
    m = ImageOps.invert(Image.fromarray((((a - b) > delta) * 255).astype(np.uint8)))
    return m.filter(ImageFilter.MaxFilter(despeckle))


def _band_strips(img: Image.Image):
    """Diagonal center-band passes: rotate level, crop the band, denoise."""
    for angle in (-30, 30):
        rot = img.rotate(angle, expand=True, fillcolor=(0, 0, 0))
        strip = rot.crop((0, int(rot.height * 0.32), rot.width, int(rot.height * 0.68)))
        for radius, delta, despeckle in ((16, 10, 3), (24, 8, 5)):
            m = _highpass_mask(strip, radius, delta, despeckle)
            yield f"band{angle}-hp{radius}", m
            yield f"band{angle}-hp{radius}-half", m.resize((m.width // 2, m.height // 2))


def _passes(img: Image.Image):
    """Yield (pass_name, frame) cheapest / highest-yield first."""
    base = _shrink(img)
    yield "gray", ImageOps.autocontrast(base.convert("L"), cutoff=1)
    yield from _edge_strips(base)
    yield from _corner_patches(base)
    for t in LUM_THRESHOLDS:
        yield f"mask{t}", _lum_mask(base, t)
    yield from _band_strips(base)


def run_one(entry) -> CheckResult:
    if not HAVE_OCR:
        return CheckResult(
            CHECK, UNVERIFIED,
            reason="tesseract OCR not available — install tesseract + pip install pytesseract",
        )
    try:
        with Image.open(entry.local_path) as img:
            img.load()
            img = img.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(CHECK, UNVERIFIED, reason=f"could not read image: {exc}")

    for pass_name, frame in _passes(img):
        words = _ocr_words(frame)
        if not words:
            continue
        vocab_hits, copyright_hits, tiled = _signals(words, frame.size)
        if vocab_hits or copyright_hits:
            hits = sorted(set(vocab_hits + copyright_hits))
            return CheckResult(
                CHECK, FAIL, confidence=0.95,
                reason=f"watermark text detected: “{', '.join(hits[:3])}”",
                details={"pass": pass_name, "hits": hits, "tiled": tiled},
            )
        if tiled:
            token, n = max(tiled, key=lambda t: t[1])
            return CheckResult(
                CHECK, FAIL, confidence=0.8,
                reason=f"tiled overlay text: “{token}” ×{n}",
                details={"pass": pass_name, "tiled": tiled},
            )
    return CheckResult(CHECK, PASS, confidence=0.9, reason="no watermark signals")


def run(entries: list) -> list:
    return [run_one(e) for e in entries]

"""C5 — aesthetic advisory (never blocks): would you still want this at 100vh?

Deterministic heuristics only: resolution floor, extreme aspect ratio,
blur (Laplacian variance). Output is WARN (rendered as ADVISE) or PASS.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from ..model import PASS, UNVERIFIED, WARN, CheckResult

CHECK = "c5"

MIN_LONG_EDGE = 1000         # full-bleed hero needs a long edge this big
MIN_SHORT_EDGE = 500         # portrait heroes are legitimately narrow; only tiny is bad
MAX_ASPECT = 3.0             # width:height (either way) beyond this is banner-ish
BLUR_VARIANCE_FLOOR = 60.0   # Laplacian variance below this reads as soft/blurry


def _laplacian_variance(gray: np.ndarray) -> float:
    k = gray.astype(np.float64)
    lap = (
        -4 * k[1:-1, 1:-1]
        + k[:-2, 1:-1]
        + k[2:, 1:-1]
        + k[1:-1, :-2]
        + k[1:-1, 2:]
    )
    return float(lap.var())


def run_one(entry) -> CheckResult:
    try:
        with Image.open(entry.local_path) as img:
            w, h = img.size
            g = img.convert("L")
            if max(g.size) > 1024:
                g.thumbnail((1024, 1024))
            gray = np.asarray(g)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(CHECK, UNVERIFIED, reason=f"could not read image: {exc}")

    advisories = []
    if max(w, h) < MIN_LONG_EDGE or min(w, h) < MIN_SHORT_EDGE:
        advisories.append(f"low resolution ({w}×{h}) for full-bleed display")
    aspect = max(w / h, h / w)
    if aspect > MAX_ASPECT:
        advisories.append(f"extreme aspect ratio ({aspect:.1f}:1)")
    blur = _laplacian_variance(gray)
    if blur < BLUR_VARIANCE_FLOOR:
        advisories.append(f"image looks soft/blurry (sharpness {blur:.0f})")

    if advisories:
        return CheckResult(
            CHECK, WARN, confidence=0.7, reason="; ".join(advisories),
            details={"width": w, "height": h, "sharpness": round(blur, 1)},
        )
    return CheckResult(CHECK, PASS, confidence=0.8, reason="no aesthetic concerns")


def run(entries: list) -> list:
    return [run_one(e) for e in entries]

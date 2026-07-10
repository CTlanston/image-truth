"""C4 — caption-image consistency: are the caption's key subjects visible?

Vision-model check (Claude, cached by content hash). A caption fails when it
describes things that are not in the picture (e.g. "a convertible on a coastal
road" over a photo of a beach). Under-description is fine — captions never
enumerate everything.
"""

from __future__ import annotations

from ..model import FAIL, PASS, UNSURE, UNVERIFIED, CheckResult
from ..vision import VisionClient, VisionUnavailable

CHECK = "c4"

PROMPT = """You are an image fact-checker. The image above carries this caption:

  "{caption}"

Question: does the image actually show what the caption describes? Check that \
the caption's key subjects — its main nouns and their attributes (colors, \
counts, settings) — are visible in the image.

Answer "no" if a key subject of the caption is absent or clearly different \
(the caption describes another scene). Answer "yes" if the caption's subjects \
are visible; the caption does NOT need to mention everything in the image. \
Answer "unsure" only if you genuinely cannot tell. Give a one-sentence reason \
naming any missing subjects."""


def run_one(entry, client: VisionClient) -> CheckResult:
    if not entry.caption:
        return CheckResult(CHECK, PASS, confidence=0.0, reason="no caption to verify")
    prompt = PROMPT.format(caption=entry.caption)
    try:
        v = client.ask(entry.local_path, CHECK, prompt)
    except VisionUnavailable as exc:
        return CheckResult(CHECK, UNVERIFIED, reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        return CheckResult(CHECK, UNVERIFIED, reason=f"vision call failed: {exc}")
    status = {"yes": PASS, "no": FAIL, "unsure": UNSURE}[v["answer"]]
    return CheckResult(
        CHECK, status, confidence=v["confidence"],
        reason=v["reason"] if status != PASS else "caption matches image",
        details={"answer": v["answer"], "cached": v.get("cached", False)},
    )


def run(entries: list, client: VisionClient = None) -> list:
    client = client or VisionClient()
    return [run_one(e, client) for e in entries]

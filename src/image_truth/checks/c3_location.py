"""C3 — location/content match: is this plausibly a photo of the claimed place?

Vision-model check (Claude, cached by content hash). Conservative and honest:
UNSURE when the model can't decide; UNVERIFIED when no API key is available.
"""

from __future__ import annotations

from ..model import FAIL, PASS, UNSURE, UNVERIFIED, CheckResult
from ..vision import VisionClient, VisionUnavailable

CHECK = "c3"

PROMPT = """You are an image fact-checker for a travel website. The image above is \
labeled as showing this location:

  "{location}"

Question: does this image plausibly depict {location}? Judge only from what \
is visible — landmarks, architecture, terrain, vegetation, climate, signage, \
street furniture. The image may be a photograph or an artwork/illustration; \
judge whether the depicted place matches the label, not the medium. You cannot \
GPS-verify; "plausibly" means a knowledgeable person would accept the label.

Answer "no" if the visible content contradicts the claimed location (wrong \
landmark, wrong terrain/climate, identifiably a different place). Answer "yes" \
if the content is consistent with it. Answer "unsure" only if the image is too \
generic to judge either way. Give a one-sentence reason."""


def run_one(entry, client: VisionClient) -> CheckResult:
    if not entry.claimed_location:
        return CheckResult(CHECK, PASS, confidence=0.0, reason="no location claim to verify")
    prompt = PROMPT.format(location=entry.claimed_location)
    try:
        v = client.ask(entry.local_path, CHECK, prompt)
    except VisionUnavailable as exc:
        return CheckResult(CHECK, UNVERIFIED, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — API failure must not kill the audit
        return CheckResult(CHECK, UNVERIFIED, reason=f"vision call failed: {exc}")
    status = {"yes": PASS, "no": FAIL, "unsure": UNSURE}[v["answer"]]
    return CheckResult(
        CHECK, status, confidence=v["confidence"],
        reason=v["reason"] if status != PASS else f"consistent with {entry.claimed_location}",
        details={"answer": v["answer"], "cached": v.get("cached", False)},
    )


def run(entries: list, client: VisionClient = None) -> list:
    client = client or VisionClient()
    return [run_one(e, client) for e in entries]

"""Report rendering: a contact-sheet report.md + machine-readable report.json.

report.md reads like a photo editor's contact sheet: summary counts on top,
then one card per image — verdict, slot, one-line reason. A human decides in
seconds; details sit under each card.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .model import ADVISE, KEEP, REJECT, UNVERIFIED, exit_code

BADGE = {KEEP: "✅ KEEP", REJECT: "❌ REJECT", ADVISE: "⚠️ ADVISE"}
CHECK_NAMES = {
    "c1": "duplicate", "c2": "watermark", "c3": "location",
    "c4": "caption", "c5": "aesthetic",
}


def to_json(verdicts: list, manifest_path: str) -> dict:
    return {
        "tool": "image-truth",
        "manifest": manifest_path,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary(verdicts),
        "images": [
            {
                "image": v.entry.image,
                "page": v.entry.page,
                "slot": v.entry.slot,
                "claimed_location": v.entry.claimed_location,
                "caption": v.entry.caption,
                "verdict": v.verdict,
                "reason": v.reason,
                "checks": [
                    {
                        "check": r.check,
                        "name": CHECK_NAMES[r.check],
                        "status": r.status,
                        "confidence": r.confidence,
                        "reason": r.reason,
                        "details": r.details,
                    }
                    for r in v.results
                ],
            }
            for v in verdicts
        ],
        "exit_code": exit_code(verdicts),
    }


def summary(verdicts: list) -> dict:
    unverified = sorted({c for v in verdicts for c in v.unverified_checks})
    return {
        "total": len(verdicts),
        "keep": sum(v.verdict == KEEP for v in verdicts),
        "reject": sum(v.verdict == REJECT for v in verdicts),
        "advise": sum(v.verdict == ADVISE for v in verdicts),
        "unverified_checks": unverified,
    }


def to_markdown(verdicts: list, manifest_path: str) -> str:
    s = summary(verdicts)
    lines = [
        "# image-truth report",
        "",
        f"**{s['total']} images** · ✅ {s['keep']} keep · ❌ {s['reject']} reject · ⚠️ {s['advise']} advise",
        f"Manifest: `{manifest_path}`",
    ]
    if s["unverified_checks"]:
        names = ", ".join(CHECK_NAMES[c] for c in s["unverified_checks"])
        lines.append(
            f"\n> ⚠️ **Unverified checks:** {names} could not run "
            "(missing API key or OCR). Their verdicts are not included above."
        )
    lines.append("")

    # rejects first — that's what a human needs to act on
    order = {REJECT: 0, ADVISE: 1, KEEP: 2}
    for v in sorted(verdicts, key=lambda v: (order[v.verdict], v.entry.image)):
        where = " · ".join(x for x in (v.entry.page, v.entry.slot) if x)
        lines.append(f"## {BADGE[v.verdict]} — `{v.entry.image}`")
        if where:
            lines.append(f"*{where}*")
        lines.append(f"> {v.reason}")
        lines.append("")
        for r in v.results:
            icon = {
                "PASS": "·", "FAIL": "✗", "WARN": "△",
                "UNSURE": "?", "UNVERIFIED": "–",
            }[r.status]
            conf = f" ({r.confidence:.0%})" if r.status in ("FAIL", "WARN") else ""
            lines.append(f"- {icon} **{CHECK_NAMES[r.check]}** {r.status}{conf}: {r.reason}")
        lines.append("")
    return "\n".join(lines)

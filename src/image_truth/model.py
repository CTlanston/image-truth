"""Shared data model: manifest entries, per-check results, verdicts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Final per-image verdicts
KEEP = "KEEP"
REJECT = "REJECT"
ADVISE = "ADVISE"

# Per-check statuses
PASS = "PASS"          # check ran, no problem found
FAIL = "FAIL"          # check ran, problem found (blocking)
WARN = "WARN"          # advisory only (never blocks)
UNSURE = "UNSURE"      # check ran but can't decide — never fake confidence
UNVERIFIED = "UNVERIFIED"  # check could not run (e.g. no API key)


@dataclass
class Entry:
    """One image slot from a manifest."""

    image: str                      # local path or URL as written in the manifest
    claimed_location: str = ""
    caption: str = ""
    page: str = ""
    slot: str = ""
    local_path: str = ""            # resolved/downloaded absolute path (set by pipeline)

    @property
    def ref(self) -> str:
        """Human-readable slot reference for reports."""
        where = " · ".join(x for x in (self.page, self.slot) if x)
        return f"{self.image}" + (f" ({where})" if where else "")


@dataclass
class CheckResult:
    """Outcome of one check for one entry."""

    check: str                      # "c1".."c5"
    status: str                     # PASS/FAIL/WARN/UNSURE/UNVERIFIED
    confidence: float = 0.0         # 0..1, meaningful for FAIL/WARN/PASS
    reason: str = ""                # one-line human explanation
    details: dict = field(default_factory=dict)


@dataclass
class ImageVerdict:
    """Aggregated verdict for one entry."""

    entry: Entry
    verdict: str                    # KEEP/REJECT/ADVISE
    results: list = field(default_factory=list)  # list[CheckResult]
    reason: str = ""                # the headline reason shown on the card

    @property
    def unverified_checks(self) -> list:
        return [r.check for r in self.results if r.status == UNVERIFIED]


def exit_code(verdicts: list) -> int:
    """CI contract: 1 if any REJECT, else 0. ADVISE never blocks."""
    return 1 if any(v.verdict == REJECT for v in verdicts) else 0


def has_unverified(verdicts: list) -> bool:
    return any(v.unverified_checks for v in verdicts)

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

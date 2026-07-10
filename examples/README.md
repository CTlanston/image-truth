# examples — real-world audit (G4)

These artifacts come from running `image-truth` **read-only** against the
`twelve-days-west` travel site, whose `IMAGE_CREDITS.md` had documented image
bugs. The tool rediscovered them **unaided** — it was never told which images
were duplicates or mislabeled; C1 hashes content and C3 reads the picture.

## `twelve-days-west-legacy-heroes.md` — runnable, reproduces the known duplicate

The pre-dedup hero table (reconstructed from the site's own `audit-baseline.json`).
Run it yourself:

```bash
image-truth check examples/twelve-days-west-legacy-heroes.md --ci
```

- **C1 rediscovers the `d625272157b7` cross-page duplicate** — the same
  Unsplash photo was used for both the homepage hero and the chapter-2
  "Lanikai" slot. The tool flags both at 100% confidence.
- **C3 catches the Hawaii-on-California mislabel** — a tropical-islands photo
  (`6ccdb62f86ef`) captioned as Big Sur, California is rejected: "tropical
  turquoise waters… not the rocky, rugged coastline of Big Sur."

## `twelve-days-west-audit-report.md` — the tool's report on the current site

Auditing the eight live chapter heroes. Notable rediscovered findings:

- **REJECT · duplicate**: `07-potato-chip-rock.png` and `potato-chip-rock.png`
  are byte-identical — a real orphaned duplicate file in `images/heroes/`.
- **REJECT · location**: the "Sunnyvale" hero is actually downtown San
  Francisco (Salesforce Tower, Bay Bridge, City Hall dome visible).
- **ADVISE · aesthetic**: `05-grand-central-market.jpg` is 500×375 — too low
  for full-bleed display. The site's own `IMAGE_CREDITS.md` admits this exact
  issue in its "Known follow-ups"; the tool found it independently.
- **ADVISE · unsure**: the Pismo pier and Disneyland castle heroes are marked
  UNSURE, not rejected — the tool declines to fake confidence when a photo is
  consistent-but-unconfirmable.

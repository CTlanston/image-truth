# image-truth report

**10 images** · ✅ 4 keep · ❌ 3 reject · ⚠️ 3 advise
Input: the 8 chapter heroes + homepage hero from `twelve-days-west/IMAGE_CREDITS.md`, plus a sweep of `images/heroes/` that includes the un-referenced orphan `potato-chip-rock.png` (paths sanitized for this example)

## ❌ REJECT — `twelve-days-west/images/heroes/00-bay-area-night-skyline.jpg`
> c3: This is clearly downtown San Francisco with the Salesforce Tower, Bay Bridge, and City Hall dome visible, not Sunnyvale.

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- ✗ **location** FAIL (97%): This is clearly downtown San Francisco with the Salesforce Tower, Bay Bridge, and City Hall dome visible, not Sunnyvale.
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

## ❌ REJECT — `twelve-days-west/images/heroes/07-potato-chip-rock.png`
> c1: duplicate of twelve-days-west/images/heroes/potato-chip-rock.png

- ✗ **duplicate** FAIL (100%): duplicate of twelve-days-west/images/heroes/potato-chip-rock.png
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Mount Woodson, California
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

## ❌ REJECT — `twelve-days-west/images/heroes/potato-chip-rock.png`
> c1: duplicate of twelve-days-west/images/heroes/07-potato-chip-rock.png

- ✗ **duplicate** FAIL (100%): duplicate of twelve-days-west/images/heroes/07-potato-chip-rock.png
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Mount Woodson, California
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

## ⚠️ ADVISE — `twelve-days-west/images/heroes/02-pismo-pier-sunset.jpg`
> c4 unsure: The image clearly shows a pier at sunset over the ocean, matching the general scene, but there is no visible evidence confirming this specific pier is at Pismo Beach.

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Pismo Beach, California
- ? **caption** UNSURE: The image clearly shows a pier at sunset over the ocean, matching the general scene, but there is no visible evidence confirming this specific pier is at Pismo Beach.
- · **aesthetic** PASS: no aesthetic concerns

## ⚠️ ADVISE — `twelve-days-west/images/heroes/04-disneyland-castle-fireworks.jpg`
> c3 unsure: The castle architecture with distinct blue conical turret roofs resembles Sleeping Beauty Castle but is also very similar to the Disneyland Paris castle, making it hard to confirm Anaheim specifically from this obscured, fireworks-heavy view.

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- ? **location** UNSURE: The castle architecture with distinct blue conical turret roofs resembles Sleeping Beauty Castle but is also very similar to the Disneyland Paris castle, making it hard to confirm Anaheim specifically from this obscured, fireworks-heavy view.
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

## ⚠️ ADVISE — `twelve-days-west/images/heroes/05-grand-central-market.jpg`
> c5: low resolution (500×375) for full-bleed display

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Downtown Los Angeles, California
- · **caption** PASS: caption matches image
- △ **aesthetic** WARN (70%): low resolution (500×375) for full-bleed display

## ✅ KEEP — `twelve-days-west/images/heroes/00-hero-bixby-bridge-sunset.jpg`
> all checks passed

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Big Sur, California
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

## ✅ KEEP — `twelve-days-west/images/heroes/01-apple-park-aerial.jpg`
> all checks passed

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Apple Park, Cupertino, California
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

## ✅ KEEP — `twelve-days-west/images/heroes/03-hollywood-sign.jpg`
> all checks passed

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Hollywood, Los Angeles, California
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

## ✅ KEEP — `twelve-days-west/images/heroes/06-el-prado-balboa-park.jpg`
> all checks passed

- · **duplicate** PASS: no duplicates found
- · **watermark** PASS: no watermark signals
- · **location** PASS: consistent with Balboa Park, San Diego, California
- · **caption** PASS: caption matches image
- · **aesthetic** PASS: no aesthetic concerns

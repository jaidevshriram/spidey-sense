# Desk test pre-runs (simulated scene camera + scripted gaze, real Haiku)

Run 2026-10-10 18:44 · `backend/desk_test/run_sim.py` · claude-haiku-5-5 · max_side 512 · up to 3 calls in flight, launched every 0.4 s · console sink · logs in `logs/desk_sim/`.
Verdicts are by decision time against the scripted timeline; the first 1.5 s of a segment is a transition (Haiku still judging older frames) and isn't counted as a false fire.

| Test | Trigger | Segment | Scene | Expect | Result | Fires | Onset→fire | Frame→fire | Gaze / trend | Haiku's reason |
|---|---|---|---|---|---|---|---|---|---|---|
| A | `big_cat_look` | 0-7 s | lion shown, gaze on the lion | fire | **HIT** | 2 | 1.56 s | 1556 ms | looked_at_s_ago=0.0 | A lion photo is displayed on the monitor; the wearer's gaze is on it. |
| A | `big_cat_look` | 7-14 s | dog shown, gaze on the dog | none | **OK (quiet)** | 0 |  |  |  |  |
| A | `big_cat_look` | 14-21 s | lion shown, gaze on the text | none | **OK (quiet)** | 0 |  |  |  | A lion is shown in the photo on the right side of the screen. |
| B | `lion_unnoticed` | 0-5 s | lion, gaze on the text | fire | **HIT** | 1 | 1.77 s | 1766 ms |  | A lion is shown in a photo on the monitor, away from the red dot. |
| B | `lion_unnoticed` | 5-9 s | gaze on the lion | none | **OK (quiet)** | 0 |  |  | looked_at_s_ago=0.0 | Lion clearly shown in the image; user's gaze is on it. |
| B | `lion_unnoticed` | 9-12 s | looked away < 3 s ago (notice memory) | none | **OK (quiet)** | 0 |  |  | looked_at_s_ago=2.1 | A lion is clearly shown in the photo on the right side of the screen. |
| B | `lion_unnoticed` | 12-16 s | memory expired, lion unseen again | fire | **HIT** | 2 (+1 in transition) | 0.09 s | 1763 ms | looked_at_s_ago=3.15 | Lion photograph displayed on the right side of the screen. |
| B | `lion_unnoticed` | 16-20 s | dog, gaze on the text | none | **OK (quiet)** | 0 |  |  |  |  |
| C | `person_to_water` | 0-5.1 s | far from the water (truth gap > 1 body-height); gaze anywhere (on the text) | none | **OK (quiet)** | 0 |  |  | gap 1.10 → 1.04 → 1.04 bh · same,farther,same · loom 0.67 | Person walks in grass, water edge remains far to the right. |
| C | `person_to_water` | 5.1-14 s | near / at the edge; gaze anywhere (on the text) | fire | **HIT** | 2 (+1 in transition) | 1.35 s | 1636 ms | gap 1.04 → 1.04 → 0.67 bh · closer,same,closer · loom 0.72 | Figure walks right across frames, moving closer to the water's edge. |
| C | `person_to_water` | 14-20 s | walking away (hysteresis) | none | **OK (quiet)** | 0 |  |  | gap 0.43 → 0.57 → 0.74 bh · closer,closer,same · loom 1.33 | Cartoon person walks left-right on grass; water edge remains a similar distance away. |
| D1 | `person_to_water_unwatched` | 0-5.1 s | far from the water (truth gap > 1 body-height); gaze on the text | none | **OK (quiet)** | 0 |  |  | gap 1.19 → 1.19 bh · closer,closer · loom 0.86 | Person moved right, closer to the water's edge over time. |
| D1 | `person_to_water_unwatched` | 5.1-14 s | near / at the edge; gaze on the text | fire | **HIT** | 1 | 2.56 s | 1651 ms | gap 0.94 → 0.74 → 0.59 bh · farther,farther,closer · loom 1.22 | Standing figure moved rightward, nearer the water's edge than before. |
| D1 | `person_to_water_unwatched` | 14-20 s | walking away (hysteresis) | none | **OK (quiet)** | 1 (+1 in transition) |  | 1487 ms | gap 0.33 → 0.33 → 0.06 bh · same,same,same · loom 2.36 | The illustrated person stands in place on grass, not moving toward the water. |
| D2 | `person_to_water_unwatched` | 0-20 s | gaze on the figure the whole time | none | **OK (quiet)** | 0 |  |  | gap 4.00 bh · farther · loom 1.0 | Person walks away from the water's edge across frames, now further from it. |

| Test | Haiku calls | Haiku call ms (median / p90) | Frame→result ms (median / p90) |
|---|---|---|---|
| A | 23 | 1455 / 2123 | 1547 / 2186 |
| B | 33 | 1437 / 1928 | 1472 / 1966 |
| C | 29 | 1625 / 2050 | 1652 / 2140 |
| D1 | 34 | 1477 / 1748 | 1556 / 1782 |
| D2 | 29 | 1549 / 2306 | 1576 / 2337 |

<!-- notes below are kept when the table is regenerated -->

## How the pre-runs are built

- Scene camera: `backend/desk_test/scene.py` renders the test page (distractor text left; picture on the right third
  for A/B, the synthetic lake clip on the right for C/D) on a monitor on a desk, 1600x1200 like the Neon.
  The picture panel is about 26% of the frame width, so Haiku gets **max_side 512** (at 384 the photo is ~100 px wide).
- Gaze: scripted per test (with ±0.6% jitter at 4 Hz), so the "truth" of where the wearer looked is exact.
  This tests the engine's gaze-vs-box logic and Haiku's boxes, not the Neon's calibration.
- Engine: the real `spidey.engine.run` loop (VLMDetector, TriggerMemory, rules, cooldowns), console sink, server off.
- Onset→fire: decision time minus the start of the segment where firing became correct (for B 12–16 s that is the
  moment the 3 s notice memory ran out; for C/D the moment the figure's true gap to the water fell below 1 body-height).
  Frame→fire: capture time of the frame Haiku judged → event (includes the Haiku call).

## Observations

- **A / B (gaze modes) behave as specified.** `looked_at_s_ago` is 0.0 while the gaze is on the picture and grows after it
  leaves; `lion_unnoticed` stays quiet for the 3 s notice memory after a look and fires again right after it expires.
- Haiku's bboxes are coarse, and when the red dot is on the animal the box tends to grow to include the dot
  (e.g. lion box [0.38, 0.33, 0.9, 0.78] vs the true ~[0.62, 0.3, 0.85, 0.55]). That biases toward "looked at"
  (safe for `unseen`, generous for `looking_at`). When the gaze is on the text the boxes are tight and exclude it.
- **Haiku's `ordinal` is not reliable on its own.** It often says "closer" while the figure walks away (the trigger text
  primes it). The engine therefore cross-checks it against the measured gap (a growing gap resets the "closer" streak)
  and requires proximity (gap ≤ 1 body-height at medium sensitivity) for both the gap trend and the ordinal path.
  Before that fix, C fired a false alert on the walk-away.
- Occasional bogus subject boxes (the figure placed at x≈0.28 for one tick) are treated as a new track by the IoU /
  centre-distance check. Two fires in C's 5–14 s window: the first on the approach (gap 1.04 → 0.67), a second at the edge
  after such a track reset re-armed it. Both are correct (the person is at the water), but a reset by one outlier box can
  re-arm early.
- Built-ins on photos: Haiku sometimes reports a photo of a tiger as `hazard: tiger, urgency 0.8–0.9`. `blindspot` is
  off in `triggers.desk.json`; leave it off for the desk test or pictures of animals will buzz it.
- Bug found and fixed in `VLMDetector._deliver`: with 3 calls in flight, a newer call's early fields arrive before an
  older call's full reply, and the stale-result check dropped the full reply (the only one with custom triggers).
  C/D got 6–9 results in 20 s; now 29–34.

## Latency cost of bbox and temporal (vlm/bench_v3.py, sequential calls)

Run 1 (quiet API, 18:22, n=2, 512 px): built-in only 1524 ms full reply; + 1 custom trigger with bbox 1508 ms;
1 temporal trigger with 3 images 1418 ms. Early fields 0.9–1.2 s in every mode. No measurable difference.

Run 2 (busy API, 18:36, n=6):

| max_side | mode | early fields ms (median) | full reply ms (median / p90) | input / output tokens |
|---|---|---|---|---|
| 384 | built-in only (no custom trigger) | 1186 | 1190 / 2807 | 172 / 69 |
| 384 | 1 custom trigger + bbox | 984 | 1797 / 3453 | 190 / 170 |
| 384 | 1 temporal trigger, 3 images | 1053 | 1764 / 2917 | 543 / 189 |
| 512 | built-in only (no custom trigger) | 2157 | 2233 / 5725 | 284 / 87 |
| 512 | 1 custom trigger + bbox | 1430 | 1465 / 3861 | 302 / 167 |
| 512 | 1 temporal trigger, 3 images | 1478 | 2283 / 3593 | 879 / 190 |

Takeaways: what costs time is the **output**. A custom trigger with bbox (or the temporal fields) adds ~100 output tokens,
worth roughly +0.3–0.6 s on the full reply when the API is busy and close to nothing when it is quiet. The three images
triple the input tokens (190 → 543 at 384 px, 302 → 879 at 512 px), but input barely moves latency. Early fields (built-in
triggers) are unaffected. In the engine runs, frame→result was 1.2–1.3 s median for A/B and 1.5–1.65 s for the temporal
tests C/D (3 calls in flight, 512 px), versus 0.92 s in the 18:12 benchmark (384 px, no boxes, quieter API).

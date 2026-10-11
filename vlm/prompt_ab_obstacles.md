# Obstacle trigger: model x prompt A/B (2026-10-10, 21:15)

Set: 56 hand-labelled frames from tonight's live walks (exact JPEGs Haiku saw, 512 px, gaze dot drawn).
25 positive (standing/walking, obstacle in the lower path within ~4 steps), 31 negative (seated at a desk, or a
clear path). Ambiguous/blurred frames excluded. Trigger text = live "Something in my way". Match = match && urgency >= 0.3.
`python3 tools/prompt_ab.py logs/prompt_eval/obstacle_set.json` (frames are not committed: people's faces).

| model | prompt | recall | false alarms | median ms (non-streamed) |
|---|---|---|---|---|
| claude-haiku-5-5 | v1 (live) | 24/25 (96%) | 6/31 (19%) | 1706 |
| claude-haiku-5-5 | v2 | 25/25 (100%) | 15/31 (48%) | 1692 |
| claude-sonnet-5-5 | v1 | 25/25 (100%) | 6/31 (19%) | 2374 |
| claude-sonnet-5-5 | v2 | 25/25 (100%) | 29/31 (94%) | 2207 |

Takeaways
- Perception is not the bottleneck: Haiku v1 already sees the obstacle in 96% of frames. Haiku v1's 6 false alarms
  are bags on the floor near chairs (borderline).
- v2 ("a missed match is worse than an extra tap") over-fires on obstacles (48% / 94%). Keep v1 as default; the
  recall wording belongs in individual trigger descriptions (as for the attack trigger), not the system prompt.
- Sonnet: same recall as Haiku, ~0.7 s slower per call. Not worth it for walking.
- The live misses come from the decision layer: latency vs walking speed, staleness / view-change vetoes (fixed per
  trigger), "noticed" counting glances (fixed), dropped out-of-order matches (fixed), and no notion of walking.

## Latency round (21:30): v1 vs v3 (triggers first) vs v4 (v1 order, 8-word reasons), Haiku, concurrency 3

| set | prompt | recall | false alarms | verdict median ms | p90 ms |
|---|---|---|---|---|---|
| obstacle | v1 | 23/25 (92%) | 7/31 (23%) | 1764 | 3438 |
| obstacle | v3 | 17/25 (68%) | 4/31 (13%) | 1482 | 4271 |
| obstacle | v4 | 24/25 (96%) | 10/31 (32%) | 2329 | 5311 |
| attack | v1 | 15/15 (100%) | 0/16 (0%) | 1586 | 3563 |
| attack | v3 | 15/15 (100%) | 0/16 (0%) | 2904 | 5676 |
| attack | v4 | 15/15 (100%) | 0/16 (0%) | 3173 | 5265 |

- Writing the scene reason first works as a short chain of thought: triggers-first (v3) loses 24 points of obstacle recall.
- Latency differences between prompts are swamped by the network: same prompt swings 1.5–3 s median, p90 3.4–5.7 s.
  The Mac's internet is the Companion phone's cellular hotspot: ping avg 117 ms, max 637 ms, 12.5% packet loss;
  a 5-token text-only Haiku request: TTFT median 550 ms, min 453, max 2210.
- Decision: keep v1 (default). Streamed delivery of the triggers block stays in (helps any prompt whose triggers close
  before the end). Concurrency 5 / every 0.25 s so overlapping calls hedge slow tails. Fix the uplink (below).

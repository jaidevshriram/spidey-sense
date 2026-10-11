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

# Heart-rate engine hook: status and record

**Already applied** (the vision agent landed engine v3 in `5449df7`; the coordinator then cleared direct edits):
- `backend/spidey/engine.py`: the heart hook. It was picked up by commit `d4a44f6` ("Radar off for now"),
  which committed the working-tree engine.py, so it is **already in HEAD**.
- `backend/spidey/rules.py`: `Rule.check_heart()` + `apply_heart()` (uncommitted, in the working tree).

`heart_engine.patch` is the record of exactly those heart changes (engine.py + rules.py, nothing else), as a unified
diff that applies cleanly to `5449df7` (`git apply --check` verified). You don't need to apply it.

The hook mirrors radar:

| radar | heart |
|---|---|
| cfg `radar: {enabled, url, ...}` | cfg `heart: {enabled, backend, ...}` → `spidey.heart.start_from_config` → `HeartRateSense(on_sample=ctl.on_heart_sample)` |
| `Controller.start_radar()` / `on_radar_target()` | `Controller.start_heart()` / `on_heart_sample()` (heart thread) |
| `R.apply_radar(rules, target, now)` → `Rule.check_radar` | `R.apply_heart(rules, sample, now)` → `Rule.check_heart` (state on the Rule: `_heart`, kept across `rules.upsert` like `_last`) |
| events via `Controller.emit`, `sensor: "radar"`, `radar: {...}` | events via `Controller.emit`, `sensor: "heart"`, `heart: {bpm, median_bpm, delta_bpm, above_s}` (+ `latency_ms` when real-time) |
| `radar` card | `oura` card from `spidey.heart.oura_card(ctl.heart_status())` (`off` when not enabled) |
| radar closed in `run()`'s `finally` | heart closed there too; heart fires are also written to the engine JSONL log |

The trigger math is `spidey.heart.heart_step` (pure, unit-tested); `HeartTriggers.step_rule` is shared by
`Rule.check_heart` (engine) and `HeartTriggers.evaluate` (CLI / tests). Heart rules still never match camera
detections (`Rule.matches` only takes vision sources) and are never sent to Haiku (`custom_triggers` filters by source).

Verified after the change: `tests.test_heart` (29), `tests.test_radar_client` (10), `./scripts/desk_test.sh check` (ALL PASS).

## Config

```yaml
heart:                       # Oura heart-rate sense (spidey/heart.py)
  enabled: true
  backend: replay            # replay | fake | open_oura
  file: tests/data/hr_spike.csv   # replay: relative to backend/ (or absolute)
  speed: 1                   # replay/fake speed-up (1 = real time)
  loop: true                 # replay: repeat the file
  # fake: rest_bpm: 70, noise_bpm: 2, spike_at_s: 150, spike_bpm: 105, spike_s: 20, spike_every_s: 300
  # open_oura: key/binary come from the repo-root .env (OURA_KEY_FILE or OURA_AUTH_KEY, OURA_BIN); chunk_s: 600
```

And a trigger (the app's "Heart racing" template sends exactly this):

```json
{"id": "heart_racing", "name": "Heart racing", "kind": "custom", "source": "heart_rate",
 "if": {"min_delta_bpm": 15, "min_delta_pct": 20, "sustain_s": 8}, "feel": "pulse", "cooldown_s": 60}
```

## Quick check (no Bluetooth, no port 8765)

```bash
cd backend
../.venv/bin/python -m unittest tests.test_heart -v            # 29 tests
../.venv/bin/python -m spidey.heart --backend replay --speed 20 # prints samples, one FIRE at ~162 s of data
```
For an engine run, use a config with `heart.enabled: true`, `backend: replay`, add the trigger from the app (or
`rules:` when seeding a fresh triggers file), and watch for `-> pulse [heart]` in the console.

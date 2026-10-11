# Plugging the radar into Spidey Sense (ready, **not applied**)

Everything here was built and tested in radar-lab only. `~/Desktop/GIT-Projects/spidey-sense` has not been modified.

## What you get
- `radar_detector.py`: `RadarDetector`, a Spidey detector that reads the radar feed (`ws://localhost:8090/ws`,
  see `PROTOCOL.md`) and returns Spidey `Detection`s. Also `CombinedDetector` (VLM/camera + radar, where a radar
  alert wins).
  - **hazard**: `"something approaching"` or `"too close"`, or `None`.
  - **where**: `left` / `ahead` / `right`, or `behind…` with a back mount.
  - **seen_by_user**: `False`.
  - **urgency**: from the radar (0.6–0.9 for approaching, 0.9–1.0 for too close).
  - **reason**: e.g. "radar: something approaching behind at 2.31 m, closing 1.0 m/s, contact in 1.8 s".
  - **raw**: `frame_ts` (radar frame time) is set, so Spidey's `e2e_ms` covers radar frame → cue.
- `detectors.patch`: adds `type: radar` and `type: combined` to `make_detector`, using 6 added lines. It passes
  `git apply --check` against spidey-sense as of 2026-10-10 18:26 PT, including the uncommitted work in
  `detectors.py`.
- `config.radar.yaml`: a radar-only run (no camera, no API key). `config.vlm_radar.yaml` runs Neon + Haiku + radar
  together.

The radar server does all the debouncing: confirmed tracks, dwell, hysteresis and hold. The adapter only maps
states and fails safe: no radar data for 0.5 s → `hazard=None`.

## Steps (about 2 minutes)
```bash
cd ~/Desktop/GIT-Projects/spidey-sense
cp ~/Downloads/radar-lab/spidey_adapter/radar_detector.py backend/spidey/radar.py
git apply ~/Downloads/radar-lab/spidey_adapter/detectors.patch
cp ~/Downloads/radar-lab/spidey_adapter/config.radar.yaml backend/configs/
cp ~/Downloads/radar-lab/spidey_adapter/config.vlm_radar.yaml backend/configs/      # optional
```
No new dependencies: the adapter needs `websockets` ≥ 12, and spidey-sense's venv has 17.2.
If the patch no longer applies because `make_detector` changed, add the two `if kind == ...` branches from
`detectors.patch` by hand.
Per spidey-sense's CLAUDE.md, also add a README line and a `docs/BUILD_LOG.md` entry in the same change.

## Run
```bash
# 1) radar (radar-lab venv). Pick one:
cd ~/Downloads/radar-lab
.venv/bin/python server.py                                   # live board
.venv/bin/python server.py --replay recordings/182745-desk_idle  # a recorded real session, looped
.venv/bin/python server.py --scenario approach               # synthetic: walk in, stop inside 0.5 m
.venv/bin/python server.py --scenario two_people             # synthetic: one walks past, one comes at you

# 2) Spidey (spidey-sense venv)
cd ~/Desktop/GIT-Projects/spidey-sense && source .venv/bin/activate
python3 backend/run.py --config backend/configs/config.radar.yaml --seconds 30        # console
python3 backend/run.py --config backend/configs/config.radar.yaml --sink datafeel-ble # Dot (Terminal.app)
```
Check the feed without Spidey: `python3 backend/spidey/radar.py` prints each Detection as it changes. Before
copying, run `.venv/bin/python spidey_adapter/radar_detector.py` from radar-lab.

## Config notes
- `mount: back` (radar worn facing behind you) reports `behind` / `behind-left` / `behind-right`, mirrored
  correctly. `mount: front` reports `left` / `ahead` / `right`. Leave it out to use the server's own `where`.
- `seen_by_user: false` lets unseen-type rules (blindspot) fire. Radar can't know gaze; with a back mount it's
  always true that you haven't seen it.
- `min_state: too_close` ignores approaching.
- Rules: `config.radar.yaml` uses its own `rules_file: triggers.radar.json`, so the app's `triggers.json`
  (blindspot etc.) doesn't double-fire on radar detections. To make radar rules show up in the app's Senses tab,
  add them to `triggers.json` instead (`when: ["too close"]` / `["something approaching"]`).
- **Combined detector**: keep `detect_every_s: 0`, otherwise the radar is only polled at that rate (the VLM
  throttles itself). A nested `primary.env_file` isn't path-resolved by `load_config`; use `ANTHROPIC_API_KEY`
  or an absolute path.
- Cooldowns: `radar_too_close` repeats every 2 s while someone stays in the zone. Raise `cooldown_s` for fewer
  repeats.

## What was tested (radar-lab, no repo changes)
- `tests/test_spidey_adapter.py`, against a copy of Spidey's `types.py` in `spidey_stub/`:
  - state mapping, back-mount mirroring, `min_state`
  - fail-safe within 0.5 s when the radar dies, and auto-reconnect
  - `CombinedDetector` precedence and trigger pass-through
  - end to end through a real `server.py --scenario approach` subprocess: pulse, then urgent
  - walk-by and flicker scenarios: zero buzzes
- Real-engine dry run in a **scratch copy** of spidey-sense's backend (patch + radar.py + config.radar.yaml,
  console sink, Spidey's own venv) against `server.py --scenario two_people`. It printed `pulse` on approach and
  `urgent` on too-close, every loop, with `where=behind` (back mount). Radar frame → Spidey cue (`e2e_ms`): median
  29 ms, p90 49 ms. Add about 117 ms for the BLE buzz.

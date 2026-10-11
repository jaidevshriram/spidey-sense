# Status (updated 2026-10-10 19:35 PT)

One-page current state. Details: `README.md`, history: `docs/BUILD_LOG.md`.

## Works now

| What | Commit | Notes |
|---|---|---|
| Dot over Bluetooth (El Jefe "DataFeelCE"), no cable | `4d4d635` | ~117 ms to buzz; ~60 ms link ping; health check every 2 s |
| Master intensity on hardware | `4d4d635` | weaker waveform variants; 0 = light only |
| App → Mac → BLE → Dot end to end | `45529e3` | tag `milestone-app-ble-dot` |
| iOS app: Log / Senses / Devices / Haptics / Settings, zero-config connect | `58bf908` | Bonjour + build-time Mac IP; simulator uses localhost |
| Bonjour re-announce on network change | `09146c6` | |
| Plain-English custom triggers (engine + app) | `8c95ab5`, `6d2828f` | judged in the single Haiku call; `backend/triggers.json` is the one source of truth |
| Neon via the Companion phone hotspot | `08fef40` | connects by gateway IP; 9–18 fps matched scene+gaze, 130 Hz gaze, ~110–150 ms capture→Mac |
| Haiku latency (single path) | `bbee782` | frame→cue 0.92 s median / 1.20 s p90 on test scenes |
| Radar client of radar-lab's :8090 server | `d4a05c2` | 10/10 tests with a fake server; radar-lab sim confirmed an approach |
| Slides (16, dark) | `1ad1ae6` | `docs/deck/index.html` |
| iOS v3 app: gaze-mode / over-time / radar trigger editor, 5 test templates, Log thumbnails + trend + radar, source/sink chips + pause, Radar & Neon cards, About | see git log | builds; verified live on fakes; radar/trend/Neon numbers shown from sample data until the engine side lands |
| Engine v3: gaze modes from real gaze history vs Haiku boxes, notice memory, temporal triggers (3 frames + relative trend memory), radar triggers, thumbnails, pause, status | this push | desk dry runs A–D all pass; `./scripts/desk_test.sh check` all pass |
| Radar on radar-lab protocol v2 (debounced state, mount-aware `where`, 0.5 s fail-safe) | this push | approach → 1 fire; two_people walk-by ignored; ~4 ms server→sense |
| App on Jaidev's iPhone ("Riptide") with a spider-web icon | this push | signed with his team; connects to the engine via Bonjour |
| Haptic Playground (web) | | `./scripts/playground.sh` |
| Per-call Haiku traces + viewer, app "Live decisions", staleness gate (2.5 s), decision-time gaze check, temporal "farther" veto, `treat_screens_as_real` for desk configs, Senses locked offline | uncommitted | `./scripts/traces.sh` → :8096 (+ `/live`); takes effect when the engine restarts |


## Latest: world-sim evaluation (19:33 PT): ALL GREEN
Real Haiku, world-style frames (no monitor, `tools/world_scene.py`), the real engine path incl. the gaze cache, 3 reps each,
parallel. `python3 tools/world_eval.py --reps 3` → `vlm/world_eval_results.md`; report `logs/reports/world_eval.html`.

| Scenario | Result |
|---|---|
| A big_cat_look: look at the lion → buzz; dog / tiger-while-reading → none | 3/3 every segment; **buzz 0.24 s after the gaze lands** (gaze cache) |
| B lion_unnoticed: looking → quiet; reading the sign → buzz once the 3 s memory ends; dog → none | 3/3 every segment (fires at exactly 9.0 s) |
| C person_to_water: far → none; near the edge → buzz; walking back → none | 3/3, frame→buzz ~1.5 s (Haiku path) |
| D1 unwatched + reading the sign → buzz near the edge | 3/3 |
| D2 unwatched + watching the person → never | 3/3 |
False alarms: **0**. Gaze-cache decision → buzz: ~3 ms after the gaze sample (+ BLE ~117 ms on hardware).

## In progress
- **Oura heart-rate sense** (stub + replay/fake backends + app template/card; live ring needs re-pairing at home, see `docs/oura.md` when it lands).
- **Live desk tests** with the real Neon + Dot (the glasses were unplugged while the phone charged).

## Paused
- **Radar** (per Jaidev, 19:00): off in `config.desk.yaml`; the code stays. Re-enable with `config.desk_radar.yaml`. The app shows Radar as "Off" (gray).

## Known issues
- Bluetooth must run from Terminal.app (the Claude app has no Bluetooth permission → exit 134). Only one process can hold the El Jefe link.
- Prompt caching is a no-op (prompt below Haiku's minimum cacheable length).
- The current radar-lab server can flag a straight walk-by at 2.5 m as approaching (its new tracker should fix this). Radar AZ_SIGN is not calibrated yet.
- The Companion phone battery drains fast while streaming over the hotspot.
- The Neon stream shows H.264 artifacts over the hotspot.
- Desk dry runs used scripted gaze, not real Neon gaze; Haiku boxes are coarse (bias toward "looked at" when the dot is on the object).
- Keep `blindspot` off during desk tests (Haiku rates tiger photos as hazards).
- A radar at the desk sees the person sitting at it as "too close" (idle recording fired twice at ~0.55 m); mount it facing away / behind.

## How to run each test (from the repo root, in Terminal.app)
```bash
source .venv/bin/activate
./scripts/ble_test.sh                                                     # Dot flashes green
./scripts/engine_ble.sh --config backend/configs/config.manual.yaml       # app drives the real Dot (nothing auto-fires)
./scripts/neon_check.sh                                                   # Neon via the phone hotspot: fps / delay / live view
python3 backend/run.py --config backend/configs/config.vlm.yaml --seconds 21   # Haiku on test scenes (console)
python3 backend/run.py --config backend/configs/config.neon.yaml          # real Neon + Haiku (console)
cd backend && ../.venv/bin/python -m unittest tests.test_radar_client -v  # radar client tests
cd backend && ../.venv/bin/python -m unittest tests.test_heart -v         # heart-rate sense (replay/fake, fake open_oura CLI)
```
Desk tests A–E: see README "Desk tests" (page `./scripts/desk_test.sh`, engine `./scripts/engine_ble.sh --config backend/configs/config.desk.yaml`).

## TBD numbers (fill in after the live desk tests)
- Glasses → buzz end to end with the latency changes (last measured before them: 1.6 s median / 1.9 s p90)
- Radar → buzz live (paused; estimate ≈ 0.15 s)
- Temporal trigger: approach → buzz latency
- Desk test hit/miss table (A–D + radar)

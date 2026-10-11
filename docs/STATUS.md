# Status (updated 2026-10-10 18:40 PT)

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
| Haptic Playground (web) | | `./scripts/playground.sh` |

## In progress
- **Vision v3:** gaze modes (looking at / unseen with noticed memory / either) from real gaze history vs Haiku's object box;
  temporal triggers (3 labelled frames per call + a relative trend memory: gap in body-heights, looming, closer/same/farther,
  touching); radar triggers in the engine; event thumbnails; pause; `status` message; desk test page + dry runs.

## Known issues
- Bluetooth must run from Terminal.app (the Claude app has no Bluetooth permission → exit 134). Only one process can hold the El Jefe link.
- Prompt caching is a no-op (prompt below Haiku's minimum cacheable length).
- The current radar-lab server can flag a straight walk-by at 2.5 m as approaching (its new tracker should fix this). Radar AZ_SIGN is not calibrated yet.
- The Companion phone battery drains fast while streaming over the hotspot.
- The Neon stream shows H.264 artifacts over the hotspot.

## How to run each test (from the repo root, in Terminal.app)
```bash
source .venv/bin/activate
./scripts/ble_test.sh                                                     # Dot flashes green
./scripts/engine_ble.sh --config backend/configs/config.manual.yaml       # app drives the real Dot (nothing auto-fires)
./scripts/neon_check.sh                                                   # Neon via the phone hotspot: fps / delay / live view
python3 backend/run.py --config backend/configs/config.vlm.yaml --seconds 21   # Haiku on test scenes (console)
python3 backend/run.py --config backend/configs/config.neon.yaml          # real Neon + Haiku (console)
cd backend && ../.venv/bin/python -m unittest tests.test_radar_client -v  # radar client tests
```
Desk tests (lion look / lion unnoticed / person near water ×2 / radar behind): steps come with Vision v3.

## TBD numbers (fill in after the live desk tests)
- Glasses → buzz end to end with the latency changes (last measured before them: 1.6 s median / 1.9 s p90)
- Radar → buzz latency
- Temporal trigger: approach → buzz latency
- Desk test hit/miss table (A–D + radar)

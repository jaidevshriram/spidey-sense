# Spidey Sense

A wearable "spidey sense": eye-tracking glasses see what's around you, an AI notices hazards **you haven't
looked at**, and a haptic Dot on your body buzzes / glows / warms to tell you, hands-free.

Hackathon project (Interface Hackathon, Oct 2026).

## Architecture

```
 WORN ON YOU                                   ON THE TABLE (MacBook)                       YOUR PHONE
 ───────────                                   ──────────────────────                       ──────────
 Pupil Labs Neon ──USB-C── Companion phone ─Wi-Fi─▶ source   (backend/spidey/sources.py)
   scene camera + gaze      (in your pocket)        │  Frame(image, gaze)
                                                    ▼
                                                  detector (backend/spidey/detectors.py)
                                                    │  Claude Haiku: {hazard, x, seen_by_user, urgency}
                                                    ▼
                                                  rules    (backend/spidey/rules.py, rules.json)
                                                    │  "unseen and urgency ≥ 0.6 → urgent"
                                                    ▼
 DataFeel Dot ──chain── El Jefe ◀──Bluetooth LE─── sink     (backend/spidey/sinks.py → haptics/dots)
   vibration · RGB light · thermal                  │
                                                    └──WebSocket :8765──▶ iOS app (ios/SpideySense)
                                                                          live "why it buzzed" log,
                                                                          senses, devices, settings
 Haptic Playground (backend/playground): web UI :8080 to try every Dot affordance by hand.
```

Every stage is swappable in `backend/configs/*.yaml` (`type: fake | neon`, `fake | vlm`, `console | datafeel | datafeel-ble`).

| Folder | What |
|---|---|
| `backend/` | Python package `spidey` (engine, rules, sources, detectors, sinks, WebSocket server), `run.py`, `configs/`, `playground/` |
| `haptics/` | DataFeel Dots: `spidey_haptics.py` (patterns, CLI), `dots/ble.py` (Bluetooth transport), `dots/fastfire.py` (2-write triggers), tests, `vendor/datafeel-python` (MIT SDK) |
| `neon/` | `neon_app`: Pupil Labs Neon gaze + scene sources (Capture over USB, or Companion phone over Wi-Fi) |
| `vlm/` | vision test scenes, latency report (detector code is `backend/spidey/detectors.py`) |
| `ios/` | SwiftUI app: Log / Senses / Devices / Settings |
| `docs/` | `protocol.md` (app ↔ engine WebSocket), `architecture.md`, `BUILD_LOG.md` (everything, timestamped) |
| `scripts/` | setup, API key, playground, Bluetooth test/latency |

## Quickstart

```bash
cd ~/Desktop/GIT-Projects/spidey-sense
./scripts/setup.sh               # uv venv on Python 3.12 + editable installs
source .venv/bin/activate
./scripts/set_api_key.sh         # Anthropic key → .env (hidden input, gitignored)

python3 backend/run.py --seconds 10                                           # fakes only, prints cues
python3 backend/run.py --config backend/configs/config.vlm.yaml --seconds 21  # Haiku on test scenes
```

**Bluetooth needs Terminal.app** (or iTerm). macOS only grants Bluetooth to real apps, and a process
started from inside other tools gets killed (exit 134). From Terminal:

```bash
./scripts/ble_test.sh                              # finds DataFeelCE, flashes the Dot green
./scripts/playground.sh                            # Haptic Playground → open the printed URL on your phone
python3 backend/run.py --sink datafeel-ble         # full pipeline buzzing the Dot
```

Only one program can hold the El Jefe Bluetooth link at a time. Our El Jefe is **DataFeelCE**; other kits at the
venue advertise as DataFeel1E/3A/7E/32/36, and the code only connects on an exact name match.

iOS: open `ios/SpideySense/SpideySense.xcodeproj` (or `cd ios/SpideySense && xcodegen generate`), pick your
signing team, run, and set the server URL to `ws://<mac-ip>:8765` in Settings.

## Measured so far

| | |
|---|---|
| Dot trigger over USB | 48 ms |
| Dot trigger over Bluetooth, stock SDK (6 writes) | 361 ms median |
| Dot trigger over Bluetooth, `dots/fastfire` (buzz starts after 2 writes) | **117 ms median**, 150 ms worst |
| Bluetooth connect + discover | 4–5 s |
| Claude Haiku 5.5 per frame (512 px) | 0.9–1.0 s median, 1.2–1.4 s p90 |

## Credits

- **DataFeel** devkit Python SDK (MIT), vendored in `haptics/vendor/datafeel-python`. The BLE protocol was identified
  from DataFeel's NuGet packages `Datafeel.NET.BLE` / `CallAndResponse.Transport.Ble`.
- **Pupil Labs** Neon, the Real-time API (`pupil-labs-realtime-api`) and Pupil Capture network API.
- **Anthropic** Python SDK, Claude Haiku 5.5.
- `bleak`, `minimalmodbus`, `pyserial`, `websockets`, FastAPI, OpenCV.
- Later: `open_oura` for Oura ring heart rate.

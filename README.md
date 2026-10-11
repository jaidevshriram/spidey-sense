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
| `vlm/` | vision test scenes, latency report, desk-test results (detector code is `backend/spidey/detectors.py`) |
| `backend/desk_test/` | desk test page (Commons images, see its `CREDITS.md`; synthetic lake scene), simulator harness, offline checks |
| `radar/` | radar sense docs + calibration; `adapter/` = radar-lab's adapter + protocol v2 (reference) |
| `ios/` | SwiftUI app: Log / Senses / Devices / Haptics / Settings (zero-config connect) |
| `docs/` | `STATUS.md` (current state, one page), `protocol.md` (app ↔ engine WebSocket), `architecture.md`, `BUILD_LOG.md` (everything, timestamped), `deck/` (slides) |
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
./scripts/engine_ble.sh --config backend/configs/config.manual.yaml   # real Dot, nothing auto-fires: drive it from the app
```

Only one program can hold the El Jefe Bluetooth link at a time. Our El Jefe is **DataFeelCE**; other kits at the
venue advertise as DataFeel1E/3A/7E/32/36, and the code only connects on an exact name match.

**Neon (wireless, via the Companion phone).** Glasses plug into the Companion phone; the phone streams scene
video + gaze over Wi-Fi to the Mac (Pupil Labs Real-time API, port 8080). Phone and Mac must be on the same Wi-Fi,
or (easiest, no venue Wi-Fi needed) **turn on the phone's hotspot and join it from the Mac**: the phone is then the
Mac's default gateway, and the check script + engine connect to it automatically (mDNS discovery does NOT work over
the Android hotspot; the gateway IP does). The Mac keeps internet through the hotspot (phone's mobile data) for Haiku.

```bash
./scripts/neon_check.sh                 # finds the phone, prints battery / fps / gaze rate / delay, opens a live view
./scripts/neon_check.sh 192.168.1.23    # if discovery is blocked on venue Wi-Fi: phone IP from the Companion app
./scripts/neon_check.sh --mock          # no device
python3 backend/run.py --config backend/configs/config.neon.yaml              # real Neon + Haiku, console output
python3 backend/run.py --config backend/configs/config.neon.yaml --sink datafeel-ble   # + buzz (Terminal.app)
```
Connection order: `source.host` / the IP argument → the Mac's default gateway (hotspot) → mDNS. On venue Wi-Fi where
discovery is blocked, set `source.host: <phone ip>` in `config.neon.yaml`. Frame timestamps are converted to the
Mac's clock with Pupil Labs Time Echo, so `e2e_ms` in the log is glasses capture → cue.

**Desk tests: attention-aware triggers** (from the app's Senses templates; turn on one at a time):

| Test | Trigger id | Expect |
|---|---|---|
| A: look at a big cat | `big_cat_look` (looking_at) | buzz while looking at the lion/tiger; nothing for the dog/car/house |
| B: lion you haven't noticed | `lion_unnoticed` (unseen, 3 s notice memory) | buzz while reading the text with the lion in view; silent once you look at it |
| C: person approaching water | `person_to_water` (temporal, any) | buzz as the person nears the water edge; nothing while far or walking away |
| D: same, only if you're not watching | `person_to_water_unwatched` (temporal, unseen) | buzz only while you look at the text |
| E: someone behind you (**radar off for now**) | `someone_behind` (radar, within 2 m, ≥ 0.3 m/s) | buzz when someone walks up; walk-bys ignored |

```bash
./scripts/desk_test.sh                     # desk test page on :8095 (lion etc. on the right, text on the left; ?test=C for the lake)
./scripts/engine_ble.sh --config backend/configs/config.desk.yaml          # Neon + Haiku + Dot (Terminal.app); radar OFF for now
# radar later: start ~/Downloads/radar-lab/server.py, then use backend/configs/config.desk_radar.yaml
./scripts/desk_test.sh prerun              # dry runs on simulated scenes + scripted gaze (real Haiku)
./scripts/desk_test.sh check               # offline engine checks
```
"Seen" comes from your real gaze history overlapping the object's box (from Haiku), not from Haiku's guess. Over-time
triggers keep only relative signals (the gap in body-heights, looming, closer/same/farther). See `docs/architecture.md`.

**iOS app: zero setup.** Open `ios/SpideySense/SpideySense.xcodeproj` (or `cd ios/SpideySense && xcodegen generate`)
and press Run. Phone and Mac on the same Wi-Fi is the only assumption:
- The app finds the engine by itself: a custom URL (if you set one) → **Bonjour** (the engine advertises
  `_spidey._tcp`) → the Mac's IP stamped into the build → `127.0.0.1` in the simulator. It re-searches after a drop.
- Pressing Run also starts the demo engine (fakes, console) if nothing is listening on 8765. Bluetooth runs still go via Terminal.
- On a real iPhone, pick your signing team once and allow Local Network on first launch.

Tabs: **Log** (why each buzz fired + link-drop system rows) · **Senses** (rules) · **Devices** (live El Jefe health) ·
**Haptics** (Link check: Phone→Mac / Mac→El Jefe / El Jefe→Dot with ms, Ping all; light, vibration, thermal with a live
chart; STOP ALL) · **Settings** (intensity, quiet, server URL source).

## Measured so far

| | |
|---|---|
| Dot trigger over USB | 48 ms |
| Dot trigger over Bluetooth, stock SDK (6 writes) | 361 ms median |
| Dot trigger over Bluetooth, `dots/fastfire` (buzz starts after 2 writes) | **117 ms median**, 150 ms worst |
| Bluetooth connect + discover | 4–5 s |
| Claude Haiku 5.5, frame → cue (384 px, streamed early fields, 2 calls in flight) | **0.92 s median, 1.20 s p90** (was 1.28 / 1.41 s); ~3 results/s |
| Neon over phone hotspot: scene+gaze | 1600x1200 @ 9–18 fps, gaze-only 130 Hz; capture→Mac 110–150 ms median (spikes to ~1 s p90) |
| Neon + Haiku through the engine (hotspot) | Haiku 1.1 s median, e2e (glasses capture → cue) 1.6 s median, 1.9 s p90 |
| El Jefe health check (BLE register read) | ~60 ms; app → Mac → Dot link ping ~60 ms |
| Master intensity 0.3, `urgent` via engine over BLE | 253 ms (weaker waveform variants) |
| Desk dry runs A–D (sim scene, scripted gaze, real Haiku, 512 px) | all pass; frame→fire 1.2–1.3 s (single image), 1.5–1.65 s (temporal, 3 images) |
| Radar frame → cue | ~29 ms median (radar-lab) + ~4 ms server→sense; + ~117 ms BLE buzz ≈ 0.15 s |

## Keeping this README current

Every change that adds or changes a feature, command, folder, config option or measurement updates this README
in the same commit, and gets a timestamped entry in `docs/BUILD_LOG.md` (see `CLAUDE.md`).

## Credits

- **DataFeel** devkit Python SDK (MIT), vendored in `haptics/vendor/datafeel-python`. The BLE protocol was identified
  from DataFeel's NuGet packages `Datafeel.NET.BLE` / `CallAndResponse.Transport.Ble`.
- **Pupil Labs** Neon, the Real-time API (`pupil-labs-realtime-api`) and Pupil Capture network API.
- **Anthropic** Python SDK, Claude Haiku 5.5.
- **radar-lab** (radar workstream): `server.py` (Infineon BGT60TR13C via the Infineon Radar SDK), tracking + debounced
  alerts, feed protocol v2 and the Spidey adapter (`radar/adapter/`).
- Desk test images from Wikimedia Commons; the lion is CC BY 2.0 by Kevin Pluck. Full list: `backend/desk_test/CREDITS.md`.
- `bleak`, `minimalmodbus`, `pyserial`, `websockets`, FastAPI, OpenCV.
- Later: `open_oura` for Oura ring heart rate.

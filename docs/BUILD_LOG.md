# Spidey Sense: build log

## How to reproduce from scratch

Repo: `~/Desktop/GIT-Projects/spidey-sense` (moved here from `~/Downloads/interface-hackathon` at 17:20).
Entries below that predate the move use the old flat paths (e.g. `spidey/…` is now `backend/spidey/…`,
`ble_transport.py` is now `haptics/dots/ble.py`, `*.command` is now `scripts/*.sh`).

```bash
# 0. Hardware: El Jefe + Dot(s) charged and on. Our El Jefe advertises over BLE as "DataFeelCE".
cd ~/Desktop/GIT-Projects/spidey-sense
./scripts/setup.sh && source .venv/bin/activate      # Python 3.12 venv, editable installs
./scripts/set_api_key.sh                             # Anthropic key → .env (gitignored)

python3 haptics/spidey_haptics.py info               # USB: expect Dot 3
python3 backend/run.py --seconds 10                  # fakes only
python3 backend/run.py --config backend/configs/config.vlm.yaml --seconds 21   # Haiku

# Bluetooth: run these from Terminal.app
./scripts/ble_test.sh                                # Dot flashes green
./scripts/ble_latency.sh                             # ~117 ms to buzz
./scripts/playground.sh                              # Haptic Playground on :8080
python3 backend/run.py --sink datafeel-ble

# iOS
cd ios/SpideySense && xcodegen generate && open SpideySense.xcodeproj
```

---

## Log (2026-10-10, times PT)

### 16:20 DataFeel SDK
- The DataFeel quickstart zip was AirDropped to `~/Downloads` and unzipped to `~/Downloads/datafeel`.
- The Python SDK is at `samples/Datafeel-devkit-samples/Python`: pyserial + minimalmodbus, Modbus RTU at 115200 baud.
  El Jefe's USB-serial chip is a CP210x (VID 10c4, PID ea60).
- `pip3 install .` was refused (PEP 668, externally managed Python), so I made a venv at
  `~/Downloads/datafeel/.venv` and ran `pip install .` there.

### 16:25 First smoke test: no dots
- `python3 spidey_haptics.py info` found El Jefe on `/dev/cu.usbserial-210`, but "No device at address 1..4".
- A direct probe showed `minimalmodbus.NoResponseError: No communication with the instrument (no answer)`.
  A scan of addresses 1–32 at 9600/19200/57600/115200 baud got nothing.
- **Cause:** the Dots weren't charged. El Jefe was on (blue LED = USB power), but the Dots have their own batteries.

### 16:31 Dots working (USB)
- After charging: one **DataFeel Dot V6** (hardware 6.3.1, firmware 1.2.0) at **address 3**; skin 23.7 °C, sink 23.6 °C.
- `spidey_haptics.py all` played urgent / pulse / ambient. `latency`: **48 ms median, 48 ms worst** (send time, 10 runs).

### 16:28–16:33 Project folder
- Created `~/Downloads/interface-hackathon` (copied `spidey_haptics.py`, own `.venv`, README).
- Added `demo.py`, a guided demo that plays each pattern twice with gaps. Jaidev confirmed feeling them.

### 16:36–16:44 Swappable pipeline `spidey/`
- `types.py` (Frame, Detection, HapticEvent), `sources.py` (FakeSource, NeonSource),
  `detectors.py` (FakeDetector, VLMDetector), `rules.py` ("blindspot": unseen and urgency ≥ 0.6 → urgent),
  `sinks.py` (Console, DataFeel, Phone stub), `engine.py`, `run.py`, `config.yaml`.
- `NeonSource` wraps the earlier work in `~/Desktop/GIT-Projects/neon-gaze-app/neon_app` (`open_source("capture"|"realtime")`).
  Gaze is in scene-camera pixels, origin top-left. That repo's debug status showed eye1 pupil confidence was poor (mean 0.20).
- Test `python3 run.py --seconds 10`: urgent fires at 3.0 s (unseen bike), no buzz once seen (6.0 s);
  143 log lines; pipeline overhead 0.3 ms median.
- Every analyzed frame is logged to `logs/events.jsonl` with timestamps per stage and `e2e_ms`.

### 16:46–16:49 Haiku VLM detector
- `VLMDetector` → `claude-haiku-5-5`: 512 px JPEG with the gaze drawn as a red dot, strict-JSON prompt,
  `thinking: disabled`, `effort: low`, max_tokens 150. (Haiku 5.5 rejects non-default `temperature`.)
- It runs on a background thread that always sends the newest frame (stale ones dropped), `min_interval_s: 0.5`.
- Synthetic test scenes are in `assets/` (`make_test_scenes.py`: street + car, empty hallway, kitchen knife),
  with a scripted gaze off/on each hazard in `config.vlm.yaml`.
- A plumbing test with a stubbed 0.8 s call: `seen` flips about 1 call after the gaze moves.
- **Blocked:** no API key found. The napkin-math repos have no `.env`; `ANTHROPIC_API_KEY` is unset.

### 16:50 Event server for the phone app
- `spidey/server.py`: WebSocket on `0.0.0.0:8765`. It sends `{"type":"hello","history":[...]}` on connect (last 50),
  then `{"type":"event", id, ts, pattern, dot, rule, reason, detection{hazard,where,seen_by_user,urgency}, latency_ms}`.
- It prints the LAN URL at startup (e.g. `ws://10.104.7.226:8765`). Verified with a Python websockets client.

### 16:50–16:54 Bluetooth: El Jefe without a cable
- DataFeel's C# BLE package (`Datafeel.NET.BLE` → `CallAndResponse.Transport.Ble`, from nuget.org) uses
  `BleNordicUartTransceiver`. The UUIDs, found in the DLL's strings:
  service `6e400001-b5a3-f393-e0a9-e50e24dcca9e`, write RX `6e400002-…`, notify TX `6e400003-…`.
  It carries the same Modbus RTU frames as USB.
- `spidey/ble_transport.py`: `BleSerial` is a pyserial look-alike over bleak, registered as
  `minimalmodbus._serialports["ble"]`, so the stock `datafeel.device.Dot("ble", 3)` works unchanged.
- **Gotcha:** a Bluetooth call from inside the Claude app is killed by macOS (exit 134, SIGABRT), because that
  app has no Bluetooth usage entry, so there is no prompt. **Run Bluetooth code from Terminal.app**
  (`open ble_test.command`), which gets a normal "Allow Bluetooth" prompt.
- **16:54: worked.** Connected to **DataFeelCE**, found the Dot at address 3, LED green (2 writes, 122 ms), skin 25.8 °C.
- **Gotcha:** 5 DataFeel controllers are in range at the venue (DataFeelCE, 1E, 7E, 36, 32). Ours is
  **DataFeelCE**. The connection now requires an exact name match (`ble_name: DataFeelCE` in config).

### 16:55 Python 3.12 venv
- `pupil-labs-realtime-api` (the wireless Neon path via the Companion phone) fails on Python 3.15 (pycares has no wheel).
- Rebuilt `.venv` with `uv venv --python 3.12` (old one kept at `.venv-py315`); everything imports.
- **Gotcha:** OpenCV and PyAV both bundle libavdevice, so you get an `objc ... implemented in both` warning at import.
  It's harmless so far.

### 16:57 Cable-free Bluetooth test (USB unplugged, El Jefe on battery)
- `open ble_latency.command` (Terminal.app). Connect + discover: 3.9–4.9 s. Found the Dot at address 3.
- Green flash 1 s: OK. One `urgent`: played.
- Stock SDK `spidey_haptics.fire` over BLE: **median 358–361 ms, worst 389–395 ms** per trigger (USB: 48 ms).
  Cause: 6 sequential Modbus writes (LED mode, LED colour, vibration mode, seq 0-3, seq 4-7, go), each about 60 ms over BLE.
- Bluetooth still crashes from inside the Claude app (exit 134) even after the permission change. Terminal.app works.

### 17:00 Faster triggers: `spidey/fastfire.py`
- Vibration mode is set once at connect. Per trigger: one write-multiple-registers to 1044–1047 (both sequence
  words, encoding checked byte-for-byte against the SDK's `write_long(LITTLE_SWAP)`), then `go`, then the LED
  mode + colour *after* the buzz has started.
- Over BLE: **until buzz median 117 ms, worst 150 ms**; everything sent median 237 ms.
  Readback of the sequence register matched (`0xe880c`).
- `DataFeelSink` (USB and BLE) now uses fastfire.

### 16:58 iOS event-log app `ios/SpideySense`
- SwiftUI, iOS 17+, generated with XcodeGen (`ios/SpideySense/project.yml`), no third-party dependencies.
- One screen: newest-first log of haptic events (time, pattern chip, hazard · direction, urgency, why).
  Tap a row for the full JSON. Status dot (live/connecting/offline). Gear → server URL (`@AppStorage`).
- URLSessionWebSocketTask, auto-reconnect (1 s → 5 s backoff); de-duplicated by id + ts; capped at 500.
  Info.plist has NSLocalNetworkUsageDescription and ATS NSAllowsLocalNetworking.
- `xcodebuild -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO`: **BUILD SUCCEEDED**.
- Simulator end-to-end with `ios/test_server.py 8799`: rows appear, it reconnects after a server restart with no duplicates.
  Screenshot: `ios/screenshot.png`. Not yet tested against `run.py` on 8765, or the tap-for-JSON sheet.
- To do (Jaidev): open in Xcode, choose a signing Team, run on the iPhone, allow Local Network, and set
  `ws://<mac-ip>:8765` (`ipconfig getifaddr en0`).

### 17:05 Haptic Playground `playground/`
- `playground/server.py` (FastAPI + uvicorn on 0.0.0.0:8080, prints the LAN URL) + `playground/index.html`
  (vanilla JS, Arial, white; canvas chart, no CDN). It holds ONE connection: `--transport ble` (default,
  DataFeelCE), `--transport usb`, or `--mock`. Launcher: `open playground/playground.command` (Terminal → BLE).
- Panels: connection (transport, device, dots, last-command ms), light (global colour, mode
  off/global/individual/breathe/track-thermal, 8 individual swatches), vibration sequence builder (8 slots:
  any VibrationWaveforms name or "Rest x s", plus presets urgent/pulse/ambient), manual vibration
  (50–300 Hz, intensity 0–1, start/stop), thermal (intensity −1..+1, or a target skin temp), a 2 Hz chart of skin/heatsink/
  thermal power, time-to-reach ±2 °C / ±4 °C / target, °C/s rate), STOP ALL, and a command log with ms per command.
- Thermal safety: target capped at 40 °C, auto-off after 30 s, off if skin > 41 °C, off if the page
  stops polling for 5 s (closed), `sendBeacon('/api/stop')` on pagehide, and STOP ALL on server exit (atexit).
- Tested in `--mock`: all endpoints OK, page renders, 30 s auto-off fired.
  Bugs fixed: the preset endpoint's kwarg clashed with `State.run(name=...)`; the mock lacked `registers.dev`.
- **Not yet run on hardware.** The thermal characterization is waiting on Jaidev's OK.

### 17:07 Haiku VLM test (real API)
- Jaidev supplied the key, stored only in `.env` (chmod 600, gitignored), loaded through `detector.env_file`. It's never printed.
- `python3 run.py --config config.vlm.yaml --seconds 22` (console sink, no buzz): 20 calls, **0 errors / 0 parse failures**.
- **Per-call latency: median 987 ms, p90 1404 ms, max 1415 ms.** Frame→cue e2e: median 1049 ms, p90 1419 ms.
- By scene:
  - Street + car: "red car ahead approaching", urgency 0.6–0.7. While unseen → `urgent` fired at 1.4 s.
    `seen_by_user` flipped to true at 4.9 s (gaze moved at 3.5 s; about 1.4 s lag = one call + throttle).
  - Hallway: hazard null every call ✓.
  - Kitchen: "knife on table", urgency 0.4–0.5, flips to seen at 19.2 s (gaze moved at 17.5 s) ✓.
    No buzz, because urgency < 0.6 and only `blindspot` was active.
- **Gotcha:** `rules.json` (written by the iOS-protocol work) overrides the `rules:` in config files, so the
  `heads_up` (ambient) rule from config.vlm.yaml wasn't loaded.
- Haiku tends to say `where: "ahead"` for things on the left of frame. The prompt may need direction from the frame position.

### 17:09 PT (2026-10-10) iOS app stub: Log / Senses / Devices / Settings + two-way protocol
- **App** (`ios/SpideySense`): TabView with 4 tabs. Log keeps the old live list + JSON detail; adds a sense filter and swipe
  Useful/Noisy (sends `feedback`). Senses lists rules as When/If/Feel with toggles, a "+" Sense Builder (source, condition,
  feel, dot, cooldown; presets Blindspot / Heartbeat shift / Machine alert), tap to edit, swipe to delete. Devices shows cards for the
  Mac hub, El Jefe "DataFeelCE", Neon and Oura (coming soon). Settings has the server URL (same `@AppStorage("serverURL")` key),
  master intensity, quiet mode, test urgent/pulse/ambient, and Emergency stop. Offline, it shows sample data under an orange "Sample" banner.
- New Swift files: `Protocol.swift` (Codable messages), `MockData.swift`, `SensesView.swift`, `DevicesView.swift`, `SettingsView.swift`.
  Changed: `ContentView.swift`, `EventStream.swift`, `Model.swift` (`quiet` flag, `isSample`). Demo launch args: `-tab senses|devices|settings`, `-openBuilder YES`.
- **Protocol**: `docs/protocol.md`. server->app: hello{history, rules, devices, settings}, event, rules.list, device.status (every 5 s),
  settings, ack, error. app->server: rules.upsert, rules.delete, feedback, command{test_pattern|stop_all|set_intensity|quiet}.
- **Python**: `spidey/server.py` now receives messages and routes them to handlers (`on(type, fn)`, run in a worker thread), with
  `hello_extra`, `broadcast` and `add_periodic`. `spidey/engine.py` has a `Controller`: rules are saved to `rules.json` (loaded at startup
  if present, else from config.yaml) and applied live. Feedback goes to `logs/feedback.jsonl`. Quiet mode logs and broadcasts events with
  `quiet: true` but doesn't play them. `spidey/rules.py` adds `id`, `enabled`, `source`, `to_dict`, `rule_from_dict`.
  sinks.py and the BLE files are untouched.
- **Stubs / TODO**: `set_intensity` is stored and broadcast but not applied to the dots. `stop_all` on the console sink is a printed stub
  (DataFeel: `sh.reset(dots)` via hasattr, but untested on hardware). Rules with source heart_rate / mac_metric / manual are synced but
  never fire. Oura is always coming_soon. El Jefe shows "connected" whenever a datafeel sink exists (no live health check). Dot 1 = left and 2 = right is a guess.
- **Verified**: `xcodegen generate`, then `xcodebuild ... -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build`:
  **BUILD SUCCEEDED** (no Swift warnings). Python handlers tested with a websockets client against `run.py` (console sink, temp config on port 8799):
  upsert, toggle off, delete, all 4 commands plus an unknown one, and feedback on a quiet event. rules.json and feedback.jsonl were
  correct, and a restart reloaded rules.json. `run.py --seconds 10` output is unchanged. Simulator (iPhone 16 Pro, iOS 18.6) is live
  against the engine: `ios/screenshots/{log,senses,devices,settings,sense-builder,log-offline-sample}.png`.
- **Gotchas**: an `ack` can arrive after the `rules.list` broadcast it triggers, so clients shouldn't rely on the order.
  An empty rules.json means "no rules" (not the defaults). Test rules.json and feedback.jsonl were removed afterwards, so the
  project starts from config.yaml as before. The app's swipe/toggle paths weren't driven by taps in the simulator, only the Python side of them.

### 17:20 Monorepo at `~/Desktop/GIT-Projects/spidey-sense` (Jaidev approved the location + private GitHub)
- Copied from `~/Downloads/interface-hackathon` (the old folder is kept as a backup for now) into
  `backend/` (spidey package, run.py, configs/, playground/), `haptics/` (spidey_haptics, demo, BLE tests,
  `dots/ble.py` + `dots/fastfire.py`, `vendor/datafeel-python` MIT SDK + VENDORED.md), `neon/` (copy of
  `neon_app` from neon-gaze-app, original untouched), `vlm/` (assets, latency_report.py), `docs/`, `ios/`, `scripts/`.
- Packaging: `pyproject.toml` in backend/haptics/neon; `scripts/setup.sh` = `uv venv --python 3.12` +
  `uv pip install -e haptics/vendor/datafeel-python -e haptics -e neon -e backend`. All imports OK.
- Config paths (`source.paths`, `detector.env_file`) now resolve relative to the config file; `.env` lives at the repo root.
- `config.vlm.yaml` uses `rules_file: rules.vlm.json`, so the app-edited `rules.json` no longer overrides its rules.
  `heads_up` is now max_urgency 0.59, so it doesn't double-fire with `blindspot` at 0.6.
- VLM direction: the prompt now asks for `x` (the hazard's horizontal centre, 0–1) and `where` is computed from it
  (<0.33 left, >0.67 right). Haiku's own "where" said "ahead" for a car on the left. After: car → "left".
- Verified from the new location: the fake pipeline (urgent at 3.0 s, silent once seen), the Haiku run
  (22 calls, **median 903 ms, p90 1203 ms**, 0 errors), and the iOS `xcodegen generate` + simulator build: **BUILD SUCCEEDED**.
- BLE test from the new location: **not yet**. DataFeelCE wasn't advertising because the playground Jaidev
  started at 17:09 (from the old folder) holds the BLE link. Rerun `./scripts/ble_test.sh` after closing it.

### 17:12 Haptic Playground confirmed on hardware
- Jaidev ran the playground (from the old folder, 17:09) over Bluetooth: "works and is great".

### 17:24 README upkeep
- Added `CLAUDE.md` (repo rule: update README.md + docs/BUILD_LOG.md in the same change) and a
  "Keeping this README current" section in the README, per Jaidev.

### 17:18 Bluetooth test from the new repo
- With the old playground stopped: `./scripts/ble_test.sh` (Terminal.app) connected to DataFeelCE (rssi −41),
  found the Dot at address 3, LED green in 121 ms, skin 29.4 °C. **The monorepo's BLE path is verified on hardware.**
- 5 DataFeel kits in range: CE (ours), 1E, 3A, 32, 36.
- The old `~/Downloads/interface-hackathon` is kept as a backup (Jaidev: don't delete).

### 17:43 iOS: Haptics tab (link check) + system rows in the Log + zero-config server URL
- **Haptics tab** (5th tab, `HapticsView.swift`): a native port of the Haptic Playground over the engine WebSocket (protocol: docs/protocol.md
  "Haptics tab + link check"). Link check card: Phone→Mac (link.ping flash:false every 2 s; app↔Mac = RTT − mac_ms; reconnect count),
  Mac→El Jefe (transport, device, rssi, last seen, register-read ms, server reason), El Jefe→Dot (answering vs expected addresses);
  green/amber (app↔Mac > 300 ms, BLE > 400 ms, stale > 6 s)/red. "Ping all" (flash:true) shows total and per-hop ms. Then
  Connection summary + master intensity, Light (ColorPicker, mode, 8 tappable LEDs), Vibration (presets, 8-slot sequence builder over
  the 123 SDK waveform names in `Waveforms.swift` + rests, manual Hz/intensity), Thermal (intensity, target stepper with the 40 °C cap,
  Swift Charts skin/sink over 120 s, session/auto-off countdown, time to ±2/±4 °C/target, rate, safety limits), a command log (last
  30 acks with ms), and a pinned red STOP ALL. Sends haptic.subscribe every 2 s while visible. Offline: sample telemetry + banner, controls disabled.
- **Log**: `system` messages from the engine and this phone's WebSocket drops/reconnects are shown as system rows (gray/amber/red + icon,
  no swipe feedback, hidden when a sense filter is on). Plus a "Can't reach the Spidey engine at <url>" banner after 3 s offline.
- **Devices**: the El Jefe card shows link ms/rssi/dots and the reason from `el_jefe.link`.
- **Protocol.swift**: LinkInfo, HapticTelemetry/ThermalSession/ThermalLimits, LinkPong, SystemMessage, a lenient AckMessage, and the
  app→server haptic.* / link.ping encoders. Every field is decoded leniently (missing/null/mistyped → nil).
- **Zero-config URL** (`ServerConfig.swift`): the stored `serverURL` is "" for automatic. Order: custom → Bonjour `_spidey._tcp`
  (~2 s, host+port from the service, IPv4) → iPhone: build-time `SpideyMacHost` → Simulator: ws://127.0.0.1:8765. Re-browses before
  each reconnect. A one-time migration resets the old placeholder (and 127.0.0.1 on a device) to automatic. Settings shows the URL +
  source + "Reset to automatic". project.yml: `NSBonjourServices`, `SpideyMacHost`, a "Stamp Mac IP" post-build script
  (`ios/SpideySense/scripts/stamp_mac_ip.sh`, PlistBuddy into the built Info.plist; `ENABLE_USER_SCRIPT_SANDBOXING: NO`), and a shared
  scheme whose Run pre-action (`scripts/start_engine.sh`) starts the demo engine if nothing listens on 8765 (logs/xcode_preaction.log).
- Commands: `cd ios/SpideySense && xcodegen generate && xcodebuild -project SpideySense.xcodeproj -scheme SpideySense -sdk iphonesimulator
  -destination 'id=<sim>' -derivedDataPath <scratch>/dd CODE_SIGNING_ALLOWED=NO build` → **BUILD SUCCEEDED**, no Swift warnings.
  Test engine: `.venv/bin/python backend/run.py --config <copy of config.yaml with port 8799, log logs/x.jsonl, rules_file rules.x.json>` (console sink).
- Verified in the iPhone 16 simulator against the 8799 engine: link check all green (RTT ~2–5 ms, mock read 0.0 ms, 1/1 Dot); Ping all
  per-hop ms; preset, sequence, LED (global + index 3), thermal target 55 → ack `capped`, "Engine capped the target at 40 °C", chart
  rising, session countdown, +2 °C in 8.8 s; STOP ALL acked; killing the engine → red "WebSocket dropped" row → re-browse → reconnect
  info rows; a 30 s thermal auto-off → amber engine `system` row. Zero-config: a clean install (uninstall + `defaults delete`) connected
  to 127.0.0.1:8765 with no settings; with the 8799 engine advertising, it resolved Bonjour → ws://10.104.7.226:8799 ("auto: found Mac on
  network (Spidey Sense on MacBook-Pro-2)"). The built Info.plist has `SpideyMacHost = 10.104.7.226`. Pre-action script: with 8765 busy →
  "engine already running on 8765, skip"; with stubs (port free) → started in the background, returned in 0 s, exit 0.
- Screenshots: ios/screenshots/haptics.png, haptics-thermal.png, log-system.png, log-system-engine.png, zero-config.png, settings-bonjour.png.
- Gotchas: `simctl uninstall` does **not** clear the app's defaults; use `simctl spawn <dev> defaults delete com.jaidev.spideysense`.
  Swipe momentum moves the layout after a screenshot, so wait ~2 s before tapping. Something else installed an intermediate build onto the
  test simulator at 17:32 (an install that targeted "booted"?), which killed the app once. Pre-actions only run from Xcode's Run, not xcodebuild.

### 17:20–17:35 Wireless-link testing backend + master intensity (engine)
- `backend/spidey/haptic_control.py` (`HapticControl`): one lock for the El Jefe link; master intensity via
  waveform-family swaps (e.g. BUZZ1_P100 → BUZZ3_P60 → BUZZ5_P20; DOUBLE_CLICK → SHORT_DOUBLE_CLICK_STRONG*; 0 = light only);
  LED/sequence/manual/preset/thermal with server-side safety (40 °C cap, 30 s auto-off, 41 °C cut-off, off when the
  app stops subscribing); 2 Hz `haptic.telemetry`; a health check (register read every 2 s); `link.ping`; `system` events on link loss.
- DataFeel sinks expose `control` (real dots); the console sink uses mock dots so the app's Haptics tab works without hardware.
- Engine handlers: `haptic.*`, `haptic.subscribe`, `link.ping`; El Jefe device status from the real health check.
  Protocol: `docs/protocol.md` → "Haptics tab + link check".
- **Hardware test over BLE** (`./scripts/engine_link_test.sh`, Terminal): El Jefe connected · dots [3] · read 62 ms ·
  RSSI −46 dBm; link.ping RTT ≈ 60 ms (BLE ≈ 55–90 ms); LED 128 ms; `urgent` at intensity 0.3 → 253 ms; stop_all OK.

### 17:30 Neon via the Companion phone is the default
- `NeonSource` default `mode: realtime`; `backend/configs/config.neon.yaml`; `neon/check_companion.py` +
  `scripts/neon_check.sh [ip] [--mock]` (discover/IP, battery, fps, gaze Hz, worn %, frame→Mac delay, live view).
  Tested: the mock view and the engine on the Neon source path in mock mode. Discovery reports "no Companion found" (no phone on the network yet).

### 17:35 Bonjour
- `spidey/server.py` advertises `_spidey._tcp` ("Spidey Sense on MacBook-Pro-2", port 8765) via python-zeroconf.
  `dns-sd -B _spidey._tcp local.` sees it.

### 17:50 Demo engine restarted with current code; Haptics tab checked in Jaidev's simulator
- Relaunched the app on the iPhone 16 Pro simulator with `-tab haptics`: Link check all green (Phone→Mac RTT 1.1 ms,
  mock El Jefe, Dot 3 answering) via the automatic URL, with no settings.

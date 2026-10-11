# Spidey Sense iOS log viewer

A SwiftUI app (iOS 17+, no dependencies) that connects to the engine's WebSocket (`ws://<mac-lan-ip>:8765`).

**Since the 2026-10-10 redesign** the tab bar is **Home · Spidey triggers · Settings · Debug** (light theme; see the repo
README "iOS app" section and `docs/screenshots/redesign/`). The technical screens below now live in the **Debug** tab
(Log = "Raw event log" / "Live decisions", Senses = "Triggers (technical editor)", Devices, Haptics, and Settings =
"Engine, connection & about"); the pause switch moved there too. The original description of those screens:

- **Log**: live list of every haptic cue and why it fired. Tap a row for the JSON, filter by sense, swipe right for *Useful* or left for *Noisy* (saved to `logs/feedback.jsonl` on the Mac). **System rows** (gray info / amber warning / red error, tagged "engine" or "this phone") show link lost/restored and thermal safety cut-offs from the engine, plus this phone's own WebSocket drops and reconnects. They're hidden when a sense filter is on and have no swipe actions. If the engine has been unreachable for more than 3 s, a red banner shows the URL and the command to start it.
- **Senses**: the engine's rules as "When / If / Feel", with on/off toggles. **+** opens the Sense Builder (presets: Blindspot, Heartbeat shift, Machine alert). Tap to edit, swipe to delete. Changes are saved to `rules.json` on the Mac and apply live.
- **Devices**: Mac hub, El Jefe, Neon and Oura status cards. The El Jefe card shows the live link check (register-read ms, rssi, answering dots, and the engine's reason when something is wrong).
- **Haptics**: a native port of the web Haptic Playground, driven through the engine's WebSocket. It's mainly a **wireless link tester**:
  - **Link check**: three hops with green/amber/red dots. Phone → Mac (WebSocket, round trip from a ping every 2 s, reconnect count), Mac → El Jefe (BLE/USB/mock, device, rssi, last seen, register-read ms), El Jefe → Dot (answering vs expected addresses). Amber = slow (app↔Mac > 300 ms, BLE > 400 ms) or stale (> 6 s). **Ping all** flashes the Dot green for 0.5 s and splits the time per hop.
  - Connection summary + master intensity; Light (color, mode, tap one of 8 LEDs); Vibration (urgent/pulse/ambient presets, an 8-slot sequence builder over all 123 DataFeel waveforms + rests, manual Hz/intensity); Thermal (intensity −1…+1, target °C capped at 40, live skin/sink chart for the last 120 s, session countdown, time to +2/+4 °C/target, rate); a command log of the last 30 acks with device ms. A red **STOP ALL** stays pinned at the bottom.
  - While the tab is visible it sends `haptic.subscribe` every 2 s; the engine turns thermal off ~5 s after it stops (tab closed, phone gone). Offline, it shows made-up temperatures under a "Sample data" banner, and the controls are disabled.
  - Launch arguments for testing: `-tab haptics` opens the tab, `-debugTargetMax 55` lets the stepper go above 40 so you can check the engine's cap.
- **Settings**: server URL, master intensity, quiet mode, test patterns, emergency stop.

While it isn't connected, the app shows sample data with an orange "Sample" banner. Protocol: `docs/protocol.md`. Screenshots: `ios/screenshots/`.

## Open in Xcode

```sh
cd ios/SpideySense
xcodegen generate          # only needed after editing project.yml (brew install xcodegen)
open SpideySense.xcodeproj
```

## Signing (one-time)

1. Select the **SpideySense** target, then go to **Signing & Capabilities**.
2. Keep "Automatically manage signing" on and choose your **Team**.
3. If the bundle id `com.jaidev.spideysense` is taken for your team, change it there (or in `project.yml`).

No team is committed in the project. Simulator builds don't need signing:

```sh
xcodebuild -project SpideySense.xcodeproj -scheme SpideySense \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
```

## Run on an iPhone

Plug in the phone (or pair it over Wi-Fi), select it as the run destination, and press Run. On first launch, iOS asks for **Local Network** permission. Allow it, or the app can't reach the Mac. If you denied it, turn it back on in Settings > Privacy & Security > Local Network.

## TestFlight

Set the destination to **Any iOS Device**, then choose Product > Archive. In the Organizer, use Distribute App > TestFlight & App Store. You need a paid Apple Developer account, and the app must exist in App Store Connect.

## Server URL: automatic (zero-config)

Hit Run in Xcode and the app connects with no typing. The Settings field stays **empty for automatic**. Resolution order:

1. **Custom URL**, if you typed one in Settings (Reset to automatic clears it).
2. **Bonjour**: the engine advertises `_spidey._tcp` ("Spidey Sense on <hostname>"). The app browses for ~2 s and uses the host and port from the first result. This works on the Simulator and on an iPhone (iPhone: allow Local Network; `NSBonjourServices` is in Info.plist).
3. **iPhone**: the Mac's LAN IP stamped into Info.plist (`SpideyMacHost`) at **build time** by the "Stamp Mac IP" build phase (`scripts/stamp_mac_ip.sh`: `ipconfig getifaddr en0 || en1`, falling back to 127.0.0.1), on port 8765.
4. **Simulator**: `ws://127.0.0.1:8765`.

After a disconnect, the app browses again before each reconnect attempt, because the Mac's IP may have changed. It keeps retrying with backoff from 1 s to 5 s, so you can start the engine after the app. Settings shows the URL in use and where it came from: "auto: found Mac on network (<name>)", "auto: Mac IP at build time 10.x.x.x", "auto: simulator", or "custom". On first launch, this version resets stale saved URLs to automatic (the old `ws://192.168.1.10:8765` placeholder, and 127.0.0.1 on a real phone).

**Engine auto-start:** the scheme's Run pre-action (`scripts/start_engine.sh`) starts the demo engine (fake source + console sink: no Bluetooth) in the background if nothing is listening on TCP 8765. It runs `.venv/bin/python backend/run.py` with output in `logs/engine_xcode.log`, and logs what it did to `logs/xcode_preaction.log`. It never blocks or fails the build. **Bluetooth runs (`--sink datafeel-ble`) still go through Terminal.app**, and only one process can hold the BLE link. So stop the auto-started engine first (`lsof -nP -iTCP:8765 -sTCP:LISTEN`, then kill that pid).

To use a custom URL, find the Mac's LAN IP with `ipconfig getifaddr en0`, enter `ws://<that-ip>:8765` in Settings, and tap Save & reconnect. The phone and the Mac must be on the same Wi-Fi network. The status dot shows the connection: green means live, orange means connecting, red means offline.

## Test without the engine

```sh
.venv/bin/pip install websockets
.venv/bin/python ios/test_server.py          # port 8765 (or pass a port, e.g. 8799)
```

The test server sends a hello with 2 sample events, then a new event every 3 s. In the Simulator, use `ws://127.0.0.1:<port>`. The test server doesn't implement the Haptics tab messages. For that tab, run the real engine with the console sink (mock dots): `python3 backend/run.py`.

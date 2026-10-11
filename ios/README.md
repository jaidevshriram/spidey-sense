# Spidey Sense iOS log viewer

A SwiftUI app (iOS 17+, no dependencies) that connects to the engine's WebSocket (`ws://<mac-lan-ip>:8765`). Four tabs:

- **Log**: live list of every haptic cue and why it fired. Tap a row for the JSON, filter by sense, swipe right for *Useful* or left for *Noisy* (saved to `logs/feedback.jsonl` on the Mac).
- **Senses**: the engine's rules as "When / If / Feel", with on/off toggles. **+** opens the Sense Builder (presets: Blindspot, Heartbeat shift, Machine alert). Tap to edit, swipe to delete. Changes are saved to `rules.json` on the Mac and apply live.
- **Devices**: Mac hub, El Jefe, Neon and Oura status cards.
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

## Set the server URL

1. Find the Mac's LAN IP: `ipconfig getifaddr en0`.
2. In the app, open the Settings tab and enter `ws://<that-ip>:8765`, then tap Save & reconnect. The app reconnects immediately, and the URL is remembered.

The phone and the Mac must be on the same Wi-Fi network. The status dot shows the connection: green means live, orange means connecting, red means offline. The app retries automatically, backing off from 1 s to 5 s.

## Test without the engine

```sh
.venv/bin/pip install websockets
.venv/bin/python ios/test_server.py          # port 8765 (or pass a port, e.g. 8799)
```

The test server sends a hello with 2 sample events, then a new event every 3 s. In the Simulator, use `ws://127.0.0.1:<port>`.

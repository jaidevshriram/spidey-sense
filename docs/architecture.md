# Architecture

## Pipeline (backend/spidey)

`engine.run(cfg)` loops: `source.frames()` → `detector.analyze(frame)` → `rules.apply(...)` → `sink.play(...)`,
logging every analyzed frame to `backend/logs/*.jsonl` (timestamps per stage, `e2e_ms`) and broadcasting each
haptic event on the WebSocket server.

| Stage | Interface | Implementations |
|---|---|---|
| Source | `frames() -> Iterator[Frame(ts, image BGR, gaze_xy, gaze_norm)]` | `FakeSource` (synthetic circle / stills playlist / video, scripted gaze), `NeonSource` (`neon_app`: `capture` USB or `realtime` Companion phone) |
| Detector | `analyze(frame) -> Detection \| None` | `FakeDetector` (scripted timeline), `VLMDetector` (Claude Haiku on a background thread; newest frame only; `None` = no new result) |
| Rules | `Rule(when, if_, feel, cooldown_s, dot, enabled)` | from `rules.json` (edited live by the app) or the config's `rules:` |
| Sink | `play(pattern, dot)` | `ConsoleSink`, `DataFeelSink(transport="serial"\|"ble")`, `PhoneSink` (stub) |

Gaze coordinates: scene-camera pixels (or 0..1 when `gaze_norm`), origin top-left.

## Haptics (haptics/)

- El Jefe speaks **Modbus RTU** to the Dots (address 3 is ours) over USB serial (CP210x, 115200) or over
  **Bluetooth LE, Nordic UART Service** (`6e400001` service, write `6e400002`, notify `6e400003`).
- `dots/ble.py: BleSerial` is a pyserial look-alike over bleak, registered as `minimalmodbus._serialports["ble"]`,
  so DataFeel's SDK `Dot("ble", 3)` works unchanged.
- `dots/fastfire.py`: the vibration mode is set once; a trigger is one write-multiple-registers (sequence words
  1044–1047), then `go`, then the LED. That's about 117 ms to buzz over BLE.
- Patterns (`spidey_haptics.PATTERNS`): **urgent** (triple click + strong buzz, red), **pulse** (heartbeat
  double clicks, pink), **ambient** (soft hum, blue).

## Phone app link

WebSocket `ws://<mac-ip>:8765`. See `protocol.md`: `hello{history, rules, devices, settings}`, `event`,
`rules.*`, `device.status`, `feedback`, `command{test_pattern|stop_all|set_intensity|quiet}`.

## Demo topology (wireless, hands-free)

Worn: Neon glasses + Companion phone (pocket) + El Jefe + Dot. On a table within about 10 m: the MacBook (lid open,
plugged in, `caffeinate -dis`) running `backend/run.py` from Terminal. Your phone shows the live log.
Range option for later: the iPhone app talks to El Jefe over CoreBluetooth directly (same Nordic UART protocol).

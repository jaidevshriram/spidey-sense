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

## Temporal + gaze memory layer (backend/spidey/rules.py: `TriggerMemory`)

Haiku answers one question per tick about one moment. Anything that depends on *time* (has the wearer looked at it?
is that person getting closer to the water?) lives in the engine, in `TriggerMemory`, which sits between the
detector and the rules: `memory.add_frame(frame)` for every frame, `memory.annotate(detection, rules, now)` for every full
Haiku result, then `rules.apply`. It only remembers **relative, measurable** quantities: gaze points and boxes as fractions
of the frame, gaps in subject body-heights, box-area ratios, and Haiku's ordinal judgements. It holds no metric distances
from the camera (radar is the only sensor that reports metres).

**Gaze history.** About 5 s of gaze samples `(ts, x, y)`, normalized by the frame size. For each custom camera trigger,
Haiku returns a `bbox` of the matching thing in the frame it saw. The engine checks the gaze samples since that trigger
started matching (a 4% margin around the box) and keeps, per trigger, the last time the gaze was inside it
(`noticed_ts`). It writes `looked_at_s_ago = now - noticed_ts` (or null) into the trigger result. Samples after the judged
frame count too, so head motion during the ~1 s Haiku call adds some error, which is acceptable at a desk.
- `looking_at`: fire only if `looked_at_s_ago <= 0.6`.
- `unseen`: fire only if never looked, or `looked_at_s_ago > notice_memory_s` (default 3). The "noticed" memory therefore
  lasts a few seconds after the gaze leaves.
- `any`: gaze ignored.
- The memory resets when the trigger hasn't matched for more than 2 s (the thing is gone).
- Built-in triggers (and custom ones without a box) still use Haiku's red-dot `seen_by_user`.

**Temporal triggers.** The detector keeps a ring buffer of downscaled frames. When any enabled custom trigger has
`temporal: true`, each call sends 3 images labelled `t-1.4s`, `t-0.7s`, `now`, still as one request. For each temporal
trigger, Haiku returns `subject_bbox`, `target_bbox` (newest frame), `ordinal` (closer/same/farther vs the earlier frames)
and `touching`. Per trigger and per tick, the engine computes:
- `gap`: box-to-box distance in pixels ÷ the subject box height ("body-heights"), smoothed as the median of 3 ticks.
- `loom`: the subject box area ÷ the previous tick's (> 1 = coming toward the wearer).
- The ordinal history.

The track is kept by box identity (IoU ≥ 0.2, or the centre within one subject height). A big jump, or 10 s without the
subject, starts a new track (re-armed). A gap of more than 4 s restarts the trend but keeps the arm state.

It fires (if armed) when:
- `touching`, or
- near the target (gap ≤ 0.5 / 1.0 / 2.0 body-heights for low / medium / high sensitivity) **and** either:
  - the gap shrank over the last `trend.ticks` ticks (no step growing by more than ε, net shrink ≥ ε·(ticks−1), with
    ε = 0.15 / 0.07 / 0.03 body-heights), or
  - Haiku said "closer" `ticks` times in a row. A tick whose measured gap grew resets this streak, because Haiku's
    ordinal alone said "closer" while the person walked away.

After firing it disarms (hysteresis) until the gap grows back to ≥ 1.3 × the firing gap + 0.2. The trigger's cooldown
applies on top. Events carry `trend: {gap: [...last 3], ordinal: [...], touching, loom}`.

**Radar** bypasses the memory. `RadarSense` targets go straight to `Rule.check_radar`: confirmed approaching/too_close
targets within `max_distance_m` and at least `min_speed_mps` fire. They are unseen by definition. They go through the same
`Controller.emit` as camera events: quiet and pause are respected, events are broadcast, and they are logged.

**Pause** (`command pause {on}`): detection, memory, logging and broadcasting keep running; `Controller.play` sends
nothing to the sink, and events are flagged `paused: true`.

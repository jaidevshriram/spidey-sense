# Spidey Sense app <-> engine protocol

One WebSocket per phone, served by the Mac engine (`spidey/server.py`, default `ws://<mac-ip>:8765`).
Every frame is one JSON object with a `type`. Swift types: `ios/SpideySense/SpideySense/Protocol.swift`.
Python handlers: `Controller` in `spidey/engine.py`.

## Server -> app

| type | fields | when |
|---|---|---|
| `hello` | `history: [event]` (last 50), `rules: [rule]`, `devices: [device]`, `settings: {quiet, intensity}` | on connect |
| `event` | `id, ts, pattern, dot, rule, reason, detection{hazard, where, seen_by_user, urgency}, latency_ms`, optional `quiet: true` | every haptic trigger |
| `rules.list` | `rules: [rule]` | after any upsert/delete (broadcast to all apps) |
| `device.status` | `devices: [device]` | every ~5 s |
| `settings` | `quiet, intensity` | after a `quiet` / `set_intensity` command |
| `ack` | `for` (message type), `ok`, plus `id` / `name` / `event_id` / `detail` / `error` | reply to the sender only |
| `error` | `error`, optional `for` | bad JSON, unknown type, handler crash |

`quiet: true` on an event means it was logged and broadcast, but not played on the dots.
Older engines send only `hello{history}` + `event`; the app treats missing rules/devices as sample data.

## App -> server

| type | fields | effect |
|---|---|---|
| `rules.upsert` | `rule` | add, or replace the rule with the same `id`; saved to `rules.json`; rule list rebuilt live |
| `rules.delete` | `id` | remove; saved; rebuilt |
| `feedback` | `event_id, ts, verdict: "useful" \| "noisy"` | appended to `logs/feedback.jsonl` (with the matching event if still in history) |
| `command` | `name, args` | see below |

Commands:

| name | args | effect |
|---|---|---|
| `test_pattern` | `{pattern: "urgent" \| "pulse" \| "ambient", dot?: int}` | `sink.play(pattern, dot)`; ignores quiet mode |
| `stop_all` | `{}` | `sink.stop()` if it exists, else DataFeel `reset(dots)` (stops vibration + LEDs, keeps the link), else a stub (console sink) |
| `set_intensity` | `{value: 0..1}` | stored + broadcast as `settings`. **TODO:** not applied to hardware yet |
| `quiet` | `{on: bool}` | when on, rules still fire, events are logged + broadcast with `quiet: true`, but nothing is played |

## Rule ("sense")

Maps 1:1 onto `spidey.rules.Rule` (`if` <-> `if_`):

```jsonc
{
  "id": "blindspot",            // stable key; defaults to a slug of name
  "name": "blindspot",
  "enabled": true,              // disabled rules are skipped
  "source": "neon",             // neon | heart_rate | mac_metric | manual
  "when": "any",                // or ["bike", "car"] (hazard kinds)
  "if": {
    "unseen": true, "seen": false, "min_urgency": 0.6, "max_urgency": 1.0,   // applied today (neon)
    "hr_band_bpm": 15, "hr_window_s": 120,     // heart_rate: outside median ± y   (TODO: no HR source yet)
    "metric": "cpu_temp_c", "value_gt": 90     // mac_metric: value > threshold    (TODO: no metric source yet)
  },
  "feel": "urgent",             // key in spidey_haptics.PATTERNS: urgent | pulse | ambient (custom = any other key)
  "dot": null,                  // 1-based; null = all dots. App uses 1 = left, 2 = right (TODO: confirm physical layout)
  "cooldown_s": 3
}
```

Only `source: "neon"` (vision detections) reaches the engine loop. Rules with other sources are stored,
synced and shown, but never match (stub in `Rule.matches`).

## Device

```json
{"id": "el_jefe", "name": "DataFeelCE", "kind": "haptics", "status": "connected",
 "detail": "datafeel-ble via the Mac (ble)", "transport": "ble", "sink": "datafeel-ble", "last_seen": 1791676969.8}
```

`status`: `connected | disconnected | simulated | coming_soon`. Ids sent today: `mac_hub`, `el_jefe` (the sink;
`simulated` for the console sink; "connected" is assumed once the sink was created), `neon` (the source; `simulated`
for `fake`, `disconnected` if no frame for 3 s), `oura` (always `coming_soon`). `last_seen` is unix seconds or null.

## Persistence

- `rules.json` (project root): `{"rules": [rule, ...]}`. Loaded at startup if present, else `config.yaml` `rules:`.
  Delete it to go back to config.yaml. Override the path with `rules_file:` in the config.
- `logs/feedback.jsonl`: one line per verdict. Override with `feedback_log:`.

## Haptics tab + link check (added 17:35)

The engine owns the single El Jefe link (`sink.control`, a `HapticControl`). With the console sink, these
messages drive **mock dots**, so the tab works in the simulator. Don't run the web playground at the same time
as an engine using a DataFeel sink: only one process can hold the BLE link.

### App -> server (each gets `{"type":"ack","for":<type>,"ok":bool,"ms":<device ms>,"error"?:...}`)

| type | fields | notes |
|---|---|---|
| `haptic.subscribe` | `on: bool` | send every ~2 s while the tab is visible; the server streams telemetry at 2 Hz. If the app stops sending for 5 s, thermal turns off |
| `haptic.led` | `mode: off\|global\|individual\|breathe\|track_thermal`, `rgb: [r,g,b]`, `index?: 0-7`, `leds?: [[r,g,b] x8]` | `index` sets one LED (individual mode) |
| `haptic.vibe.sequence` | `steps: [waveform name \| rest seconds (0-1.27)]` (max 8) | names = DataFeel `VibrationWaveforms` |
| `haptic.vibe.manual` | `hz: 50-300`, `intensity: 0-1`, `on: bool` | scaled by master intensity |
| `haptic.preset` | `name: urgent\|pulse\|ambient` | scaled by master intensity |
| `haptic.thermal` | `on: bool`, `intensity?: -1..1` **or** `target_c?: number` | target capped at 40 °C (`capped: true` in ack); auto-off after 30 s; off if skin > 41 °C |
| `haptic.stop_all` | – | vibration, thermal, LEDs off |
| `link.ping` | `id`, `t_app` (unix s), `flash: bool` | reply `link.pong` (below); `flash` = Dot green 0.5 s |
| `command` `set_intensity` | `args.value: 0-1` | master intensity, **applied to hardware**: waveforms swap to weaker variants (e.g. BUZZ1_P100 → BUZZ3_P60); 0 = light only |

### Server -> app

- `haptic.telemetry` (2 Hz while subscribed): `{ts, skin_c, sink_c, thermal_power, last_cmd_ms, intensity,
  thermal: null | {mode, value, base_c, reached: {"+2C": s, "+4C": s, "target": s}, elapsed_s, remaining_s, rate_c_per_s},
  limits: {target_cap_c, skin_cutoff_c, thermal_max_s}}`
- `link.pong`: `{id, t_app, mac_ms, ble_ms, flash_ms, link}`. App RTT = now − t_app. Per hop: app↔Mac = RTT − mac_ms;
  Mac↔El Jefe↔Dot = ble_ms (a register read on every dot).
- `link` object (also in `device.status` → `el_jefe.link`): `{transport: ble|usb|mock, device, rssi (dBm at connect, or null),
  ok, ms (register-read round trip), last_seen, dots: [answering addresses], addresses: [expected], link_up, reason}`.
  `reason` is a human sentence, e.g. "BLE: link to DataFeelCE is down. Is El Jefe on and in range?"
- `system`: `{ts, level: info|warning|error, text}` for link lost/restored, thermal safety cut-offs. Show in the Log as system rows.
- The El Jefe card in `device.status` is now real: `status` connected/disconnected from a register read every 2 s.

## Triggers: plain-English custom triggers (added 17:58)

**One source of truth:** `backend/triggers.json`, owned by the engine. It's seeded once from the config's `rules:`
(or the built-in defaults) if missing; after that, config `rules:` are ignored, and the engine prints and echoes the active
set (`hello.rules`, `rules.list` after every change). The app's "senses" are these triggers.

Trigger (= rule) JSON, extending the Rule section above:
```jsonc
{
  "id": "knife_near", "name": "Knife nearby", "enabled": true,
  "kind": "custom",             // "builtin" (hazard/urgency logic) | "custom" (judged by Haiku from `describe`)
  "describe": "warn me if someone holding a knife is near and I haven't looked at them",   // custom only
  "source": "neon",
  "when": "any", "if": {"unseen": true, "min_urgency": 0.5},     // unseen = "only if I haven't looked at it"
  "feel": "urgent",             // urgent | pulse | ambient | custom pattern key
  "intensity": 0.8,             // optional per-trigger vibration strength 0..1 (multiplies master intensity)
  "rgb": [255, 0, 0],           // optional LED colour override
  "dot": null, "cooldown_s": 3
}
```
Custom triggers are evaluated by the **same single Haiku call** that detects hazards: enabled custom
triggers are appended to the prompt, and Haiku returns per trigger id
`{"match": bool, "urgency": 0-1, "x": 0-1, "seen_by_user": bool, "reason": "one line"}`. A custom trigger fires when
`match` and `urgency >= if.min_urgency` and (if `if.unseen`) not seen, subject to cooldown.

Event additions: `trigger` (the trigger id that fired), `trigger_name`, `why` (Haiku's one-line reason; for
built-ins the overall scene reason). `rule`/`reason` are kept for compatibility.

App -> server additions:
- `rules.upsert` / `rules.delete` (unchanged), and the toggle = upsert with `enabled` flipped. The engine persists to triggers.json and broadcasts `rules.list`.
- `rules.reset` `{}`: restore the default triggers (blindspot + heads_up); replies `rules.list`.

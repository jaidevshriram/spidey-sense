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

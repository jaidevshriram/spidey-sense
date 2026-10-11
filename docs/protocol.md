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

## v3: gaze modes, temporal triggers, radar, thumbnails, pause (added 18:15)

Trigger additions:
```jsonc
{
  "gaze_mode": "unseen",        // "unseen" (fire only if NOT looked at recently) | "looking_at" (fire only while looking at it) | "any"
                                //   back-compat: if missing, if.unseen=true => "unseen", else "any"
  "notice_memory_s": 3,         // unseen: once looked at, stay quiet this long after gaze leaves it
  "temporal": false,            // true: Haiku gets the last 3 frames (~0.7 s apart) tiled into one image, oldest left -> newest right,
                                //   so the description can refer to change over time ("getting closer to the water")
  "source": "neon",             // "neon" (camera + gaze) | "radar"
  "if": {"min_urgency": 0.5,
         "max_distance_m": 2.0, "min_speed_mps": 0.3}   // radar triggers only
}
```
**"Seen" comes from real gaze, not the red dot.** For custom camera triggers Haiku also returns the object's
`bbox: [x0, y0, x1, y1]` (0..1 of the newest frame). The engine keeps ~5 s of gaze history and computes
`looked_at_s_ago`: when the gaze point last fell inside the bbox (with ~4% margin). `looking_at` = within 0.6 s;
`unseen` = not within `notice_memory_s`. Built-in triggers keep using Haiku's seen_by_user.

Radar triggers come from the radar server (`ws://localhost:8090`, owned by `~/Downloads/radar-lab/server.py`):
a confirmed "approaching" target within `max_distance_m` and faster than `min_speed_mps` fires; radar targets count as
unseen by definition.

Event additions:
- `sensor`: "camera" | "radar"
- `thumb`: base64 JPEG (~240 px wide) of the frame that fired, with the gaze dot and the bbox drawn (camera only)
- `radar`: `{distance_m, speed_mps, angle_deg, side: "left"|"right"|"center"}` (radar only)
- `gaze_mode`, `looked_at_s_ago` (camera custom triggers)

Server -> app: `status` (on connect, every 2 s, and on change):
```jsonc
{"type": "status", "paused": false,
 "source": {"type": "neon"|"fake", "live": true, "fps": 16.2, "gaze_hz": 130, "worn": true, "battery": 13, "delay_ms": 140},
 "radar": {"connected": true, "url": "ws://localhost:8090", "mode": "board"|"sim", "last": {...}, "rate_hz": 10} | null,
 "about": {"config": "config.neon.yaml", "model": "claude-haiku-5-5", "commit": "bbee782", "sink": "datafeel-ble"}}
```
`device.status` gains a `radar` card; the `neon` card gets the fps / gaze Hz / worn / battery / delay detail.

App -> server: `command` `pause` `{on: bool}`: while paused, detection keeps running for the Log, but nothing reaches the Dot
(events are flagged `paused: true`).

### v3.1: temporal triggers use multiple images + a trend layer (supersedes "tiled" above)
- Temporal triggers send the last 3 frames (~0.7 s apart) as **3 images in the same request**, each preceded by a label
  `t-1.4s` / `t-0.7s` / `now`; still one Haiku call per tick.
- For each temporal trigger Haiku also returns a numeric `progress` (the subject's estimated distance to the target in metres,
  or a 0..1 closeness if distance isn't estimable; field `progress_unit: "m" | "closeness"`) and the subject's `bbox` in the newest frame.
- The engine keeps a per-trigger rolling history (track identity by bbox IoU ≥ 0.2; reset on a big jump), smooths it (median of 3), and fires
  only when the trend is consistent: moving toward the target for ≥ `trend.ticks` consecutive ticks (default 3) **and** past
  `trend.threshold` (default 1.5 m, or 0.7 closeness), with hysteresis (re-arm only after moving 30% back away) + cooldown.
  The same history mechanism holds the gaze "noticed" memory.
- Trigger field: `"trend": {"threshold": 1.5, "ticks": 3}` (temporal triggers only; editable in the app).
- Event field: `"trend": {"values": [4.0, 2.5, 1.2], "unit": "m"}`. The app's Log shows "4.0 → 2.5 → 1.2 m".

### v3.2: trend values are RELATIVE only (supersedes metres in v3.1; per Jaidev)
No absolute distances from the camera. For each temporal trigger Haiku returns `gap`: the image-space gap between the
subject and the target (e.g. person bbox ↔ water edge), as a fraction of image width (0 = touching, 1 = across the frame),
plus the subject `bbox`. The engine's memory only keeps measurable, relative quantities: `gap` per tick, its change, and bbox
size (growth = approaching the camera). It fires when the gap shrinks for ≥ `trend.ticks` ticks and the latest gap
≤ `trend.threshold` (fraction of the width, default 0.10), and the total shrink is ≥ 30% of the starting gap.
- Trigger field: `"trend": {"threshold": 0.10, "ticks": 3}` (threshold = gap as a fraction of the frame width).
- Event field: `"trend": {"values": [0.42, 0.25, 0.08], "unit": "gap"}`. The app shows "gap 42% → 25% → 8%".
Radar keeps metres (the sensor measures range directly).

### v3.3: final temporal signals (supersedes v3.1/v3.2 numbers)
Per temporal trigger Haiku returns (one call, 3 labelled frames): `subject_bbox`, `target_bbox` (newest frame),
`ordinal: "closer"|"same"|"farther"` (subject vs target compared with the earlier frames), and `touching: bool`.
The engine computes only relative, scale-free signals:
- `gap` = image-space distance between the subject and target boxes ÷ the subject box height ("body-heights"), and its change per tick
- `loom` = subject box area ÷ previous tick's area (> 1 = approaching the wearer)
- the ordinal history
It fires on a consistent trend: the gap shrinks for ≥ `ticks` ticks (by more than the sensitivity), OR `ordinal == "closer"` for ≥ `ticks`
ticks, OR `touching`, with hysteresis + cooldown.
- Trigger field: `"trend": {"ticks": 3, "sensitivity": "low"|"medium"|"high"}` (high = smaller changes count; default medium).
- Event field: `"trend": {"gap": [0.8, 0.4, 0.1], "ordinal": ["closer", "closer", "closer"], "touching": false, "loom": 1.3}`.
  The app shows "gap 0.8 → 0.4 → 0.1 body-heights · closer ×3".

## Heart rate (Oura) (added 2026-10-10)

Source: `spidey/heart.py` `HeartRateSense` (backends `open_oura` = the ring over Bluetooth via the open_oura CLI,
`replay` = a CSV, `fake` = synthetic). Engine hook: `Controller.start_heart` / `rules.apply_heart` (record: `docs/patches/heart_engine.md`). Ring setup: `docs/oura.md`.

Trigger (`source: "heart_rate"`; the app sends `kind: "custom"`, no `describe`):
```jsonc
{
  "id": "heart_racing", "name": "Heart racing", "kind": "custom", "source": "heart_rate",
  "if": {
    "min_delta_bpm": 15,   // fire when bpm >= 2-min median + this ...
    "min_delta_pct": 20,   // ... OR >= median * (1 + this/100)  (whichever threshold is lower)
    "sustain_s": 8         // continuously above the threshold for at least this long
  },
  "feel": "pulse", "cooldown_s": 60   // plus the usual intensity / rgb / dot
}
```
- "bpm" is the median of the last 5 s of readings (per-beat bpm is jumpy); "median" is the rolling 2-min median.
- No fires during the first 30 s (baseline warming up). Fires once per excursion: re-arms only after the bpm drops
  back below the threshold, and never within `cooldown_s` of the last fire. A gap > 10 s in readings restarts `sustain_s`.
- The old `hr_band_bpm` / `hr_window_s` keys (Rule section above) are read as `min_delta_bpm` if `min_delta_bpm` is missing;
  the window is fixed at 120 s.

Event additions: `sensor: "heart"`, and
```json
"heart": {"bpm": 104.0, "median_bpm": 71.0, "delta_bpm": 33.0, "above_s": 12.0}
```
`detection` is absent on heart events; `why` is a sentence ("HR 104 bpm, +33 over your 2-min median 71 for 12 s");
`latency_ms` (reading → publish) only when the reading is real-time. The app's Log shows `HR 104 (median 71, +33) · 12 s`.

Device card `oura` (in `device.status`; replaces the `coming_soon` stub):
```json
{"id": "oura", "name": "Oura", "kind": "heart_rate", "status": "connected",
 "backend": "open_oura", "last_bpm": 72.0, "median_bpm": 70.5, "since_last_s": 0.8, "rate_hz": 1.1,
 "connected": true, "detail": "open_oura · 72 bpm · 2-min median 70 · open_oura: live", "last_seen": 1791683202.5}
```
`status`: `connected` (open_oura, a reading in the last 10 s) | `simulated` (replay / fake running) | `disconnected`
(the ring is out of range / not worn / the CLI is restarting) | `off` (not enabled: `detail` = "off (heart.enabled: false)",
like the radar card).
Older engines still send `status: "coming_soon"`; the app keeps showing "Coming soon" for that.

## Live decisions: per-call trace stream ("why didn't it fire") (added 19:16)

App -> server: `{"type":"trace.subscribe","on":true}`, resent every ~2 s while the Log's "Live decisions" toggle is on
(like `haptic.subscribe`). The engine streams `trace` only while the last `on: true` is < 5 s old; `on: false` stops at once.
Reply: `{"type":"ack","for":"trace.subscribe","ok":true}`.

Server -> app, one per **full** Haiku call while subscribed (no images, except a small thumbnail on near misses):

```json
{"type": "trace", "id": 812, "ts": 1791684221.25, "t": 1791684222.70,
 "hazard": null, "urgency": 0.0, "reason": "Person at laptop; no hazards visible.",
 "latency_ms": 1432.0, "n_images": 1, "paused": false, "stale": false, "error": null,
 "raw": "{\"hazard\": null, ...}",
 "near_miss": true, "thumb": "<base64 JPEG ~240 px: gaze dot + the trigger's box>",
 "decisions": [{"id": "big_cat_look", "name": "Big cat (looking)", "fired": false, "matched": true,
                "why": "match, but looking_at needs gaze in box ≤0.6 s; last in box 2.3 s ago",
                "gaze_mode": "looking_at", "haiku_reason": "Tiger visible on laptop screen"}]}
```

- `id` = call id (also in `backend/logs/runs/<run>/calls.jsonl` and on camera events as `call_id`), `ts` = frame capture time,
  `t` = result time, `latency_ms` = frame -> result, `raw` = Haiku's raw reply (<= 2000 chars).
- `decisions`: one per enabled camera trigger. `matched` = Haiku matched it (or the trend layer fired); `why` is the reason it
  did not fire: `no match`, `not in Haiku's reply`, `match, but looking_at needs gaze in box ≤0.6 s; last in box 2.3 s ago`,
  `match, but unseen: looked at it 1.4 s ago (notice memory 3 s)`, `urgency 0.40 < min 0.5`, `cooldown 2.1 s left`,
  `stale: result 2.7 s old (max 2.5 s)`, `gaze left the box (gaze now at 0.31,0.55)`, `trend: too far: gap 1.25 > 1.0 body-heights`,
  `trend: veto: Haiku says farther`, `trend: disarmed after firing (...)`; `fired` when it fired. A fired decision while
  `paused: true` was not played.
- `near_miss: true` (+ `thumb`) when some decision has `matched: true, fired: false`.
- `stale: true`: the call's result arrived after a newer frame's result and was dropped without evaluation.

Camera `event`s also carry `call_id` and `raw` (Haiku's raw reply text) for the Log row detail sheet.

Engine-side gates added with this (they change firing): `max_result_age_s` (config, default 2.5): no fire on a result whose
frame is older than that at decision time; `looking_at` triggers with a box also need the **newest** gaze sample inside the box
(4 % margin) at decision time. Temporal triggers: the latest tick's `ordinal: "farther"` (or a gap that grew by more than ε)
vetoes a fire unless `touching`.

## v3.4: walking gate, time-to-contact, per-trigger staleness (added 2026-10-11)

All additions are optional. Older engines ignore the new `if` keys; older apps ignore the new status/event fields.

Trigger `if` additions (camera triggers; any source may carry them):
```jsonc
"if": {
  "only_while_walking": true,    // fire only while the glasses' IMU says the wearer is walking. If walking is unknown
                                 //   (no motion samples yet) the gate does not block.
  "max_ttc_s": 3.0,              // fire only if the wearer would reach the thing within this many seconds
                                 //   (time-to-contact from box growth; no estimate yet = no fire). Not for temporal triggers.
  "max_age_s": 6.0,              // per-trigger result staleness limit; overrides config max_result_age_s (static obstacles)
  "keep_on_view_change": true    // keep a result valid after the view changes (head turn); default false
}
```
**Apps must round-trip `if` keys they don't know.** The iOS app keeps every unmodelled `if` key verbatim
(`RuleCondition.extra`) and writes it back on `rules.upsert`; editors that rebuild the condition merge the original's
unmodelled keys back in (`RuleCondition.preserving`). The app's trigger wizard and editor expose `only_while_walking`
as "Only while I'm walking" (default off; off = key omitted, or `false` if the trigger already had it).

`status.source` additions (from the glasses' IMU; `null` while unknown):
```jsonc
"source": {..., "walking": true, "cadence_spm": 112.0, "turning": false}
```
`cadence_spm` = steps per minute, only while walking. The app's Neon card shows "walking · 112 steps/min" / "still",
plus "turning" when true.

Trigger results may carry `ttc_s` (seconds to contact, optional): it appears on camera `event`s (flattened from the
rule's extra, alongside `gaze_mode` / `looked_at_s_ago`) and may appear on `trace` decisions; the event `why` gains
"(about 2.1 s away)". Decision `why` strings for the new gates: `not walking (only while walking)`, `not approaching it (no time-to-contact yet)`,
`reaching it in 4.2 s (> 3 s)`, `stale: result 3.1 s old (max 6 s)`, `stale: the view changed 0.8 s ago, after this frame`.

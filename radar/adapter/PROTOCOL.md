# Radar feed protocol (radar-lab → anyone): `ws://<host>:8090/ws`

Served by `radar-lab/server.py` (aiohttp), protocol **version 2**. All messages are JSON text frames.
The same feed is produced from the live board, from a recording (`--replay`) or from a synthetic scenario
(`--scenario`), so consumers can't tell the difference except through the `source` field.

Units: metres, seconds, m/s, degrees. Time `t` is unix seconds (float) on the radar Mac's clock, stamped when the
frame arrived from the board (in replay/scenario mode, when it was re-emitted).

Coordinates are as seen from the radar: `y` points straight out of the board's antenna face, `x` points to the
radar's right, and `az_deg` is positive to the right. With `alert.mount = "back"` the server's `where` strings are
already converted to the wearer's frame (`behind-left` = the wearer's left).

## Server → client

### `hello` (once, on connect)
```json
{"type": "hello", "version": 2, "source": "live", "info": "BGT60TR13C live",
 "config": {"detect": {...}, "track": {...}, "alert": {...}},
 "live_keys": ["alert.close_enter_m", "alert.close_exit_m", "alert.approach_speed_mps", "alert.ttc_max_s",
               "alert.corridor_m", "alert.az_sign", "alert.mount", "alert.min_hold_s"],
 "events": [{"t": 1791682593.47, "frm": "too_close", "to": "calm"}],
 "recordings": [{"name": "182745-desk_idle", "frames": 601, "notes": "..."}],
 "axes": {"range_max": 6.3, "speed_max": 3.0, "n_range": 64, "n_doppler": 64, "frame_s": 0.05}}
```
- `source`: `live` | `replay` | `scenario`
- `config`: every tunable (see `radar_config.py`)
- `events`: the last ≤50 state changes, so a late joiner has context

### `frame` (every radar frame, 20 Hz)
```json
{"type": "frame", "seq": 454, "t": 1791682605.474, "fps": 20.0, "source": "live", "info": "BGT60TR13C live",
 "proc_ms": 1.76,
 "state": "approaching", "urgency": 0.74, "since": 1791682604.9,
 "primary": {"track": 90, "r": 2.31, "az_deg": -12.4, "closing": 1.02, "ttc": 1.77, "where": "ahead",
             "why": "approaching"},
 "tracks": [{"id": 90, "x": -0.50, "y": 2.26, "r": 2.31, "az_deg": -12.4, "vr": -1.02, "closing": 1.02,
             "vx": 0.08, "vy": -1.0, "cpa": 0.29, "t_cpa": 2.2, "ttc": 1.77, "status": "confirmed",
             "coasting": false, "misses": 0, "confidence": 1.0, "age_s": 1.9, "snr_db": 31.0}],
 "dets": [{"r": 2.33, "az_deg": -10.9, "vr": -1.04, "snr_db": 33.1}],
 "event": {"t": 1791682604.9, "frm": "calm", "to": "approaching", "track": 90, "r": 2.45, "az_deg": -12.0,
           "closing": 1.0, "ttc": 1.95, "where": "ahead", "why": "approaching"},
 "heat": "AAAAgnBdZHVw…",
 "recording": {"name": "183242-walk", "frames": 120, "seconds": 6.0}}
```

| field | meaning |
|---|---|
| `state` | **The debounced alert. This is what to act on.** `calm` \| `approaching` \| `too_close`. It never reacts to a single frame (see "Alert semantics" below). |
| `urgency` | 0 when calm. `approaching`: 0.6–0.9, higher the sooner contact is. `too_close`: 0.9–1.0, higher the closer. |
| `since` | `t` when the current state started (null before the first change) |
| `primary` | the track behind the current alert (null when calm). It is sticky, so it doesn't hop between body parts. |
| `primary.where` | `left` / `ahead` / `right`, or `behind` / `behind-left` / `behind-right` with a back mount. Has hysteresis (±20° to leave "ahead", ±10° to return) and a 0.4 s dwell. |
| `primary.ttc` | seconds until it reaches the too-close zone at the current closing speed. null if it isn't closing. |
| `tracks[]` | all live tracks, tentative and confirmed. **Only `status: "confirmed"` tracks are real objects.** |
| `tracks[].closing` | = −`vr`. Positive means coming toward the radar (m/s). |
| `tracks[].cpa` / `t_cpa` | predicted closest-pass distance (m) and time to it (s) on a straight line. A walk-by has a large `cpa`. |
| `tracks[].coasting` | no detection this frame; the position is predicted (a confirmed track survives up to 0.4 s like this) |
| `tracks[].confidence` | hits in the last 7 frames / 7 |
| `dets[]` | raw per-frame detections, before tracking (debug/visualisation only; they *do* flicker) |
| `event` | present only on the frame where `state` changed. An event to `calm` has no track fields. |
| `heat` | every 2nd frame only. Base64 of 64×64 uint8 = range–Doppler power over the top 40 dB. Row-major `[doppler][range]`; doppler row 0 is the fastest approaching speed (−3 m/s), row 32 is 0 m/s; range column k = k × 0.1 m. |
| `recording` | present while the server is recording raw frames |
| `proc_ms` | processing time for this frame (detect + track + alert) |

### `event` (standalone)
`{"type": "event", "event": {"t": ..., "frm": "too_close", "to": "calm", "why": "reset"}}`: sent when a replay or
scenario loops and the tracker resets while an alert was up. Frames after it report `state: "calm"`.

### Others
- `{"type": "config", "config": {...}}`: after any config change (broadcast to all clients)
- `{"type": "info", "info": "radar error: … (retrying)"}`: board/source status changes
- `{"type": "record", "status": "started" | "stopped", "name": "...", "frames": 120, "path": "/…/recordings/…"}`
- `{"type": "pong", "c": <echo>, "t": <server time>}`
- `{"type": "error", "error": "..."}`: for a bad client message

## Client → server
| message | effect |
|---|---|
| `{"type": "ping", "c": 123.4}` | → `pong` with the same `c` (round-trip measurement) |
| `{"type": "config", "set": {"alert.close_enter_m": 0.8}}` | change live keys (see `live_keys`). Changing `close_enter_m` alone also sets `close_exit_m = enter + 0.2`. |
| `{"type": "record", "action": "start", "name": "walk_toward", "notes": "...", "seconds": 20}` | record raw frames to `recordings/<HHMMSS>-<name>/` (live board only; `seconds` optional) |
| `{"type": "record", "action": "stop"}` | stop recording |

## Alert semantics (what `state` guarantees)
- Only **confirmed** tracks count: 5 detections within the last 7 frames (≥ 0.25 s). A confirmed track that
  coasts longer than 0.3 s can't raise alerts.
- **too_close**: a confirmed track is inside `close_enter_m` (0.5 m) for `close_dwell_s` (0.2 s). It clears only
  after the track is back beyond `close_exit_m` (0.7 m).
- **approaching**: for `approach_dwell_s` (0.3 s), all of these hold: closing speed > `approach_speed_mps`
  (0.35 m/s); range fell ≥ `approach_drop_m` (0.2 m) over `approach_window_s` (0.4 s); predicted closest pass ≤
  `corridor_m` (0.6 m), so walk-bys and diagonal misses don't trigger; time to contact ≤ `ttc_max_s` (4 s).
- Escalation (calm → approaching → too_close) is immediate once those conditions hold. An alert stays up for at
  least `min_hold_s` (1 s) and steps down only after `clear_s` (0.8 s) without support.
- Measured on synthetic walks at 20 fps: `approaching` fires about 0.85 s after a person starts walking in,
  `too_close` 0.15–0.2 s after they cross 0.5 m. Server-side processing is about 2 ms per frame, and frame →
  client is about 5 ms on localhost.

## Fail-safe expectations for consumers
- Treat "no `frame` for > 0.5 s" as **no information** (not as calm-and-safe, not as an alert). The Spidey adapter
  then reports `hazard=None`.
- Reconnect on close. The server restarts cleanly, and the board is released on Ctrl-C.

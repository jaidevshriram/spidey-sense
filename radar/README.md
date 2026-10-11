# Radar sense

Spidey Sense's radar input: someone **approaching** you (or **too close**) is felt on the Dot, left/right aware.
The radar is an Infineon DEMO-BGT60TR13C, plugged into the Mac over USB. See [NOTICE.md](NOTICE.md) for credits.

## Who owns what

| Piece | Where | Notes |
|---|---|---|
| Board + signal processing + alert state | `~/Downloads/radar-lab/server.py` (separate workstream) | The **only** process that opens the board. WebSocket `ws://localhost:8090/ws`, ~17–20 fps. |
| Read-only client | `backend/spidey/radar.py` (`RadarSense`) | Background thread, auto-reconnect (1→5 s backoff). Never touches the USB port. |
| Fake server for tests | `backend/tests/fake_radar_server.py` | Same message schema, scripted walks, port 8091+. |
| Tests | `backend/tests/test_radar_client.py` | `cd backend && ../.venv/bin/python -m unittest tests.test_radar_client -v` |

## Run

```bash
# 1) the radar server (radar-lab's own venv)
cd ~/Downloads/radar-lab && .venv/bin/python server.py          # board
cd ~/Downloads/radar-lab && .venv/bin/python server.py --sim    # synthetic target, no board

# 2) watch it from Spidey (prints every alert update, confirmed approaches, and position lines while calm)
cd ~/Desktop/GIT-Projects/spidey-sense/backend
../.venv/bin/python -m spidey.radar [--url ws://localhost:8090] [--side-sign -1] [--seconds 30]
```

The radar-lab page at http://localhost:8090 still works alongside; any number of WebSocket clients can listen.
Spidey never sends `config` to the radar server (that would change it for everyone); left/right is fixed on our side.

## Interface (used by the engine)

```python
from spidey.radar import RadarSense
rs = RadarSense("ws://localhost:8090", on_target=cb, side_sign=1)   # never blocks or raises
rs.status()  # {"connected", "url", "mode": "board"|"sim"|"unknown", "last", "rate_hz", "since_last_s"}
rs.close()
```

`cb(dict)` runs on the radar thread for every update that is not `calm`, plus once when it goes back to `calm`
(also a `calm` with `reason: "disconnected"` if the link drops mid-alert):

| key | meaning |
|---|---|
| `state` | `approaching` / `too_close` / `calm` (the server's debounced alert state) |
| `distance_m`, `speed_mps` | range; radial speed, **positive = toward the radar** |
| `angle_deg`, `side` | azimuth × `side_sign`; `side` is `center` when \|angle\| < 10°, else `left`/`right` |
| `confirmed` | the server's own confirmation (tracked schema: alerts only come from confirmed tracks), else derived: same state ≥ 3 updates in a row |
| `ts` | receive time, Mac clock (unix s); `server_ts` is the server's frame time |
| `track_id` | tracker id when the server has tracking, else `None` |
| `urgency` | server's urgency if sent, else derived like radar-lab's `alerts.py` (too_close 0.9–1.0, approaching 0.6–0.9 by time-to-contact) |

Radar triggers (`docs/protocol.md` v3) should fire on `state == "approaching" and confirmed`, filtered by
`max_distance_m` / `min_speed_mps` / `min_urgency`.

### Server schemas understood

- **legacy** (radar-lab `server.py` today): `{"type":"frame","t","fps","info","state","target":{"dist","speed","angle","snr"}|null,"proc_ms","heat","event"?}`.
  `speed` is negative when approaching; `angle` in degrees, + = right as seen from the board. `hello` on connect carries
  `info` (`"sim"` / `"BGT60TR13C live"`), which sets `mode`.
- **tracked** (radar-lab `pipeline.py` + `tracker.py` + `alerts.py`, being wired in): `state`, `urgency`,
  `primary: {track, r, az_deg, closing, ttc, where, why}`, `tracks: [{id, r, az_deg, closing, status: confirmed|tentative, ...}]`.
- **flat** (a possible drop-in source): `distance_m`, `speed_mps`, `angle_deg`, `confirmed`, `track_id`.

Why the tracked schema matters: the legacy server judges "approaching" from one target's Doppler, so a person walking
**past** in a straight line 2.5 m away reads as approaching at the ends of the walk (−0.6 m/s radial). The fake server's
`walk_by_line` scenario shows it: 34 confirmed-approaching callbacks with the legacy schema. radar-lab's tracker rejects
this with a predicted-closest-pass corridor check.

## AZ_SIGN calibration (left/right)

radar-lab hasn't calibrated `AZ_SIGN` yet, so check once per mounting:

1. Mount the board as it will be worn (antenna side out, upright). Start `server.py` (board) in radar-lab.
2. Run `../.venv/bin/python -m spidey.radar` from `backend/`.
3. Stand ~1.5 m in front, then walk slowly from **your left to your right as seen by the wearer** (i.e. from the radar's
   left to its right, looking out from the board). Watch the calm position lines: `az` should go from negative to
   positive and `side` from `left` → `center` → `right`.
4. If it's mirrored, use `--side-sign -1` (and set the same `side_sign` in the engine's radar config). If `az` barely
   changes as you walk sideways, the board is rotated 90°; tell radar-lab (the horizontal RX pair is then RX1/RX2).
5. For a **back-mounted** radar, the radar's right is the wearer's left: flip `side_sign` accordingly.

## Measured (2026-10-10)

- Fake server, both schemas: approach from the left 3 m → 0.5 m at 0.6 m/s gives a confirmed `approaching` at ~2.85 m
  (left, 0.60 m/s), then confirmed `too_close` at 0.5 m, then one `calm`. Walk past (tangential, 2.5 m): no confirmed
  approach. Walk away: no callbacks. Server send → `on_target`: median 0.36 ms, p95 1.1 ms, max 18 ms (localhost, n=456).
- radar-lab `server.py --sim` (no board): ~17.5 Hz, `mode=sim`, confirmed approach 3 updates after connecting
  (sim target loops 3 m → 0.5 m at 1 m/s and drifts ±30°, so the state stays `approaching` and never returns to calm).

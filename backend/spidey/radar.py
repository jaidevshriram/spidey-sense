"""Radar "sense": a read-only WebSocket client for the radar-lab server (Infineon BGT60TR13C).

The radar board is owned by another process, `~/Downloads/radar-lab/server.py` (aiohttp, WebSocket at
`/ws` on :8090). That process is the only one allowed to open the board; this module never touches the
USB/serial port. It just listens to the frame stream and turns it into Spidey "targets":

    on_target({"state": "approaching"|"too_close"|"calm", "distance_m", "speed_mps" (+ = toward radar),
               "angle_deg", "side": "left"|"right"|"center", "confirmed", "ts" (Mac unix time),
               "track_id", "urgency", "server_ts"})

called for every update that is not "calm", plus once when the state returns to "calm".

Two server message schemas are understood (radar-lab is moving from the first to the second):
  * legacy  (server.py + AlertEngine): {"type":"frame","t","fps","info","state",
             "target": {"dist","speed" (- = approaching),"angle" (deg, + = right),"snr"} | null, "heat", "event"?}
  * tracked (pipeline.py + tracker.py + alerts.py): {"state","urgency","primary": {"track","r","az_deg",
             "closing","ttc","where","why"} | null, "tracks": [{"id","r","az_deg","closing","status",...}], ...}
A flat message with distance_m / speed_mps / angle_deg / confirmed is accepted too.

`confirmed` is the server's own confirmation when it has one (tracked schema: alerts only come from
M-of-N confirmed tracks; or an explicit "confirmed" field); otherwise it is derived here: the same state
for >= 3 consecutive updates.

`side_sign` calibrates left/right (radar-lab's AZ_SIGN is not calibrated): angle_deg in the output is
already multiplied by side_sign. CLI for the calibration walk:  python -m spidey.radar [--side-sign -1]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import threading
import time
from collections import deque
from urllib.parse import urlsplit, urlunsplit

try:                                              # websockets >= 13
    from websockets.asyncio.client import connect as ws_connect
except ImportError:                               # pragma: no cover - older websockets
    from websockets import connect as ws_connect

CENTER_DEG = 10.0          # |angle| below this -> "center"
CONFIRM_RUN = 3            # derived confirmation: same state this many updates in a row
CLOSE_ZONE_M = 0.5         # radar-lab AlertConfig.close_enter_m (for the derived urgency)
TTC_MAX_S = 4.0            # radar-lab AlertConfig.ttc_max_s
BACKOFF_S = (1, 2, 3, 4, 5)


STALE_S = 0.5


def ws_url(url: str) -> str:
    """radar-lab serves the socket at /ws; accept the bare host URL too."""
    p = urlsplit(url)
    if p.path in ("", "/"):
        p = p._replace(path="/ws")
    return urlunsplit(p)


def side_of(angle_deg: float) -> str:
    if abs(angle_deg) < CENTER_DEG:
        return "center"
    return "right" if angle_deg > 0 else "left"


def _f(x, default=0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def derived_urgency(state: str, distance_m: float, speed_mps: float) -> float:
    """Same shape as radar-lab alerts.AlertMachine.urgency(), for servers that don't send one."""
    if state == "too_close":
        return round(min(1.0, 0.9 + 0.1 * max(0.0, 1 - distance_m / CLOSE_ZONE_M)), 3)
    if state == "approaching":
        ttc = (distance_m - CLOSE_ZONE_M) / speed_mps if speed_mps > 0.05 else TTC_MAX_S
        return round(min(0.9, max(0.6, 0.6 + 0.3 * (1 - max(0.0, ttc) / TTC_MAX_S))), 3)
    return 0.0


class RadarSense:
    def __init__(self, url: str = "ws://localhost:8090", on_target=None, side_sign: int = 1):
        self.url = url
        self.on_target = on_target
        self.side_sign = -1 if side_sign < 0 else 1
        self.on_frame = None                 # optional: called with EVERY parsed update (CLI / debugging)
        self._lock = threading.Lock()
        self._connected = False
        self._mode = "unknown"
        self._info = ""
        self._last: dict | None = None
        self._last_rx: float | None = None
        self._rx_times: deque = deque()
        self._run_state, self._run_len = "calm", 0
        self._alerting = False               # we emitted a non-calm update and owe a "calm"
        self._geom = dict(distance_m=0.0, speed_mps=0.0, angle_deg=0.0, track_id=None)
        self._where, self._ttc, self._why = None, None, None
        self.stale_s = STALE_S                # radar-lab PROTOCOL v2: no frame for > 0.5 s = no information
        self._closing = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._thread = threading.Thread(target=self._thread_main, name="radar-sense", daemon=True)
        try:
            self._thread.start()
            threading.Thread(target=self._stale_watch, name="radar-stale", daemon=True).start()
        except Exception as e:               # never raise from __init__
            print(f"[radar] could not start client thread: {e!r}", flush=True)

    def _stale_watch(self):
        """Fail safe: an alert must not hang on when the feed stops (server down, board unplugged)."""
        while not self._closing:
            time.sleep(0.1)
            if self._alerting and self._last_rx is not None and time.time() - self._last_rx > self.stale_s:
                self._emit(dict(self._last or {}, state="calm", confirmed=False, ts=time.time(),
                                reason="stale", urgency=0.0))
                self._alerting = False
                self._run_state, self._run_len = "calm", 0

    # ---- public ---------------------------------------------------------------------------
    def status(self) -> dict:
        now = time.time()
        with self._lock:
            while self._rx_times and now - self._rx_times[0] > 2.0:
                self._rx_times.popleft()
            rate = len(self._rx_times) / 2.0 if self._rx_times else 0.0
            return {"connected": self._connected, "url": self.url, "mode": self._mode,
                    "last": dict(self._last) if self._last else None, "rate_hz": round(rate, 1),
                    "since_last_s": None if self._last_rx is None else round(now - self._last_rx, 2)}

    def close(self) -> None:
        self._closing = True
        loop, task = self._loop, self._task
        if loop and task and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=3)

    # ---- connection loop (background thread) ----------------------------------------------
    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        try:
            self._task = self._loop.create_task(self._main())
            self._loop.run_until_complete(self._task)
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            self._loop.close()

    async def _main(self):
        target, attempt = ws_url(self.url), 0
        while not self._closing:
            try:
                async with ws_connect(target, open_timeout=3, max_size=2 ** 22,
                                      ping_interval=10, ping_timeout=10) as ws:
                    attempt = 0
                    self._set_connected(True)
                    print(f"[radar] connected {target}", flush=True)
                    async for raw in ws:
                        self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt == 0:
                    print(f"[radar] {target}: {type(e).__name__}: {e} (retrying)", flush=True)
            finally:
                if self._connected:
                    print("[radar] disconnected", flush=True)
                self._set_connected(False)
            if self._closing:
                break
            await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
            attempt += 1

    def _set_connected(self, on: bool):
        with self._lock:
            was, self._connected = self._connected, on
        if was and not on and self._alerting:          # don't leave the engine stuck in an alert
            self._emit(dict(self._last or {}, state="calm", confirmed=False, ts=time.time(),
                            reason="disconnected", urgency=0.0))
            self._alerting = False
        if not on:
            self._run_state, self._run_len = "calm", 0

    # ---- message handling -----------------------------------------------------------------
    def _handle(self, raw):
        now = time.time()
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type", "frame")
        if "info" in msg or "mode" in msg:
            self._note_mode(msg)
        if kind != "frame" and "state" not in msg:
            return                                       # hello / info / config / pong
        upd = self._parse(msg, now)
        if upd is None:
            return
        with self._lock:
            self._last, self._last_rx = upd, now
            self._rx_times.append(now)
        if self.on_frame:
            try:
                self.on_frame(upd)
            except Exception as e:
                print(f"[radar] on_frame error: {e!r}", flush=True)
        if upd["state"] != "calm":
            self._alerting = True
            self._emit(upd)
        elif self._alerting:
            self._alerting = False
            self._emit(upd)

    def _note_mode(self, msg: dict):
        mode = msg.get("mode")
        info = str(msg.get("info") or "")
        if mode in ("board", "sim"):
            self._mode = mode
        elif info:
            self._info = info
            low = info.lower()
            if low == "sim" or "synthetic" in low or low.startswith("sim"):
                self._mode = "sim"
            elif "live" in low or "bgt60" in low:
                self._mode = "board"

    def _parse(self, msg: dict, now: float) -> dict | None:
        state = msg.get("state")
        if state not in ("calm", "approaching", "too_close"):
            return None
        server_confirmed = None
        track_id = None
        urgency = msg.get("urgency")
        geom = None                                       # (distance_m, speed_mps toward, angle_deg raw)

        if "distance_m" in msg:                           # flat (drop-in source) schema
            geom = (_f(msg.get("distance_m")), _f(msg.get("speed_mps")), _f(msg.get("angle_deg")))
            track_id = msg.get("track_id")
            if "confirmed" in msg:
                server_confirmed = bool(msg["confirmed"])
        elif "primary" in msg or "tracks" in msg:         # tracked schema (pipeline.py)
            prim = msg.get("primary")
            tracks = [t for t in (msg.get("tracks") or []) if isinstance(t, dict)]
            if not prim and tracks:
                conf = [t for t in tracks if t.get("status") == "confirmed"] or tracks
                t = min(conf, key=lambda t: _f(t.get("r"), 99))
                prim = dict(track=t.get("id"), r=t.get("r"), az_deg=t.get("az_deg"), closing=t.get("closing"),
                            _confirmed=t.get("status") == "confirmed")
            if prim:
                geom = (_f(prim.get("r")), _f(prim.get("closing")), _f(prim.get("az_deg")))
                track_id = prim.get("track", prim.get("id"))
                # protocol v2: the server's own debounced direction, already in the wearer's frame for a back mount
                self._where = prim.get("where") or None
                self._ttc, self._why = prim.get("ttc"), prim.get("why")
            # AlertMachine only raises alerts from confirmed tracks -> a non-calm state is confirmed
            server_confirmed = state != "calm" or bool(prim and prim.get("_confirmed"))
        else:                                             # legacy schema (server.py AlertEngine)
            tgt = msg.get("target")
            if isinstance(tgt, dict):
                geom = (_f(tgt.get("dist")), -_f(tgt.get("speed")), _f(tgt.get("angle")))
            if "confirmed" in msg:
                server_confirmed = bool(msg["confirmed"])

        if geom is not None:
            angle = geom[2] * self.side_sign
            self._geom = dict(distance_m=round(geom[0], 3), speed_mps=round(geom[1], 3),
                              angle_deg=round(angle, 1), track_id=track_id)
        g = self._geom                                    # no target this frame: keep the last geometry

        if state == self._run_state:
            self._run_len += 1
        else:
            self._run_state, self._run_len = state, 1
        confirmed = server_confirmed if server_confirmed is not None else self._run_len >= CONFIRM_RUN
        if urgency is None:
            urgency = derived_urgency(state, g["distance_m"], g["speed_mps"])
        where = self._where if state != "calm" else None
        if where and self.side_sign < 0:                 # calibration flip applies to the server's direction too
            where = where.replace("left", "@").replace("right", "left").replace("@", "right")
        side = side_of(g["angle_deg"])
        if where:                                         # server direction wins (hysteresis + dwell, mount-aware)
            side = "left" if "left" in where else "right" if "right" in where else "center"
        return {"state": state, "distance_m": g["distance_m"], "speed_mps": g["speed_mps"],
                "angle_deg": g["angle_deg"], "side": side, "where": where or side,
                "ttc_s": self._ttc, "server_why": self._why, "confirmed": confirmed,
                "ts": now, "track_id": g["track_id"], "urgency": _f(urgency),
                "server_ts": msg.get("t"), "has_target": geom is not None}

    def _emit(self, upd: dict):
        if not self.on_target:
            return
        try:
            self.on_target(upd)
        except Exception as e:
            print(f"[radar] on_target error: {e!r}", flush=True)


# ---- CLI: watch the stream / AZ_SIGN calibration walk ---------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Print radar updates from the radar-lab server (read-only).")
    ap.add_argument("--url", default="ws://localhost:8090")
    ap.add_argument("--side-sign", type=int, default=1, choices=(1, -1),
                    help="flip left/right if the printed side is mirrored")
    ap.add_argument("--every", type=float, default=0.25, help="seconds between plain position lines")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until Ctrl-C)")
    args = ap.parse_args(argv)

    t0 = time.time()
    last_line = [0.0]
    episode = [False]

    def on_frame(u):
        now = time.time()
        if u["has_target"] and now - last_line[0] >= args.every and u["state"] == "calm":
            last_line[0] = now
            print(f"{now - t0:7.2f}s  calm         r={u['distance_m']:.2f} m  v={u['speed_mps']:+.2f} m/s  "
                  f"az={u['angle_deg']:+6.1f}°  side={u['side']}", flush=True)

    def on_target(u):
        rel = time.time() - t0
        flag = "confirmed" if u["confirmed"] else "tentative"
        print(f"{rel:7.2f}s  {u['state']:<11}  r={u['distance_m']:.2f} m  v={u['speed_mps']:+.2f} m/s  "
              f"az={u['angle_deg']:+6.1f}°  side={u['side']:<6}  {flag}  urg={u['urgency']:.2f}"
              + (f"  track={u['track_id']}" if u.get("track_id") is not None else ""), flush=True)
        if u["state"] == "approaching" and u["confirmed"] and not episode[0]:
            episode[0] = True
            print(f"        *** CONFIRMED APPROACH from the {u['side']} at {u['distance_m']:.2f} m, "
                  f"{u['speed_mps']:.2f} m/s", flush=True)
        if u["state"] == "calm":
            episode[0] = False

    rs = RadarSense(args.url, on_target=on_target, side_sign=args.side_sign)
    rs.on_frame = on_frame
    try:
        next_status = time.time() + 5
        while not args.seconds or time.time() - t0 < args.seconds:
            time.sleep(0.2)
            if time.time() >= next_status:
                next_status += 5
                s = rs.status()
                print(f"[status] connected={s['connected']} mode={s['mode']} rate={s['rate_hz']} Hz "
                      f"since_last={s['since_last_s']} s", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        rs.close()


if __name__ == "__main__":
    main()

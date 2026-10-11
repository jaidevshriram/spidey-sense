"""Radar -> Spidey Detection. Drop-in for spidey-sense `backend/spidey/radar.py` (see INTEGRATION.md).

Reads the radar-lab feed (ws://localhost:8090/ws, protocol in PROTOCOL.md) on a background thread.
`analyze(frame)` never blocks: it turns the newest radar message into a Spidey `Detection`.

The radar server already debounces (confirmed tracks, dwell, hysteresis, hold), so one radar frame
can never become an alert here. This adapter only maps states:

    radar state     -> Detection.hazard         urgency (from the radar)
    calm            -> None                     0.0
    approaching     -> "something approaching"  0.6 .. 0.9 (sooner contact = higher)
    too_close       -> "too close"              0.9 .. 1.0

and fails safe: no data for `stale_s` (radar server down, board unplugged) -> hazard None.

    python radar_detector.py [ws://host:8090/ws] [--mount back]   # print Detections live
"""
from __future__ import annotations

import json
import threading
import time

try:                                      # inside spidey-sense: backend/spidey/radar.py
    from .types import Detection
except ImportError:
    try:                                  # run as a script inside spidey-sense (package installed)
        from spidey.types import Detection
    except ImportError:                   # radar-lab tests: local copy of Spidey's types
        from spidey_stub.types import Detection

DEFAULT_URL = "ws://localhost:8090/ws"
HAZARDS = {"approaching": "something approaching", "too_close": "too close"}


class RadarClient:
    """Keeps the newest radar frame message; reconnects forever. Thread-safe reads."""

    def __init__(self, url: str = DEFAULT_URL, reconnect_s: float = 1.0):
        self.url, self.reconnect_s = url, reconnect_s
        self.latest: dict | None = None
        self.latest_rx: float = 0.0           # local time the latest frame arrived
        self.hello: dict | None = None
        self.connected = False
        self.error: str | None = None
        self.n_frames = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ws = None
        self._thread = threading.Thread(target=self._run, name="radar-ws", daemon=True)
        self._thread.start()

    def _run(self):
        from websockets.sync.client import connect   # websockets >= 12 (spidey-sense has 17.x)
        while not self._stop.is_set():
            try:
                with connect(self.url, open_timeout=2, close_timeout=1, max_size=2 ** 22) as ws:
                    self._ws = ws
                    self.connected, self.error = True, None
                    for raw in ws:
                        if self._stop.is_set():
                            break
                        msg = json.loads(raw)
                        kind = msg.get("type")
                        if kind == "frame":
                            msg.pop("heat", None)          # not needed here
                            with self._lock:
                                self.latest, self.latest_rx = msg, time.time()
                                self.n_frames += 1
                        elif kind == "hello":
                            self.hello = msg
            except Exception as e:                          # refused / dropped / bad message
                self.error = f"{type(e).__name__}: {e}"
            finally:
                self._ws = None
                self.connected = False
            self._stop.wait(self.reconnect_s)

    def snapshot(self) -> tuple[dict | None, float]:
        with self._lock:
            return self.latest, self.latest_rx

    def wait_for_frames(self, timeout: float = 5.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.n_frames:
                return True
            time.sleep(0.05)
        return False

    def close(self):
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._thread.join(timeout=2)


class RadarDetector:
    """Spidey detector backed by the radar feed. Config keys (detector: in the YAML) = __init__ args.

    url            radar-lab WebSocket
    mount          None = trust the radar server's "where"; "front"/"back" = recompute here
                   (back: the radar's right is your left -> "behind-left" etc.)
    seen_by_user   what to report; radar can't see gaze. False -> unseen-rules (blindspot) can fire.
                   For a back mount, False is always right (you can't see behind you).
    stale_s        older radar data than this -> no hazard (fail safe)
    min_state      "approaching" (default) or "too_close": ignore lower alert levels
    """

    def __init__(self, url: str = DEFAULT_URL, mount: str | None = None, seen_by_user: bool = False,
                 stale_s: float = 0.5, min_state: str = "approaching", hazards: dict | None = None,
                 connect_timeout_s: float = 0.0):
        self.mount, self.seen_by_user, self.stale_s = mount, bool(seen_by_user), float(stale_s)
        self.min_rank = {"approaching": 1, "too_close": 2}[min_state]
        self.hazards = {**HAZARDS, **(hazards or {})}
        self.client = RadarClient(url)
        if connect_timeout_s:
            self.client.wait_for_frames(connect_timeout_s)

    def _where(self, primary: dict) -> str | None:
        if self.mount is None:
            return primary.get("where")
        az = float(primary.get("az_deg") or 0.0)
        side = "ahead" if abs(az) < 15 else ("right" if az > 0 else "left")
        if self.mount == "back":
            return {"ahead": "behind", "right": "behind-left", "left": "behind-right"}[side]
        return side

    def analyze(self, frame=None) -> Detection:
        msg, rx = self.client.snapshot()
        now = time.time()
        if msg is None or now - rx > self.stale_s:
            why = "radar: no data" if msg is None else f"radar: stale ({now - rx:.1f} s old)"
            return Detection(None, None, False, 0.0, raw={"radar": "stale", "error": self.client.error,
                                                         "connected": self.client.connected}, reason=why)
        state = msg.get("state", "calm")
        rank = {"calm": 0, "approaching": 1, "too_close": 2}.get(state, 0)
        p = msg.get("primary") or {}
        age_ms = max(0.0, (now - float(msg.get("t", now))) * 1000)
        raw = {"radar": state, "seq": msg.get("seq"), "frame_ts": msg.get("t"), "age_ms": round(age_ms, 1),
               "track": p.get("track"), "r": p.get("r"), "az_deg": p.get("az_deg"),
               "closing": p.get("closing"), "ttc": p.get("ttc"),
               "n_confirmed": sum(1 for t in msg.get("tracks", []) if t.get("status") == "confirmed")}
        if rank < self.min_rank or not p:
            return Detection(None, None, False, 0.0, raw=raw, reason=f"radar: {state}")
        where = self._where(p)
        ttc = f", contact in {p['ttc']:.1f} s" if p.get("ttc") is not None else ""
        reason = (f"radar: {self.hazards[state]} {where or ''} at {p.get('r', 0):.2f} m, "
                  f"closing {p.get('closing', 0):.1f} m/s{ttc}").replace("  ", " ")
        return Detection(hazard=self.hazards[state], where=where, seen_by_user=self.seen_by_user,
                         urgency=float(msg.get("urgency", 0.0)), raw=raw, reason=reason)

    def close(self):
        self.client.close()


class CombinedDetector:
    """Camera/VLM detector + radar: a radar alert wins (fast, works in the dark / behind you);
    otherwise the primary detector's result passes through unchanged (including None = no news).

    With a combined detector set `detect_every_s: 0` (the VLM throttles itself), or the radar is
    only polled at that rate.
    """

    def __init__(self, primary, radar: RadarDetector):
        self.primary, self.radar = primary, radar

    def set_triggers(self, triggers):                # custom plain-English triggers -> the VLM
        if hasattr(self.primary, "set_triggers"):
            self.primary.set_triggers(triggers)

    def analyze(self, frame):
        rd = self.radar.analyze(frame)
        pd = self.primary.analyze(frame)
        if rd.hazard is not None:
            if pd is not None:
                rd.triggers = dict(pd.triggers or {})
                rd.raw["vision"] = {"hazard": pd.hazard, "where": pd.where, "urgency": pd.urgency}
            return rd
        return pd

    def close(self):
        self.radar.close()
        self.primary.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="print the Detections Spidey would get from the radar")
    ap.add_argument("url", nargs="?", default=DEFAULT_URL)
    ap.add_argument("--mount", choices=["front", "back"])
    ap.add_argument("--hz", type=float, default=10)
    a = ap.parse_args()
    det = RadarDetector(a.url, mount=a.mount)
    print(f"connecting to {a.url} …", flush=True)
    last = None
    try:
        while True:
            d = det.analyze(None)
            key = (d.hazard, d.where)
            if key != last:
                print(f"{time.strftime('%H:%M:%S')}  hazard={d.hazard!r:26} where={d.where!r:16} "
                      f"urgency={d.urgency:.2f} seen={d.seen_by_user}  {d.reason}", flush=True)
                last = key
            time.sleep(1 / a.hz)
    except KeyboardInterrupt:
        pass
    finally:
        det.close()

"""A stand-in for ~/Downloads/radar-lab/server.py: same WebSocket message schema, scripted targets, no board.

Schemas (copied from radar-lab, not its code):
  legacy  -> {"type":"frame","i","t","fps","info","target":{"dist","speed"(- = approaching),"angle"(deg,+ = right),"snr"}|null,
              "state","proc_ms","heat":null, "event"?:{"t","frm","to","dist","speed"}}   (server.py today)
  tracked -> {"type":"frame","i","t","fps","info","state","urgency","since","primary":{"track","r","az_deg","closing","ttc",
              "where","why"}|null,"tracks":[{"id","x","y","r","az_deg","vr","closing","status",...}],"event"?}  (pipeline.py)
On connect it sends {"type":"hello","info":"sim"|...,"config":{...},"axes":{...}} like server.py.

    python tests/fake_radar_server.py --scenario approach_left --loop --port 8091 [--schema tracked]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import threading
import time
from collections import deque

from websockets.asyncio.server import serve

FPS = 20.0
ALERT_DIST = 0.6        # m (radar-lab default 0.35/0.5; 0.6 so a 0.5 m stop clearly counts)
APPROACH_MPS = 0.3


def scenario_points(name: str):
    """Yield (x, y) in metres per frame (radar at origin, y straight out, x + = right)."""
    dt = 1 / FPS
    if name == "approach_left":           # 3 m -> 0.5 m at 0.6 m/s from 30° left, then stand 1 s
        az = math.radians(-30)
        r = 3.0
        while r > 0.5:
            yield r * math.sin(az), r * math.cos(az)
            r -= 0.6 * dt
        for _ in range(int(FPS)):
            yield 0.5 * math.sin(az), 0.5 * math.cos(az)
    elif name == "walk_past":             # tangential: arc at r = 2.5 m, -40° -> +40° at ~1 m/s (radial speed 0)
        a, a_end = math.radians(-40), math.radians(40)
        while a < a_end:
            yield 2.5 * math.sin(a), 2.5 * math.cos(a)
            a += 1.0 * dt / 2.5
    elif name == "walk_by_line":          # straight line at y = 2.5 m, x -2 -> +2 at 1 m/s: radial speed
        x = -2.0                          # reaches -0.6 m/s at the ends (the walk-by tracking must reject)
        while x < 2.0:
            yield x, 2.5
            x += 1.0 * dt
    elif name == "walk_away":             # 1 m -> 3.5 m at 0.8 m/s, slightly right
        r, az = 1.0, math.radians(15)
        while r < 3.5:
            yield r * math.sin(az), r * math.cos(az)
            r += 0.8 * dt
    elif name == "lateral_cal":           # slow left -> right sweep at 1.5 m (for the calibration doc)
        for k in range(int(6 * FPS)):
            a = math.radians(-40 + 80 * k / (6 * FPS))
            yield 1.5 * math.sin(a), 1.5 * math.cos(a)
    else:
        raise ValueError(name)


SCENARIOS = ("approach_left", "walk_past", "walk_away", "lateral_cal", "walk_by_line")


class Alert:
    """Mirror of radar-lab's AlertEngine: too_close 2 of 3, approaching 3 of 5, 0.6 s hold."""

    def __init__(self):
        self.state, self.hist, self.close = "calm", deque(maxlen=5), deque(maxlen=3)
        self.last_change, self.hold = 0.0, 0.6

    def update(self, tgt, now):
        self.hist.append(bool(tgt and tgt["speed"] < -APPROACH_MPS))
        self.close.append(bool(tgt and tgt["dist"] < ALERT_DIST))
        new = "too_close" if sum(self.close) >= 2 else "approaching" if sum(self.hist) >= 3 else "calm"
        rank = {"calm": 0, "approaching": 1, "too_close": 2}
        if new != self.state:
            if rank[new] > rank[self.state] or now - self.last_change > self.hold:
                old, self.state, self.last_change = self.state, new, now
                return old
        elif new != "calm":
            self.last_change = now
        return None


class FakeRadarServer:
    def __init__(self, port: int = 8091, schema: str = "legacy", info: str = "sim"):
        self.port, self.schema, self.info = port, schema, info
        self.clients: set = set()
        self.sent = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    # --- lifecycle (background thread, for tests) ---------------------------------------
    def start(self):
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve()), daemon=True)
        self._thread.start()
        self._ready.wait(5)
        return self

    def stop(self):
        if self.loop and self._stop:
            self.loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(5)

    async def _serve(self):
        self.loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
        async with serve(self._handler, "127.0.0.1", self.port):
            self._ready.set()
            await self._stop.wait()

    async def _handler(self, ws):
        if ws.request.path != "/ws":
            await ws.close(1008, "use /ws")
            return
        self.clients.add(ws)
        await ws.send(json.dumps(dict(type="hello", info=self.info,
                                      config=dict(alert_dist=ALERT_DIST, approach_mps=APPROACH_MPS, az_sign=1.0),
                                      axes=dict(range_max=6.3, speed_max=3.0, n_range=64, n_doppler=64))))
        try:
            async for m in ws:
                msg = json.loads(m)
                if msg.get("type") == "ping":
                    await ws.send(json.dumps(dict(type="pong", c=msg.get("c"))))
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

    async def _broadcast(self, msg):
        msg["t"] = time.time()                 # stamped right before send (latency reference)
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send(data)
                self.sent += 1
            except Exception:
                self.clients.discard(ws)

    # --- scenario playback --------------------------------------------------------------
    def play(self, name: str, tail_s: float = 1.5) -> None:
        """Blocking: play a scenario (plus `tail_s` of empty frames) from another thread."""
        asyncio.run_coroutine_threadsafe(self._play(name, tail_s), self.loop).result()

    async def _play(self, name, tail_s=1.5):
        alert = Alert()
        prev = None
        i = 0
        pts = list(scenario_points(name)) + [None] * int(tail_s * FPS)
        t_next = time.perf_counter()
        for p in pts:
            now = time.time()
            tgt = None
            if p is not None:
                x, y = p
                r = math.hypot(x, y)
                vr = 0.0 if prev is None else (r - math.hypot(*prev)) * FPS          # + = moving away
                vx, vy = (0.0, 0.0) if prev is None else ((x - prev[0]) * FPS, (y - prev[1]) * FPS)
                tgt = dict(dist=round(r, 3), speed=round(vr, 3), angle=round(math.degrees(math.atan2(x, y)), 1),
                           snr=30.0, x=x, y=y, vx=vx, vy=vy)
            prev = p
            old = alert.update(tgt, now)
            self._send_frame(i, tgt, alert, old, now)
            i += 1
            t_next += 1 / FPS
            await asyncio.sleep(max(0.0, t_next - time.perf_counter()))
            await asyncio.sleep(0)

    def _send_frame(self, i, tgt, alert, old, now):
        base = dict(type="frame", i=i, fps=FPS, info=self.info, state=alert.state, proc_ms=1.0)
        if self.schema == "legacy":
            msg = dict(base, target=tgt and {k: tgt[k] for k in ("dist", "speed", "angle", "snr")}, heat=None)
            if old is not None:
                msg["event"] = dict(t=now, frm=old, to=alert.state, dist=tgt and tgt["dist"], speed=tgt and tgt["speed"])
        else:
            tracks, prim = [], None
            if tgt:
                closing = -tgt["speed"]
                ttc = (tgt["dist"] - 0.5) / closing if closing > 0.05 and tgt["dist"] > 0.5 else None
                tracks = [dict(id=1, x=round(tgt["x"], 3), y=round(tgt["y"], 3), r=tgt["dist"], az_deg=tgt["angle"],
                               vr=tgt["speed"], closing=round(closing, 3), vx=round(tgt["vx"], 3),
                               vy=round(tgt["vy"], 3), status="confirmed" if i >= 5 else "tentative",
                               coasting=False, misses=0, confidence=1.0, snr_db=30.0)]
                if alert.state != "calm":
                    prim = dict(track=1, r=tgt["dist"], az_deg=tgt["angle"], closing=round(closing, 2), ttc=ttc,
                                where="left" if tgt["angle"] < -15 else "right" if tgt["angle"] > 15 else "ahead",
                                why=alert.state)
            urg = {"calm": 0.0, "approaching": 0.75, "too_close": 1.0}[alert.state]
            msg = dict(base, urgency=urg, primary=prim, since=now, tracks=tracks, heat=None)
            if old is not None:
                msg["event"] = dict(t=now, frm=old, to=alert.state, **(prim or {}))
        asyncio.ensure_future(self._broadcast(msg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--schema", choices=("legacy", "tracked"), default="legacy")
    ap.add_argument("--scenario", choices=SCENARIOS + ("all",), default="all")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()
    srv = FakeRadarServer(args.port, args.schema).start()
    print(f"fake radar: ws://localhost:{args.port}/ws  schema={args.schema}", flush=True)
    names = SCENARIOS if args.scenario == "all" else (args.scenario,)
    try:
        while True:
            for n in names:
                print(f"-> {n}", flush=True)
                srv.play(n)
            if not args.loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()

"""Offline checks of the v3 engine pieces (no Haiku, no server, no Bluetooth, no radar board):
radar hook (fake RadarSense injected as spidey.radar), pause, status/devices, gaze modes + notice memory,
temporal trend + hysteresis, back-compat of old triggers.

    .venv/bin/python backend/desk_test/test_engine_v3.py          # prints PASS/FAIL per check, exit 1 on failure
    .venv/bin/python backend/desk_test/test_engine_v3.py --live-radar ws://localhost:8091
        # + the real spidey/radar.py against backend/tests/fake_radar_server.py on a spare port
"""
from __future__ import annotations

import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from spidey import rules as R  # noqa: E402
from spidey.engine import Controller, make_thumb  # noqa: E402
from spidey.types import Detection, Frame  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + str(detail) if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


class RecSink:
    def __init__(self):
        self.calls = []

    def play(self, pattern, dot=None, intensity=None, rgb=None):
        self.calls.append(pattern)

    def close(self):
        pass


def controller(rules, cfg_extra=None, tmp=None):
    tmp = Path(tmp or tempfile.mkdtemp())
    p = tmp / "triggers.json"
    import json
    p.write_text(json.dumps({"rules": rules}))
    cfg = {"sink": {"type": "console"}, "detector": {"type": "vlm", "model": "claude-haiku-5-5"},
           "source": {"type": "fake"}, "_config_path": "/x/config.desk.sim.yaml", **(cfg_extra or {})}
    sink = RecSink()
    ctl = Controller(cfg, sink, "fake", rules_path=p, feedback_path=tmp / "fb.jsonl")
    return ctl, sink


RADAR_RULE = {"id": "someone_behind", "name": "Someone behind me", "kind": "custom", "source": "radar",
              "describe": "", "if": {"max_distance_m": 2.0, "min_speed_mps": 0.3}, "feel": "urgent",
              "cooldown_s": 3}


# ---- radar hook with an injected fake RadarSense --------------------------------------------------
def test_radar_stub():
    made = {}

    class FakeRadarSense:
        def __init__(self, url, on_target=None, side_sign=1):
            made.update(url=url, cb=on_target, side_sign=side_sign)

        def status(self):
            return {"connected": True, "url": made["url"], "mode": "sim", "last": None, "rate_hz": 17.0}

        def close(self):
            made["closed"] = True

    mod = types.ModuleType("spidey.radar")
    mod.RadarSense = FakeRadarSense
    sys.modules["spidey.radar"] = mod
    try:
        ctl, sink = controller([RADAR_RULE], {"radar": {"enabled": True, "url": "ws://localhost:8090"}})
        ctl.start_radar()
        check("radar: RadarSense constructed from cfg", made.get("url") == "ws://localhost:8090" and ctl.radar)
        cb, now = made["cb"], time.time()
        base = {"distance_m": 1.4, "speed_mps": 0.6, "angle_deg": -35, "side": "left", "ts": now, "confirmed": True}
        cb({**base, "state": "calm"})
        cb({**base, "state": "approaching", "confirmed": False})
        cb({**base, "state": "approaching", "distance_m": 2.6})
        cb({**base, "state": "approaching", "speed_mps": 0.1})
        check("radar: calm / unconfirmed / too far / too slow don't fire", sink.calls == [], sink.calls)
        out = []
        ctl.emit = (lambda orig: (lambda ev, det=None, t0=None: out.extend(orig(ev, det, t0)) or out))(ctl.emit)
        cb({**base, "state": "approaching"})
        check("radar: confirmed approaching 1.4 m @ 0.6 m/s fires", sink.calls == ["urgent"], sink.calls)
        ev = out[0] if out else {}
        check("radar: event fields", ev.get("sensor") == "radar" and ev.get("radar", {}).get("side") == "left"
              and ev.get("gaze_mode") == "unseen" and "thumb" not in ev, ev)
        check("radar: why text", ev.get("why") == "Person approaching from behind-left, 1.4 m at 0.6 m/s", ev.get("why"))
        cb({**base, "state": "too_close", "distance_m": 0.5})
        check("radar: cooldown holds", sink.calls == ["urgent"], sink.calls)
        st = ctl.status_msg()
        check("status: radar block from RadarSense.status()", st["radar"]["rate_hz"] == 17.0, st["radar"])
        card = next(d for d in ctl.devices() if d["id"] == "radar")
        check("devices: radar card connected", card["status"] == "connected", card)
        # pause: event still produced, flagged, sink untouched
        ctl.on_command({"name": "pause", "args": {"on": True}})
        for r in ctl.rules:
            r._last = -1e9
        out.clear()
        cb({**base, "state": "approaching"})
        check("pause: event flagged paused, nothing played", out and out[0].get("paused") and sink.calls == ["urgent"],
              (out, sink.calls))
        check("pause: status says paused", ctl.status_msg()["paused"] is True)
        ctl.on_command({"name": "pause", "args": {"on": False}})
    finally:
        del sys.modules["spidey.radar"]


def test_radar_import_guard():
    sys.modules["spidey.radar"] = None          # makes `from .radar import RadarSense` raise ImportError
    try:
        ctl, _ = controller([RADAR_RULE], {"radar": {"enabled": True}})
        ctl.start_radar()
        check("radar: missing module -> logged, engine continues", ctl.radar is None)
        check("status: radar null when not running", ctl.status_msg()["radar"] is None)
    finally:
        del sys.modules["spidey.radar"]


def test_status_and_backcompat():
    ctl, _ = controller([{"id": "old", "name": "old", "describe": "a knife", "if": {"unseen": True}}])
    r = ctl.rules[0]
    check("back-compat: if.unseen -> gaze_mode unseen", r.effective_gaze_mode == "unseen" and r.kind == "custom")
    d = r.to_dict()
    check("to_dict carries v3 fields", all(k in d for k in ("gaze_mode", "notice_memory_s", "temporal")), d)
    check("custom_triggers excludes radar/empty describe",
          R.custom_triggers(R.load_rules([RADAR_RULE, d])) == [("old", "a knife", False)])
    img = np.zeros((1200, 1600, 3), np.uint8)
    for i in range(10):
        ctl.note_frame(Frame(time.time(), img, (0.5, 0.5), True))
        time.sleep(0.02)
    st = ctl.status_msg()
    check("status: shape", st["type"] == "status" and {"paused", "source", "radar", "about"} <= set(st)
          and st["about"]["config"] == "config.desk.sim.yaml" and st["source"]["live"], st)
    neon = next(x for x in ctl.devices() if x["id"] == "neon")
    check("devices: neon card has stats {fps, gaze_hz, worn, battery, delay_ms}",
          {"fps", "gaze_hz", "worn", "battery", "delay_ms"} <= set(neon["stats"]), neon["stats"])
    check("hello carries status", "status" in ctl.hello_extra())
    thumb = make_thumb(Frame(time.time(), img, (0.5, 0.5), True), [0.1, 0.1, 0.3, 0.3])
    check("thumb: small base64 JPEG", thumb and len(thumb) < 20000, len(thumb or ""))


# ---- gaze modes + notice memory ---------------------------------------------------------------------
def det_with(tid, match, bbox, frame, **kw):
    return Detection(None, None, False, 0.0, raw={"frame_ts": frame.ts}, frame=frame,
                     triggers={tid: {"match": match, "urgency": 0.5, "bbox": bbox, **kw}})


def test_gaze_modes():
    look = R.rule_from_dict({"id": "look", "name": "look", "describe": "lion", "gaze_mode": "looking_at",
                             "cooldown_s": 0})
    uns = R.rule_from_dict({"id": "uns", "name": "uns", "describe": "lion", "gaze_mode": "unseen",
                            "notice_memory_s": 3, "cooldown_s": 0})
    mem = R.TriggerMemory()
    img = np.zeros((1200, 1600, 3), np.uint8)
    box = [0.65, 0.3, 0.85, 0.6]
    t0 = 1000.0

    def tick(t, gaze):
        f = Frame(t0 + t, img, gaze, True)
        mem.add_frame(f)
        d = Detection(None, None, False, 0.0, raw={"frame_ts": f.ts}, frame=f,
                      triggers={"look": {"match": True, "urgency": .5, "bbox": box},
                                "uns": {"match": True, "urgency": .5, "bbox": box}})
        mem.annotate(d, [look, uns], f.ts)
        return look.matches(d), uns.matches(d), d.triggers["uns"].get("looked_at_s_ago")

    res = {}
    for i in range(0, 120):                    # 12 s at 10 Hz: text 0-4, lion 4-7, text 7-12
        t = i / 10
        gaze = (0.75, 0.45) if 4 <= t < 7 else (0.2, 0.4)
        res[round(t, 1)] = tick(t, gaze)
    check("looking_at: quiet while looking elsewhere", res[2.0][0] is False)
    check("looking_at: fires while looking at it", res[5.0][0] is True)
    check("unseen: fires when never looked", res[2.0][1] is True)
    check("unseen: quiet while looking", res[5.0][1] is False)
    check("unseen: notice memory holds 3 s after looking away", res[8.0][1] is False and res[9.9][1] is False,
          (res[8.0], res[9.9]))
    check("unseen: fires again after memory expired", res[10.5][1] is True, res[10.5])


# ---- temporal trend ---------------------------------------------------------------------------------
def test_trend():
    r = R.rule_from_dict({"id": "w", "name": "w", "describe": "person to water", "temporal": True,
                          "gaze_mode": "any", "trend": {"ticks": 3, "sensitivity": "medium"}, "cooldown_s": 0})
    mem = R.TriggerMemory()
    img = np.zeros((1200, 1600, 3), np.uint8)
    water = [0.7, 0.4, 1.0, 1.0]
    fires = []

    def tick(t, x, ordinal):
        f = Frame(2000 + t, img, (0.1, 0.1), True)
        mem.add_frame(f)
        d = Detection(None, None, False, 0.0, raw={"frame_ts": f.ts}, frame=f, triggers={"w": {
            "match": True, "urgency": .4, "subject_bbox": [x, 0.4, x + 0.05, 0.7], "target_bbox": water,
            "ordinal": ordinal, "touching": False}})
        mem.annotate(d, [r], f.ts)
        if r.check(d, f.ts):
            fires.append(round(x, 2))
            mem.fired("w")

    xs = [0.1 + 0.04 * i for i in range(14)]          # walks right to the water (gap ~1.4 -> 0 body-heights)
    for i, x in enumerate(xs):
        tick(i * 0.7, x, "closer")
    for j, x in enumerate(reversed(xs)):              # walks back (Haiku still says "closer": cross-checked)
        tick((14 + j) * 0.7, x, "closer")
    check("trend: fires once on the approach, near the water", len(fires) == 1 and fires[0] >= 0.4, fires)


def live_radar(url):
    """Real spidey/radar.py against backend/tests/fake_radar_server.py (start it first on a spare port)."""
    ctl, sink = controller([RADAR_RULE], {"radar": {"enabled": True, "url": url}})
    ctl.start_radar()
    t_end = time.time() + 20
    while time.time() < t_end and not sink.calls:
        time.sleep(0.2)
    st = ctl.radar_status()
    check("live radar: connected + fired on the fake server's approach", bool(sink.calls) and st.get("connected"),
          (sink.calls, st))
    ctl.radar.close()


if __name__ == "__main__":
    test_radar_stub()
    test_radar_import_guard()
    test_status_and_backcompat()
    test_gaze_modes()
    test_trend()
    if "--live-radar" in sys.argv:
        live_radar(sys.argv[sys.argv.index("--live-radar") + 1])
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)

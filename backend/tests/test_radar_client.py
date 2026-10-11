"""RadarSense against the fake radar-lab server (both message schemas).

    cd backend && ../.venv/bin/python -m unittest tests.test_radar_client -v      (or pytest, if installed)
"""
from __future__ import annotations

import statistics
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_radar_server import FakeRadarServer  # noqa: E402
from spidey.radar import RadarSense, side_of, ws_url  # noqa: E402

PORT = 8091
LATENCIES_MS: list[float] = []


def wait_for(cond, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Base:
    schema = "legacy"
    port = PORT

    @classmethod
    def setUpClass(cls):
        cls.srv = FakeRadarServer(cls.port, cls.schema).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def run_scenario(self, name, side_sign=1):
        got, lock = [], threading.Lock()

        def on_target(u):
            lat = (time.time() - u["server_ts"]) * 1000 if u.get("server_ts") else None
            with lock:
                got.append(dict(u, lat_ms=lat))

        rs = RadarSense(f"ws://127.0.0.1:{self.port}", on_target=on_target, side_sign=side_sign)
        try:
            self.assertTrue(wait_for(lambda: rs.status()["connected"]), "client never connected")
            time.sleep(0.1)
            self.srv.play(name)
            time.sleep(0.2)
            st = rs.status()
        finally:
            rs.close()
        LATENCIES_MS.extend(g["lat_ms"] for g in got if g["lat_ms"] is not None)
        return got, st

    # (1) approach from the left 3 m -> 0.5 m at 0.6 m/s
    def test_approach_left(self):
        got, st = self.run_scenario("approach_left")
        states = [g["state"] for g in got]
        conf_appr = [g for g in got if g["state"] == "approaching" and g["confirmed"]]
        conf_close = [g for g in got if g["state"] == "too_close" and g["confirmed"]]
        self.assertTrue(conf_appr, f"no confirmed approach: {states}")
        self.assertTrue(conf_close, f"no confirmed too_close: {states}")
        self.assertLess(got.index(conf_appr[0]), got.index(conf_close[0]))
        a = conf_appr[0]
        self.assertEqual(a["side"], "left")
        self.assertAlmostEqual(a["speed_mps"], 0.6, delta=0.1)
        self.assertGreater(a["distance_m"], 1.5)          # caught early, not at the end
        self.assertEqual(states[-1], "calm")               # exactly one return-to-calm at the end
        self.assertEqual(states.count("calm"), 1)
        self.assertEqual(st["mode"], "sim")
        self.assertAlmostEqual(st["rate_hz"], 20, delta=4)
        print(f"\n  [{self.schema}] approach_left: first confirmed approach at {a['distance_m']:.2f} m "
              f"({a['speed_mps']:.2f} m/s, {a['side']}), too_close at {conf_close[0]['distance_m']:.2f} m, "
              f"{len(got)} callbacks, rate {st['rate_hz']} Hz")

    # flipping side_sign mirrors the side
    def test_side_sign(self):
        got, _ = self.run_scenario("approach_left", side_sign=-1)
        self.assertTrue(got and all(g["side"] == "right" for g in got if g["state"] != "calm"))

    # (2) tangential walk past at 2.5 m (radial speed ~0)
    def test_walk_past(self):
        got, _ = self.run_scenario("walk_past")
        self.assertFalse([g for g in got if g["state"] == "approaching" and g["confirmed"]], got[:3])

    # (3) walking away
    def test_walk_away(self):
        got, _ = self.run_scenario("walk_away")
        self.assertEqual(got, [])


class TestLegacySchema(Base, unittest.TestCase):
    schema = "legacy"
    port = PORT


class TestTrackedSchema(Base, unittest.TestCase):
    schema = "tracked"
    port = PORT + 1


class TestClientBasics(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(ws_url("ws://localhost:8090"), "ws://localhost:8090/ws")
        self.assertEqual(ws_url("ws://h:1/ws"), "ws://h:1/ws")
        self.assertEqual(side_of(5), "center")
        self.assertEqual(side_of(-12), "left")
        self.assertEqual(side_of(12), "right")

    def test_no_server_then_reconnect(self):
        port = PORT + 2
        t0 = time.time()
        rs = RadarSense(f"ws://127.0.0.1:{port}")
        self.assertLess(time.time() - t0, 0.2)            # __init__ never blocks
        try:
            time.sleep(1.5)
            s = rs.status()
            self.assertFalse(s["connected"])
            self.assertIsNone(s["last"])
            srv = FakeRadarServer(port).start()
            try:
                self.assertTrue(wait_for(lambda: rs.status()["connected"], 7), "no reconnect within backoff")
            finally:
                srv.stop()
            self.assertTrue(wait_for(lambda: not rs.status()["connected"], 5))
        finally:
            rs.close()


def tearDownModule():
    if LATENCIES_MS:
        xs = sorted(LATENCIES_MS)
        print(f"\nlatency server send -> on_target: n={len(xs)} median {statistics.median(xs):.2f} ms, "
              f"p95 {xs[int(0.95 * (len(xs) - 1))]:.2f} ms, max {xs[-1]:.2f} ms")
        assert statistics.median(xs) < 20, "median latency too high"


if __name__ == "__main__":
    unittest.main(verbosity=2)

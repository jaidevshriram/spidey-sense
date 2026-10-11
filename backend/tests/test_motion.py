"""cd backend && ../.venv/bin/python -m unittest tests.test_motion -v"""
import math
import random
import unittest

from spidey.motion import WalkDetector

HZ = 110


def feed(w, secs, accel_fn, gyro_fn=lambda t: (0.0, 0.0, 0.0), t0=0.0):
    """Feed `secs` of IMU at 110 Hz; returns the state at the end and the walking verdict over time."""
    out = []
    for i in range(int(secs * HZ)):
        t = t0 + i / HZ
        w.add(t, accel_fn(t), gyro_fn(t))
        out.append((t, w.state(t)))
    return out[-1][1], out


def still(t, rnd=random.Random(1)):
    return (rnd.gauss(0, 0.01), rnd.gauss(0, 0.01), 1.0 + rnd.gauss(0, 0.01))


def walking(cadence_spm=108, amp=0.15):
    rnd = random.Random(2)
    f = cadence_spm / 60.0

    def a(t):
        return (rnd.gauss(0, 0.03), rnd.gauss(0, 0.03), 1.0 + amp * math.sin(2 * math.pi * f * t) + rnd.gauss(0, 0.03))
    return a


class TestWalk(unittest.TestCase):
    def test_no_data_is_unknown(self):
        self.assertIsNone(WalkDetector().state(0.0)["walking"])

    def test_still_is_not_walking(self):
        st, _ = feed(WalkDetector(), 5, still)
        self.assertFalse(st["walking"])

    def test_walking_detected_with_cadence(self):
        st, hist = feed(WalkDetector(), 6, walking(108))
        self.assertTrue(st["walking"])
        self.assertAlmostEqual(st["cadence_spm"], 108, delta=8)
        first = next(t for t, s in hist if s["walking"])
        self.assertLess(first, 4.0)                       # window fills (2 s) + hysteresis (0.8 s)

    def test_fast_and_slow_walks(self):
        for cad in (80, 150):
            st, _ = feed(WalkDetector(), 6, walking(cad))
            self.assertTrue(st["walking"], cad)
            self.assertAlmostEqual(st["cadence_spm"], cad, delta=12)

    def test_single_nod_is_not_walking(self):
        rnd = random.Random(3)

        def nod(t):
            bump = 0.4 if 2.0 <= t < 2.2 else 0.0
            return (rnd.gauss(0, 0.01), rnd.gauss(0, 0.01), 1.0 + bump + rnd.gauss(0, 0.01))
        _, hist = feed(WalkDetector(), 5, nod)
        self.assertFalse(any(s["walking"] for _, s in hist))

    def test_random_vibration_is_not_walking(self):
        rnd = random.Random(4)
        _, hist = feed(WalkDetector(), 5, lambda t: (0.0, 0.0, 1.0 + rnd.gauss(0, 0.08)))
        self.assertFalse(any(s["walking"] for _, s in hist))

    def test_stops_walking_after_off_delay(self):
        w = WalkDetector()
        feed(w, 6, walking(108))
        st, hist = feed(w, 5, still, t0=6.0)
        self.assertFalse(st["walking"])
        stop = next(t for t, s in hist if not s["walking"])
        self.assertGreater(stop, 6.5)                     # not on the first quiet sample

    def test_turning_from_gyro(self):
        st, _ = feed(WalkDetector(), 1, still, gyro_fn=lambda t: (0.0, 90.0, 0.0))
        self.assertTrue(st["turning"])
        st, _ = feed(WalkDetector(), 1, still, gyro_fn=lambda t: (0.0, 10.0, 0.0))
        self.assertFalse(st["turning"])

    def test_stalled_stream_is_unknown(self):
        w = WalkDetector()
        feed(w, 3, walking(108))
        self.assertIsNone(w.state(10.0)["walking"])


if __name__ == "__main__":
    unittest.main()

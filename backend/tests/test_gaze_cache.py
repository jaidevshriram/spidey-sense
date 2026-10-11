"""cd backend && ../.venv/bin/python -m unittest tests.test_gaze_cache -v"""
import unittest

import numpy as np

from spidey.gaze_cache import GazeCache

LION = (0.6, 0.3, 0.8, 0.6)
T = [("look", "looking_at", 3.0), ("unseen", "unseen", 3.0)]


def run(cache, samples, t0=0.0, dt=1 / 155):
    """samples: list of (x, y, n) -> n gaze samples at 155 Hz. Returns {trigger: first fire time}."""
    fired, t = {}, t0
    for x, y, n in samples:
        for _ in range(n):
            for d in cache.on_gaze(x, y, t, T):
                if d.fire and d.trigger_id not in fired:
                    fired[d.trigger_id] = round(t - t0, 3)
            t += dt
    return fired, t


class TestGazeCache(unittest.TestCase):
    def setUp(self):
        self.c = GazeCache()
        for tid, _, _ in T:
            self.c.update(tid, LION, 0.0, True, "lion")

    def test_looking_at_fires_after_dwell(self):
        fired, _ = run(self.c, [(0.2, 0.5, 30), (0.7, 0.45, 60)])       # 0.19 s elsewhere, then on the lion
        self.assertIn("look", fired)
        self.assertAlmostEqual(fired["look"], 30 / 155 + 0.2, delta=0.02)  # ~0.2 s after entering the box

    def test_unseen_fires_after_grace_when_not_looked(self):
        fired, _ = run(self.c, [(0.2, 0.5, 80)])
        self.assertIn("unseen", fired)
        self.assertNotIn("look", fired)
        self.assertAlmostEqual(fired["unseen"], 0.3, delta=0.02)

    def test_unseen_quiet_while_looking_and_during_notice_memory(self):
        c = GazeCache(ttl_s=10)
        for tid, _, _ in T:
            c.update(tid, LION, 0.0, True)
        fired, t = run(c, [(0.7, 0.45, 155), (0.2, 0.5, 155 * 2)])     # look 1 s, then away 2 s (< 3 s memory)
        self.assertNotIn("unseen", fired)
        fired2, _ = run(c, [(0.2, 0.5, 155 * 2)], t0=t)                # memory expires -> fires
        self.assertIn("unseen", fired2)

    def test_stale_box_never_fires(self):
        fired, _ = run(self.c, [(0.7, 0.45, 10)], t0=2.0)
        self.assertEqual(fired, {})

    def test_not_matched_drops_box(self):
        self.c.update("look", None, 0.5, False)
        fired, _ = run(self.c, [(0.7, 0.45, 60)])
        self.assertNotIn("look", fired)

    def test_head_motion_invalidates(self):
        a = np.zeros((24, 32), np.uint8)
        self.c.motion(a, 0.0)
        self.assertTrue(self.c.motion(a + 80, 0.1))
        fired, _ = run(self.c, [(0.7, 0.45, 60)], t0=0.1)
        self.assertEqual(fired, {})


if __name__ == "__main__":
    unittest.main()

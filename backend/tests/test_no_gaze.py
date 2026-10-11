"""No gaze (glasses off / tracking lost) = the user isn't looking at anything.
cd backend && ../.venv/bin/python -m unittest tests.test_no_gaze -v"""
import unittest

import numpy as np

from spidey.gaze_cache import GazeCache
from spidey.rules import TriggerMemory
from spidey.types import Frame

IMG = np.zeros((120, 160, 3), np.uint8)
LION = (0.6, 0.3, 0.8, 0.6)


class TestNoGaze(unittest.TestCase):
    def test_newest_gaze_none_once_gaze_is_lost(self):
        m = TriggerMemory()
        m.add_frame(Frame(0.0, IMG, (0.7, 0.45), True))
        self.assertIsNotNone(m.newest_gaze())
        m.add_frame(Frame(0.5, IMG, None, True))          # 0.5 s without gaze
        self.assertIsNone(m.newest_gaze())

    def test_cache_off_image_gaze_fires_unseen_never_looking_at(self):
        c = GazeCache()
        c.update("look", LION, 0.0, True, "lion")
        c.update("unseen", LION, 0.0, True, "lion")
        fired = set()
        for i in range(80):                               # the engine passes (-1, -1) when gaze is None
            for d in c.on_gaze(-1.0, -1.0, i / 155, [("look", "looking_at", 3.0), ("unseen", "unseen", 3.0)]):
                if d.fire:
                    fired.add(d.trigger_id)
        self.assertEqual(fired, {"unseen"})


if __name__ == "__main__":
    unittest.main()

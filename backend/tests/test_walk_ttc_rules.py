"""Rule options only_while_walking / max_ttc_s and time-to-contact from box growth.
cd backend && ../.venv/bin/python -m unittest tests.test_walk_ttc_rules -v"""
import unittest

import numpy as np

from spidey.rules import TriggerMemory, load_rules, time_to_contact
from spidey.types import Detection, Frame

IMG = np.zeros((120, 160, 3), np.uint8)


def box(cx, cy, size):
    return [cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2]


def det(ts, b, match=True):
    return Detection(None, None, False, 0.0, 0.0, {"frame_ts": ts},
                     triggers={"obs": {"match": match, "urgency": 0.6, "bbox": b}})


def rule(**cond):
    return load_rules([{"id": "obs", "name": "obs", "kind": "custom", "describe": "obstacle",
                        "gaze_mode": "any", "if": {"min_urgency": 0.3, **cond}, "cooldown_s": 0}])


class TestTTC(unittest.TestCase):
    def test_growing_box_gives_time_to_contact(self):
        # size 0.2 -> 0.3 in 0.5 s: tau = 0.3 * 0.5 / 0.1 = 1.5 s
        self.assertAlmostEqual(time_to_contact([(0.0, box(0.5, 0.6, 0.2))], 0.5, box(0.5, 0.6, 0.3)), 1.5, places=2)

    def test_not_growing_or_unrelated_is_none(self):
        self.assertIsNone(time_to_contact([(0.0, box(0.5, 0.6, 0.3))], 0.5, box(0.5, 0.6, 0.3)))   # same size
        self.assertIsNone(time_to_contact([(0.0, box(0.5, 0.6, 0.3))], 0.5, box(0.5, 0.6, 0.2)))   # shrinking
        self.assertIsNone(time_to_contact([(0.0, box(0.1, 0.1, 0.1))], 0.5, box(0.8, 0.8, 0.2)))   # other thing
        self.assertIsNone(time_to_contact([], 0.5, box(0.5, 0.6, 0.3)))

    def test_memory_writes_ttc_aged_to_now(self):
        m, rules = TriggerMemory(), rule()
        for ts, size in ((0.0, 0.2), (0.5, 0.3)):
            m.add_frame(Frame(ts, IMG, None, True))
            d = det(ts, box(0.5, 0.6, size))
            m.annotate(d, rules, ts + 0.2)                   # result arrives 0.2 s after the frame
        self.assertAlmostEqual(d.triggers["obs"]["ttc_s"], 1.3, places=2)


class TestRuleOptions(unittest.TestCase):
    def fire(self, rules, ctx, ttc=None):
        d = det(1.0, box(0.5, 0.6, 0.3))
        if ttc is not None:
            d.triggers["obs"]["ttc_s"] = ttc
        ev, rec = rules[0].decide(d, 1.1, ctx)
        return ev is not None, rec["why"]

    def test_only_while_walking(self):
        r = rule(only_while_walking=True)
        self.assertEqual(self.fire(r, {"walking": False}), (False, "not walking (only while walking)"))
        self.assertTrue(self.fire(r, {"walking": True})[0])
        self.assertTrue(self.fire(r, {"walking": None})[0])            # unknown (no IMU) never blocks
        self.assertTrue(self.fire(rule(), {"walking": False})[0])      # option off: walking doesn't matter

    def test_max_ttc(self):
        r = rule(max_ttc_s=2.0)
        self.assertFalse(self.fire(r, {})[0])                          # no estimate yet
        self.assertFalse(self.fire(r, {}, ttc=3.5)[0])
        ok, _ = self.fire(rule(max_ttc_s=2.0), {}, ttc=1.2)
        self.assertTrue(ok)

    def test_ttc_in_event(self):
        d = det(1.0, box(0.5, 0.6, 0.3))
        d.triggers["obs"]["ttc_s"] = 1.2
        ev, _ = rule(only_while_walking=True)[0].decide(d, 1.1, {})
        self.assertEqual(ev.extra["ttc_s"], 1.2)
        self.assertIn("about 1.2 s away", ev.why)
        ev, _ = rule()[0].decide(det(1.0, box(0.5, 0.6, 0.3)) if False else d, 1.1 + 10, {})
        self.assertNotIn("s away", ev.why)               # not an approach-type trigger: no distance-in-time talk


if __name__ == "__main__":
    unittest.main()

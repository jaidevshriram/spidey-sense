"""Oura cloud heart-rate backend: parsing, token selection, polling into HeartRateSense (HTTP mocked).
cd backend && ../.venv/bin/python -m unittest tests.test_oura_cloud -v"""
import time
import unittest
from unittest import mock

from spidey import heart as H

PAYLOAD = {"data": [{"bpm": 72, "source": "awake", "timestamp": "2026-10-11T10:05:00+00:00"},
                    {"bpm": 70, "source": "rest", "timestamp": "2026-10-11T10:00:00.000Z"},
                    {"bpm": "bad", "timestamp": "nope"}], "next_token": None}


class TestOuraCloud(unittest.TestCase):
    def test_parse_sorted_and_skips_bad_rows(self):
        rows = H.parse_oura_heartrate(PAYLOAD)
        self.assertEqual([r[1] for r in rows], [70.0, 72.0])
        self.assertEqual(rows[1][2], "awake")

    def test_token_from_env_wins(self):
        with mock.patch.dict("os.environ", {"OURA_TOKEN": " abc "}):
            self.assertEqual(H.oura_cloud_token(env_file=H.REPO / "nonexistent.env"), "abc")

    def test_no_token_is_auth_error(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(H, "TOKEN_FILE", H.REPO / "nonexistent_token.json"):
            with self.assertRaises(H.OuraAuthError):
                H.oura_cloud_token(env_file=H.REPO / "nonexistent.env")

    def test_poll_emits_new_readings_once(self):
        now = time.time()
        rows = [(now - 600, 70.0, "rest"), (now - 300, 72.0, "awake")]
        got = []
        with mock.patch.object(H, "oura_cloud_token", return_value="t"), \
                mock.patch.object(H, "oura_cloud_fetch", return_value=rows):
            hs = H.HeartRateSense("oura_cloud", on_sample=got.append, poll_s=0.05)
            time.sleep(0.3)
            hs.close()
        self.assertEqual([s["bpm"] for s in got], [70.0, 72.0])     # polled several times, emitted once
        st = hs.status()
        self.assertTrue(st["connected"])
        self.assertIn("Oura cloud · last reading 5 min ago", st["detail"])
        self.assertEqual(H.oura_card(st)["status"], "connected")

    def test_auth_failure_reports_disconnected(self):
        with mock.patch.object(H, "oura_cloud_token", side_effect=H.OuraAuthError("no token")):
            hs = H.HeartRateSense("oura_cloud", poll_s=0.05)
            time.sleep(0.15)
            hs.close()
        self.assertFalse(hs.status()["connected"])
        self.assertIn("no token", hs.status()["detail"])

    def _rule(self):
        return {"id": "hr", "name": "Heart racing", "source": "heart_rate", "feel": "pulse",
                "if": {"min_delta_bpm": 15}, "cooldown_s": 60}

    def test_cloud_single_fresh_reading_fires(self):
        now = time.time()
        sample = {"ts": now - 300, "bpm": 92.0, "median_bpm": 70.0, "baseline_ready": True, "backend": "oura_cloud"}
        _, fire = H.HeartTriggers.step_rule(self._rule(), sample, H.HeartState())
        self.assertIsNotNone(fire)
        self.assertIn("over your usual 70", fire["why"])

    def test_cloud_history_never_fires(self):
        sample = {"ts": time.time() - 3 * 3600, "bpm": 120.0, "median_bpm": 70.0, "baseline_ready": True,
                  "backend": "oura_cloud"}
        _, fire = H.HeartTriggers.step_rule(self._rule(), sample, H.HeartState())
        self.assertIsNone(fire)


if __name__ == "__main__":
    unittest.main()

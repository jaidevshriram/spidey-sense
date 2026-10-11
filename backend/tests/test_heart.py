"""Heart-rate sense: trigger logic, replay/fake backends, and the open_oura subprocess path (against a fake CLI).
No Bluetooth, no ring.

    cd backend && ../.venv/bin/python -m unittest tests.test_heart -v
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from spidey.heart import (HeartParams, HeartRateSense, HeartState, HeartTriggers, HeartWindow,  # noqa: E402
                          fake_series, heart_step, load_csv, oura_command, oura_settings, parse_live_hr_line,
                          write_temp_key)

DATA = HERE / "data"
SPIKE, FLAT = DATA / "hr_spike.csv", DATA / "hr_flat.csv"
RULE = {"id": "heart_racing", "name": "Heart racing", "enabled": True, "source": "heart_rate", "feel": "pulse",
        "if": {"min_delta_bpm": 15, "min_delta_pct": 20, "sustain_s": 8}, "cooldown_s": 60}


def run_series(series, rule=RULE, window=None):
    """Feed (t, bpm) through the window + triggers synchronously (no threads). Returns (fires, samples)."""
    w, trig, fires, samples = window or HeartWindow(), HeartTriggers(), [], []
    for t, b in series:
        s = {"ts": float(t), "bpm": b, **w.add(float(t), b)}
        samples.append(s)
        fires += trig.evaluate([rule], s)
    return fires, samples


def wait_for(cond, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


class TestRule(unittest.TestCase):
    def test_spike_csv_fires_exactly_once_with_timing(self):
        fires, _ = run_series(load_csv(SPIKE))
        self.assertEqual(len(fires), 1, fires)
        f = fires[0]
        # the spike starts at t=150 (ramp 150-153); smoothed bpm crosses the threshold at ~154; +8 s sustain
        self.assertGreaterEqual(f["ts"], 158)
        self.assertLessEqual(f["ts"], 166)
        h = f["heart"]
        self.assertGreaterEqual(h["delta_bpm"], 15)
        self.assertGreaterEqual(h["above_s"], 8)
        self.assertAlmostEqual(h["median_bpm"], 70, delta=3)
        self.assertGreater(h["bpm"], 95)
        self.assertEqual(f["pattern"], "pulse")
        self.assertEqual(f["rule"], "heart_racing")
        self.assertIn("HR", f["why"])

    def test_flat_csv_never_fires(self):
        fires, samples = run_series(load_csv(FLAT))
        self.assertEqual(fires, [])
        self.assertAlmostEqual(samples[-1]["median_bpm"], 70, delta=2)

    def test_short_spike_below_sustain_does_not_fire(self):
        series = [(t, 70.0) for t in range(60)] + [(t, 105.0) for t in range(60, 66)] + \
                 [(t, 70.0) for t in range(66, 120)]
        fires, _ = run_series(series)
        self.assertEqual(fires, [])

    def test_cooldown_and_rearm(self):
        def spikes(gap):
            s, t = [], 0
            for _ in range(2):
                s += [(t + i, 70.0) for i in range(gap)]
                t += gap
                s += [(t + i, 105.0) for i in range(15)]
                t += 15
            return s + [(t + i, 70.0) for i in range(40)]
        # spikes 45 s apart (start to start): inside the 60 s cooldown -> one fire
        self.assertEqual(len(run_series(spikes(30))[0]), 1)
        # 95 s apart: cooldown over and re-armed in between -> two fires
        self.assertEqual(len(run_series(spikes(80))[0]), 2)

    def test_no_rearm_while_still_above(self):
        """A long excursion fires once even after the cooldown passes, until bpm drops below the threshold."""
        series = [(t, 70.0) for t in range(60)] + [(t, 105.0) for t in range(60, 160)]
        fires, _ = run_series(series, window=HeartWindow(window_s=600))   # long window: median stays at rest
        self.assertEqual(len(fires), 1)

    def test_warmup_blocks_fires(self):
        fires, _ = run_series([(t, 70.0) for t in range(10)] + [(t, 110.0) for t in range(10, 25)])
        self.assertEqual(fires, [])

    def test_percent_threshold(self):
        p = HeartParams(min_delta_bpm=40, min_delta_pct=10, sustain_s=0)
        self.assertAlmostEqual(p.threshold(100), 110)
        st, fire = heart_step(HeartState(), 0, 111, 100, p)
        self.assertIsNotNone(fire)
        self.assertEqual(fire["delta_bpm"], 11)
        st, fire = heart_step(HeartState(), 0, 109, 100, p)
        self.assertIsNone(fire)

    def test_step_is_pure(self):
        s0 = HeartState()
        p = HeartParams(sustain_s=0)
        s1, f1 = heart_step(s0, 10, 100, 70, p)
        s1b, f1b = heart_step(s0, 10, 100, 70, p)
        self.assertEqual((s1, f1), (s1b, f1b))
        self.assertEqual(s0, HeartState())

    def test_gap_restarts_sustain(self):
        p = HeartParams(sustain_s=8)
        st, _ = heart_step(HeartState(), 0, 100, 70, p)
        st, f = heart_step(st, 30, 100, 70, p)          # 30 s gap: excursion restarts at 30
        self.assertIsNone(f)
        st, f = heart_step(st, 38, 100, 70, p)
        self.assertIsNotNone(f)

    def test_params_from_rule(self):
        p = HeartParams.from_rule({"hr_band_bpm": 12}, 30)
        self.assertEqual((p.min_delta_bpm, p.min_delta_pct, p.sustain_s, p.cooldown_s), (12, 20, 8, 30))
        p = HeartParams.from_rule({"min_delta_bpm": "x", "sustain_s": -1}, None)
        self.assertEqual((p.min_delta_bpm, p.sustain_s, p.cooldown_s), (15, 8, 60))

    def test_disabled_and_other_sources_ignored(self):
        w, trig = HeartWindow(), HeartTriggers()
        rules = [dict(RULE, enabled=False), dict(RULE, id="cam", source="neon")]
        for t, b in load_csv(SPIKE):
            self.assertEqual(trig.evaluate(rules, {"ts": t, "bpm": b, **w.add(t, b)}), [])

    def test_rule_objects_work(self):
        from spidey.rules import rule_from_dict
        fires, _ = run_series(load_csv(SPIKE), rule=rule_from_dict(RULE))
        self.assertEqual(len(fires), 1)

    def test_fake_series_spike(self):
        fires, _ = run_series(fake_series(duration_s=200, spike_at_s=100, seed=1))
        self.assertEqual(len(fires), 1)
        self.assertTrue(100 <= fires[0]["ts"] <= 116, fires[0]["ts"])
        self.assertEqual(run_series(fake_series(duration_s=300, seed=2))[0], [])


class TestEngineRules(unittest.TestCase):
    """rules.Rule.check_heart / apply_heart (the engine path) and the Controller hook, with no server."""

    def test_apply_heart_spike(self):
        from spidey import rules as R
        rules = R.load_rules([RULE, {"id": "cam", "name": "cam", "feel": "urgent"}])
        w, events = HeartWindow(), []
        for t, b in load_csv(SPIKE):
            events += R.apply_heart(rules, {"ts": t, "bpm": b, **w.add(t, b)}, now=t)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual((e.sensor, e.pattern, e.rule), ("heart", "pulse", "heart_racing"))
        self.assertEqual(set(e.extra["heart"]), {"bpm", "median_bpm", "delta_bpm", "above_s"})
        self.assertTrue(158 <= e.ts <= 166)

    def test_heart_rules_never_match_camera(self):
        from spidey import rules as R
        from spidey.types import Detection
        r = R.rule_from_dict(RULE)
        self.assertFalse(r.matches(Detection(hazard="bike", where="left", seen_by_user=False, urgency=1.0)))
        self.assertEqual(R.custom_triggers([r]), [])

    def test_controller_hook_emits_heart_event(self):
        from spidey.engine import Controller
        from spidey.sinks import make_sink

        class Srv:
            clients, history = [], []
            def __init__(self): self.published = []
            def publish(self, ev): self.published.append(ev)
            def broadcast(self, m): pass

        with tempfile.TemporaryDirectory() as d:
            cfg = {"sink": {"type": "console"}, "source": {"type": "fake"},
                   "heart": {"enabled": True, "backend": "replay", "file": str(SPIKE), "speed": 400}}
            ctl = Controller(cfg, make_sink({"type": "console"}), "fake", rules_path=Path(d) / "t.json",
                             feedback_path=Path(d) / "fb.jsonl")
            ctl.on_upsert({"rule": RULE})
            ctl.server = srv = Srv()
            self.assertEqual({c["id"]: c for c in ctl.devices()}["oura"]["status"], "off")
            ctl.start_heart()
            try:
                self.assertTrue(ctl.heart.done.wait(10))
                card = {c["id"]: c for c in ctl.devices()}["oura"]
            finally:
                ctl.heart.close()
        self.assertEqual(len(srv.published), 1, srv.published)
        ev = srv.published[0]
        self.assertEqual((ev["sensor"], ev["trigger"], ev["pattern"]), ("heart", "heart_racing", "pulse"))
        self.assertGreaterEqual(ev["heart"]["delta_bpm"], 15)
        self.assertEqual(card["backend"], "replay")
        self.assertIsNotNone(card["median_bpm"])


class TestThreadedBackends(unittest.TestCase):
    def collect(self, backend, **opts):
        got, lock = [], threading.Lock()

        def on_sample(s):
            with lock:
                got.append(s)

        hs = HeartRateSense(backend, on_sample=on_sample, **opts)
        return hs, got

    def test_replay_accelerated_spike(self):
        trig, fires = HeartTriggers(), []
        hs = HeartRateSense("replay", on_sample=lambda s: fires.extend(trig.evaluate([RULE], s)),
                            file=str(SPIKE), speed=200, t0=0.0)
        try:
            self.assertTrue(hs.done.wait(10), "replay never finished")
            st = hs.status()
        finally:
            hs.close()
        self.assertEqual(len(fires), 1)
        self.assertTrue(158 <= fires[0]["ts"] <= 166, fires[0]["ts"])    # data time, unaffected by speed
        self.assertEqual(st["backend"], "replay")
        self.assertEqual(st["samples"], 241)
        self.assertIsNotNone(st["median_bpm"])
        self.assertIn("finished", st["detail"])

    def test_replay_flat_accelerated(self):
        hs, got = self.collect("replay", file=str(FLAT), speed=300)
        try:
            self.assertTrue(hs.done.wait(10))
        finally:
            hs.close()
        trig = HeartTriggers()
        self.assertEqual(sum(len(trig.evaluate([RULE], s)) for s in got), 0)
        self.assertEqual(len(got), 301)

    def test_replay_real_time_pacing_and_status(self):
        hs, got = self.collect("replay", file=str(SPIKE), speed=10)
        try:
            self.assertTrue(wait_for(lambda: len(got) >= 6, 3))
            st = hs.status()
            self.assertTrue(st["connected"])
            self.assertLess(st["since_last_s"], 1.0)
            self.assertGreater(st["rate_hz"], 0)
            self.assertLess(len(got), 40)               # paced, not dumped: ~10 samples/s
        finally:
            hs.close()
        self.assertFalse(hs._thread.is_alive())

    def test_replay_loop(self):
        hs, got = self.collect("replay", file=str(FLAT), speed=2000, loop=True)
        try:
            self.assertTrue(wait_for(lambda: len(got) > 400, 5))
        finally:
            hs.close()
        ts = [s["ts"] for s in got]
        self.assertEqual(ts, sorted(ts))                # loops keep time moving forward

    def test_fake_backend(self):
        hs, got = self.collect("fake", speed=100, spike_at_s=60, seed=3, duration_s=150)
        try:
            self.assertTrue(hs.done.wait(10))
        finally:
            hs.close()
        trig = HeartTriggers()
        fires = [f for s in got for f in trig.evaluate([RULE], s)]
        self.assertEqual(len(fires), 1)

    def test_bad_inputs_never_raise(self):
        for backend, opts in (("replay", {}), ("replay", {"file": "/nope.csv"}), ("bogus", {})):
            hs = HeartRateSense(backend, **opts)
            hs.done.wait(3)
            st = hs.status()
            hs.close()
            self.assertFalse(st["connected"])
            self.assertTrue(st["detail"])

    def test_on_sample_errors_are_caught(self):
        def boom(_):
            raise RuntimeError("boom")
        hs = HeartRateSense("replay", on_sample=boom, file=str(FLAT), speed=1000)
        self.assertTrue(hs.done.wait(5))
        self.assertEqual(hs.status()["samples"], 301)
        hs.close()


class TestOpenOura(unittest.TestCase):
    """The open_oura backend against tests/fake_oura.py (no Bluetooth). Never uses the real .env."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.bin = d / "oura"
        self.bin.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{HERE / 'fake_oura.py'}' \"$@\"\n")
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IXUSR)
        self.env = d / "env"
        self.runs = d / "runs.txt"
        self.db = str(d / "oura.db")
        self.saved = {k: os.environ.get(k) for k in ("FAKE_OURA_BPMS", "FAKE_OURA_RC", "FAKE_OURA_RUNS",
                                                      "OURA_AUTH_KEY", "OURA_KEY_FILE", "OURA_BIN")}
        for k in ("OURA_AUTH_KEY", "OURA_KEY_FILE", "OURA_BIN"):
            os.environ.pop(k, None)
        os.environ["FAKE_OURA_RUNS"] = str(self.runs)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_parse_live_hr_line(self):
        self.assertEqual(parse_live_hr_line("  72 bpm (IBI 833 ms)"), (72.0, 833))
        self.assertEqual(parse_live_hr_line("101 bpm"), (101.0, None))
        self.assertIsNone(parse_live_hr_line("Streaming live heart rate for 30s (Ctrl-C to stop early)..."))
        self.assertIsNone(parse_live_hr_line("No beats captured. Make sure the ring is worn."))
        self.assertIsNone(parse_live_hr_line("  999 bpm (IBI 60 ms)"))

    def test_settings_from_env_file_and_command(self):
        self.env.write_text(f"ANTHROPIC_API_KEY=x\nOURA_AUTH_KEY='{'ab' * 16}'\nOURA_BIN={self.bin}\n")
        s = oura_settings({"db": self.db}, self.env)
        self.assertEqual(s["bin"], str(self.bin))
        self.assertEqual(s["key_hex"], "ab" * 16)
        self.assertIsNone(s["key_file"])
        cmd = oura_command(dict(s, chunk_s=60), "/k")
        self.assertEqual(cmd, [str(self.bin), "--key-file", "/k", "--db", self.db, "live-hr", "--seconds", "60"])
        s2 = oura_settings({"db": self.db, "name": "", "address": "AA"}, self.env)
        self.assertIn("--address", oura_command(s2, "/k"))

    def test_temp_key_file_is_private_and_validated(self):
        d, p = write_temp_key("AB" * 16)
        try:
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
            self.assertEqual(Path(p).read_text().strip(), "ab" * 16)
        finally:
            import shutil
            shutil.rmtree(d)
        with self.assertRaises(ValueError) as cm:
            write_temp_key("secret-but-wrong")
        self.assertNotIn("secret", str(cm.exception))

    def test_stream_restart_and_cleanup(self):
        self.env.write_text(f"OURA_AUTH_KEY={'cd' * 16}\nOURA_BIN={self.bin}\n")
        os.environ["FAKE_OURA_BPMS"] = "70,72,74"
        got = []
        hs = HeartRateSense("open_oura", on_sample=got.append, env_file=str(self.env), db=self.db, chunk_s=5)
        try:
            self.assertTrue(wait_for(lambda: len(got) >= 6, 10), f"got {len(got)}")   # >= 2 runs: restarted
            st = hs.status()
            tmpdir = hs._tmpdir
            self.assertTrue(tmpdir and os.path.isdir(tmpdir))
        finally:
            hs.close()
        self.assertEqual([s["bpm"] for s in got[:3]], [70, 72, 74])
        self.assertEqual(got[0]["ibi_ms"], 857)
        self.assertEqual(st["backend"], "open_oura")
        self.assertEqual(st["last_bpm"] in (70, 72, 74), True)
        self.assertFalse(os.path.exists(tmpdir), "temp key dir not removed on close")
        runs = self.runs.read_text().splitlines()
        self.assertGreaterEqual(len(runs), 2)
        self.assertEqual(runs[0], "live-hr seconds=5 key_len=32 key_mode=0o600")

    def test_failure_backs_off(self):
        self.env.write_text(f"OURA_AUTH_KEY={'cd' * 16}\nOURA_BIN={self.bin}\n")
        os.environ["FAKE_OURA_BPMS"] = ""
        os.environ["FAKE_OURA_RC"] = "1"
        hs = HeartRateSense("open_oura", env_file=str(self.env), db=self.db)
        try:
            self.assertTrue(wait_for(lambda: "restarting" in hs.status()["detail"], 5), hs.status())
            st = hs.status()
            time.sleep(0.5)
            n = len(self.runs.read_text().splitlines())
        finally:
            hs.close()
        self.assertFalse(st["connected"])
        self.assertIn("no beats", st["detail"])
        self.assertEqual(n, 1)                          # backing off (2 s), not hammering

    def test_missing_binary_or_key(self):
        self.env.write_text("OURA_BIN=/nonexistent/oura\n")
        hs = HeartRateSense("open_oura", env_file=str(self.env), db=self.db)
        hs.done.wait(3)
        self.assertIn("binary not found", hs.status()["detail"])
        hs.close()
        self.env.write_text(f"OURA_BIN={self.bin}\n")
        hs = HeartRateSense("open_oura", env_file=str(self.env), db=self.db)
        hs.done.wait(3)
        self.assertIn("OURA_KEY_FILE or OURA_AUTH_KEY", hs.status()["detail"])
        hs.close()
        self.env.write_text(f"OURA_BIN={self.bin}\nOURA_AUTH_KEY=tooshort\n")
        hs = HeartRateSense("open_oura", env_file=str(self.env), db=self.db)
        hs.done.wait(3)
        self.assertIn("32 hex", hs.status()["detail"])
        self.assertNotIn("tooshort", hs.status()["detail"])
        hs.close()


if __name__ == "__main__":
    unittest.main()

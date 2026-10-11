"""Hearing: Neon mic -> ElevenLabs STT (mocked) -> "heard" line in the Haiku user text; sound-only matches in rules.
cd backend && ../.venv/bin/python -m unittest tests.test_hearing -v"""
import time
import unittest

import numpy as np

from spidey import hearing as H
from spidey.detectors import VLMDetector, norm_triggers
from spidey.rules import TriggerMemory, load_rules
from spidey.types import Detection, Frame

SR = H.SR


def sine(seconds, amp=8000, f=440):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.int16)


def silence(seconds):
    return np.zeros(int(seconds * SR), np.int16)


def word(text, s, e, typ="word"):
    return {"text": text, "type": typ, "start": s, "end": e}


def stt_json(*words):
    """Shaped like the ElevenLabs /v1/speech-to-text response (spacing items between words)."""
    out = []
    for w in words:
        if out:
            out.append({"text": " ", "type": "spacing", "start": w["start"], "end": w["start"]})
        out.append(w)
    return {"language_code": "en", "language_probability": 0.98,
            "text": "".join(w["text"] for w in out), "words": out}


class FakeSTT:
    def __init__(self, responses=None, delay=0.0, error=None):
        self.responses, self.delay, self.error = list(responses or []), delay, error
        self.calls = []

    def transcribe(self, pcm):
        self.calls.append(pcm.copy())
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return self.responses.pop(0) if self.responses else {"text": "", "words": []}


def feed_seconds(h, t0, pcm, chunk_s=0.128):
    """Feed pcm in Neon-sized chunks (1024 samples at 8 kHz = 128 ms) starting at unix time t0."""
    n = int(chunk_s * SR)
    for i in range(0, pcm.size, n):
        h.feed(t0 + i / SR, pcm[i:i + n])
    return t0 + pcm.size / SR


def wait_idle(h, timeout=2.0):
    end = time.time() + timeout
    while h._in_flight and time.time() < end:
        time.sleep(0.01)


class TestHelpers(unittest.TestCase):
    def test_block_rms_and_window(self):
        r = H.block_rms(np.concatenate([silence(0.5), sine(0.5, amp=1000)]))
        self.assertEqual(r.size, 10)
        self.assertLess(r[:5].max(), 1)
        self.assertAlmostEqual(float(r[5:].mean()), 1000 / np.sqrt(2), delta=20)
        buf = np.arange(SR * 4, dtype=np.int16)
        self.assertEqual(H.window_samples(buf, 3).size, SR * 3)
        self.assertEqual(int(H.window_samples(buf, 3)[-1]), int(buf[-1]))
        self.assertEqual(H.window_samples(buf, 10).size, buf.size)

    def test_parse_events_and_words(self):
        resp = stt_json(word("hey", 0.2, 0.4), word("Jaidev,", 0.45, 0.9), word("(car horn)", 1.0, 1.8, "audio_event"),
                        word("(silence)", 2.0, 2.5, "audio_event"))
        p = H.parse_stt(resp, 100.0)
        self.assertEqual([w[2] for w in p["words"]], ["hey", "Jaidev,"])
        self.assertAlmostEqual(p["words"][0][0], 100.2)
        self.assertEqual([e[2] for e in p["events"]], ["car horn"])      # (silence) dropped

    def test_parse_text_only_response(self):
        p = H.parse_stt({"text": "watch out (siren) [Shouting]"}, 5.0)
        self.assertEqual([w[2] for w in p["words"]], ["watch out"])
        self.assertEqual([e[2] for e in p["events"]], ["siren", "shouting"])
        self.assertEqual(H.parse_stt({}, 0.0), {"words": [], "events": [], "text": ""})

    def test_overlapping_windows_dedupe(self):
        # window A [0,3] and B [1.5,4.5] both heard "watch out"; B owns from 2.25 s
        a = {"t0": 0.0, "t1": 3.0, **H.parse_stt(stt_json(word("hey", 0.5, 0.7), word("Jaidev,", 0.8, 1.3),
                                                         word("watch", 2.4, 2.7), word("out", 2.75, 2.95)), 0.0)}
        b = {"t0": 1.5, "t1": 4.5, **H.parse_stt(stt_json(word("watch", 0.9, 1.2), word("out", 1.25, 1.45),
                                                         word("(car horn)", 1.5, 2.5, "audio_event")), 1.5)}
        for order in ([a, b], [b, a]):          # results may arrive out of order
            text, events = H.assemble(order, since=-1)
            self.assertEqual(text, "hey Jaidev, watch out")
            self.assertEqual(events, ["car horn"])

    def test_seam_word_heard_by_both(self):
        # "out" straddles the ownership seam (2.25 s): A has it starting 2.2, B starting 2.3 -> once
        a = {"t0": 0.0, "t1": 3.0, **H.parse_stt(stt_json(word("watch", 1.8, 2.1), word("out", 2.2, 2.6)), 0.0)}
        b = {"t0": 1.5, "t1": 4.5, **H.parse_stt(stt_json(word("out", 0.8, 1.1), word("now", 1.2, 1.5)), 1.5)}
        self.assertEqual(H.assemble([a, b], since=-1)[0], "watch out now")
        self.assertEqual(H.merge_overlap(["watch", "out"], ["Out!", "now"]), ["watch", "out", "now"])
        self.assertEqual(H.merge_overlap(["go"], ["stop"]), ["go", "stop"])

    def test_recent_window_cuts_old_words(self):
        a = {"t0": 0.0, "t1": 3.0, **H.parse_stt(stt_json(word("old", 0.1, 0.3)), 0.0)}
        b = {"t0": 6.0, "t1": 9.0, **H.parse_stt(stt_json(word("new", 0.5, 0.8)), 6.0)}
        self.assertEqual(H.assemble([a, b], since=9.0 - 5)[0], "new")

    def test_format(self):
        self.assertEqual(H.format_heard("hey Jaidev, watch out", ["car horn"], 5),
                         'Heard (last 5 s): "hey Jaidev, watch out" · sounds: car horn')
        self.assertEqual(H.format_heard("", ["siren", "dog barking"], 5), "Heard (last 5 s): sounds: siren, dog barking")
        self.assertIsNone(H.format_heard("  ", [], 5))
        long = H.format_heard("word " * 200, [], 5, max_chars=40)
        self.assertLess(len(long), 80)

    def test_multipart_body(self):
        body, ctype = H.multipart({"model_id": "scribe_v2", "tag_audio_events": "true"}, "file", "clip.pcm", b"\x01\x02")
        boundary = ctype.split("boundary=")[1]
        self.assertIn(b'name="model_id"\r\n\r\nscribe_v2\r\n', body)
        self.assertIn(b'filename="clip.pcm"', body)
        self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode()))


class TestHearing(unittest.TestCase):
    def test_no_key_is_off_with_reason(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(H, "REPO_ENV", H.Path("/nonexistent/.env")):
            h = H.Hearing()
        self.assertFalse(h.enabled)
        h.feed(0.0, sine(1))                       # harmless
        st = h.status()
        self.assertFalse(st["listening"])
        self.assertIn("ELEVENLABS_API_KEY", st["detail"])
        self.assertIsNone(h.recent(time.time()))
        self.assertEqual(H.Hearing(enabled=False).status()["detail"], "hearing off (config)")

    def test_silence_costs_no_calls(self):
        stt = FakeSTT()
        h = H.Hearing(stt=stt)
        feed_seconds(h, 1000.0, silence(8))
        wait_idle(h)
        self.assertEqual(stt.calls, [])
        self.assertGreater(h.n_skipped_silent, 3)

    def test_loud_audio_is_windowed_and_heard(self):
        t0 = time.time() - 4.0
        resp = stt_json(word("hey", 0.2, 0.4), word("Jaidev", 0.5, 0.9), word("(car horn)", 1.0, 2.0, "audio_event"))
        stt = FakeSTT([resp] * 10)
        h = H.Hearing(stt=stt)
        feed_seconds(h, t0, sine(4))
        wait_idle(h)
        self.assertGreaterEqual(len(stt.calls), 1)
        self.assertTrue(all(c.size <= 3 * SR + 1 for c in stt.calls))
        self.assertEqual(stt.calls[-1].dtype, np.int16)
        line = h.recent(t0 + 4.0)
        self.assertTrue(line.startswith('Heard (last 5 s): "hey Jaidev'), line)
        self.assertIn("sounds: car horn", line)
        self.assertEqual(line.count("Jaidev"), 1)               # overlapping windows not repeated
        st = h.status()
        self.assertTrue(st["listening"])
        self.assertIn("car horn", st["last_heard"])
        self.assertIn("listening", st["detail"])

    def test_max_in_flight(self):
        stt = FakeSTT(delay=0.3)
        h = H.Hearing(stt=stt, max_in_flight=2, hop_s=0.5)
        feed_seconds(h, 0.0, sine(4))                # 8 hops, calls take 0.3 s, all fed at once
        self.assertLessEqual(h._in_flight, 2)
        self.assertGreater(h.n_skipped_busy, 0)
        wait_idle(h)

    def test_sound_after_silence_is_sent(self):
        stt = FakeSTT()
        h = H.Hearing(stt=stt)
        t = feed_seconds(h, 0.0, silence(6))
        feed_seconds(h, t, sine(1.0, amp=3000))
        wait_idle(h)
        self.assertGreaterEqual(len(stt.calls), 1)

    def test_stt_error_shows_in_status_and_keeps_going(self):
        h = H.Hearing(stt=FakeSTT(error="HTTP 401: invalid api key"))
        feed_seconds(h, time.time() - 3, sine(3))
        wait_idle(h)
        self.assertIn("401", h.status()["detail"])
        self.assertIsNone(h.recent(time.time()))

    def test_gap_padded(self):
        h = H.Hearing(stt=FakeSTT())
        h.feed(0.0, sine(0.5))
        h.feed(1.5, sine(0.5))                       # 1 s of dropped frames
        self.assertAlmostEqual(h._buf.size / SR, 2.0, delta=0.01)
        self.assertAlmostEqual(h._buf_end, 2.0)

    def test_resampler_8k_fltp_to_16k_s16(self):
        import av
        f = av.AudioFrame.from_ndarray((0.3 * np.sin(np.arange(1024) / 8000 * 2 * np.pi * 440)).astype(np.float32)
                                       .reshape(1, -1), format="fltp", layout="mono")
        f.sample_rate = 8000
        r = H.Resampler16k()
        out = np.concatenate([r(f), r(f)])
        self.assertEqual(out.dtype, np.int16)
        self.assertGreater(out.size, 3000)           # ~2048 per 1024-sample frame, minus resampler delay
        self.assertGreater(np.abs(out).max(), 5000)


class TestDetectorText(unittest.TestCase):
    def det(self, ctx):
        d = object.__new__(VLMDetector)              # no API client needed for the text
        d.context_fn = ctx
        return d

    def test_heard_line_appended_after_triggers(self):
        d = self.det(lambda: 'Heard (last 5 s): "hey Jaidev" · sounds: car horn')
        t = d.user_text([("name", "someone calls my name", False)], d.context())
        lines = t.split("\n")
        self.assertEqual(lines[0], "Custom triggers:")
        self.assertEqual(lines[1], '- id "name": someone calls my name')
        self.assertEqual(lines[2], 'Heard (last 5 s): "hey Jaidev" · sounds: car horn')
        self.assertIn('"bbox": null', lines[3])

    def test_no_context_unchanged(self):
        for ctx in (None, lambda: None, lambda: "  ", lambda: 1 / 0, lambda: 42):
            d = self.det(ctx)
            self.assertEqual(d.user_text([("a", "b", False)], d.context()), 'Custom triggers:\n- id "a": b')
        d = self.det(lambda: "Heard (last 5 s): sounds: siren")
        self.assertEqual(d.user_text([], d.context()), "Custom triggers: none.")


class TestSoundOnlyRules(unittest.TestCase):
    """A sound-only match has no bbox (and x null): the gaze modes must not crash; 'any' fires."""

    def run_mode(self, mode, seen=False, extra_if=None):
        rules = load_rules([{"id": "name", "name": "name called", "kind": "custom", "describe": "someone calls my name",
                             "gaze_mode": mode, "if": extra_if or {}, "cooldown_s": 0}])
        img = np.zeros((120, 160, 3), np.uint8)
        trig = norm_triggers({"name": {"match": True, "urgency": 0.7, "x": None, "bbox": None,
                                       "seen_by_user": seen, "reason": "heard 'Jaidev'"}})
        self.assertIsNone(trig["name"]["where"])
        frame = Frame(10.0, img, (80, 60), False)
        d = Detection(None, None, False, 0.0, 500, {"frame_ts": 10.0}, triggers=trig, frame=frame)
        mem = TriggerMemory()
        mem.add_frame(frame)
        mem.annotate(d, rules, 10.2)
        self.assertIsNone(d.triggers["name"]["bbox"])
        ev, rec = rules[0].decide(d, 10.2, {"max_result_age_s": 3.0, "gaze_now": mem.newest_gaze()})
        return ev, rec

    def test_any_fires_without_bbox(self):
        ev, rec = self.run_mode("any")
        self.assertIsNotNone(ev, rec)
        self.assertNotIn("bbox", ev.extra)

    def test_gaze_modes_fall_back_to_haiku_seen(self):
        self.assertIsNotNone(self.run_mode("unseen", seen=False)[0])
        self.assertIsNone(self.run_mode("unseen", seen=True)[0])
        self.assertIsNotNone(self.run_mode("looking_at", seen=True)[0])
        self.assertIsNone(self.run_mode("looking_at", seen=False)[0])

    def test_ttc_condition_never_fires_without_box(self):
        ev, rec = self.run_mode("any", extra_if={"max_ttc_s": 3})
        self.assertIsNone(ev)
        self.assertIn("time-to-contact", rec["why"])


if __name__ == "__main__":
    unittest.main()

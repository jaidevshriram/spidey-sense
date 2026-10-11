"""Voice (ElevenLabs out + voice-drafted triggers). No network: ElevenLabs and Anthropic are mocked.
cd backend && ../.venv/bin/python -m unittest tests.test_voice -v"""
import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from spidey import voice as V
from spidey.engine import Controller
from spidey.sinks import make_sink

MP3 = b"ID3" + b"\x00" * 2000


class FakeEleven:
    def __init__(self, available=True, delay=0.0, gate: threading.Event | None = None):
        self.available, self.delay, self.gate = available, delay, gate
        self.voice_id = "testvoice"
        self.said, self.heard = [], []

    def tts(self, text):
        if self.gate is not None:
            self.gate.wait(5)
        time.sleep(self.delay)
        self.said.append(text)
        return MP3

    def stt(self, audio, filename="a.m4a", tag_events=False, language_code=None):
        self.heard.append((audio, filename))
        return {"text": "buzz me when a dog comes near me if I haven't seen it", "words": [], "language_code": "en"}


class FakeAnthropic:
    def __init__(self, reply: str):
        self.reply, self.calls = reply, []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


def wait_for(cond, timeout=3.0):
    t = time.time() + timeout
    while time.time() < t:
        if cond():
            return True
        time.sleep(0.01)
    return False


class TestSpokenLine(unittest.TestCase):
    def test_ttc_and_engine_notes(self):
        ev = {"pattern": "urgent", "trigger_name": "Table edge",
              "why": "table edge right in front [gaze cache, box 3.1 s old] (about 1.2 s away)"}
        self.assertEqual(V.spoken_line(ev), "Heads up: table edge, about one second away.")

    def test_ttc_field_wins_and_plural(self):
        ev = {"pattern": "urgent", "trigger_name": "Stairs", "why": "stairs down", "ttc_s": 2.6}
        self.assertEqual(V.spoken_line(ev), "Heads up: stairs, about three seconds away.")
        ev["ttc_s"] = 0.3
        self.assertEqual(V.spoken_line(ev), "Heads up: stairs, under a second away.")

    def test_no_ttc_short_and_clean(self):
        ev = {"pattern": "pulse", "trigger_name": "Dog nearby",
              "why": "a brown dog walking toward the camera on the left side of the sidewalk near a bench and a tree; "
                     "it is close [gaze cache, box 0.4 s old]"}
        line = V.spoken_line(ev)
        self.assertTrue(line.startswith("Dog nearby. A brown dog"), line)
        self.assertNotIn("[", line)
        self.assertNotIn(";", line)
        self.assertLessEqual(len(line.split()), 12)
        self.assertTrue(line.endswith("."))

    def test_radar(self):
        ev = {"pattern": "urgent", "sensor": "radar", "trigger_name": "Behind me",
              "why": "Person approaching from behind-left, 1.2 m at 0.8 m/s",
              "radar": {"distance_m": 1.2, "side": "left"}}
        self.assertEqual(V.spoken_line(ev), "Heads up: someone approaching behind you, left, one meter away.")

    def test_ttc_words(self):
        self.assertIsNone(V.ttc_words(None))
        self.assertIsNone(V.ttc_words(float("nan")))
        self.assertEqual(V.ttc_words(1.0), "about one second away")
        self.assertEqual(V.ttc_words(4.4), "about four seconds away")


class TestVoiceOut(unittest.TestCase):
    def make(self, speak="urgent", **kw):
        sent = []
        client = FakeEleven(**kw)
        vo = V.VoiceOut(client, sent.append, speak=speak, log=lambda s: None)
        self.addCleanup(vo.close)
        return vo, client, sent

    def test_speak_modes(self):
        urgent = {"id": 1, "ts": 1.0, "pattern": "urgent", "trigger_name": "A"}
        pulse = {"id": 2, "ts": 2.0, "pattern": "pulse", "trigger_name": "B"}
        vo, _, _ = self.make("urgent")
        self.assertTrue(vo.wants(urgent))
        self.assertFalse(vo.wants(pulse))
        self.assertFalse(vo.wants({**urgent, "quiet": True}))
        self.assertFalse(vo.wants({**urgent, "paused": True}))
        vo.speak = "all"
        self.assertTrue(vo.wants(pulse))
        vo.speak = "off"
        self.assertFalse(vo.wants(urgent))
        vo2, _, _ = self.make("all", available=False)
        self.assertFalse(vo2.wants(urgent))

    def test_message_shape(self):
        vo, _, sent = self.make("all")
        self.assertTrue(vo.offer({"id": 7, "ts": 123.0, "pattern": "urgent", "trigger_name": "Lion"}))
        self.assertTrue(wait_for(lambda: sent))
        m = sent[0]
        self.assertEqual({k: m[k] for k in ("type", "event_id", "ts", "text", "format")},
                         {"type": "speech", "event_id": 7, "ts": 123.0, "text": "Heads up: lion.", "format": "mp3"})
        self.assertEqual(base64.b64decode(m["audio_b64"]), MP3)

    def test_newest_wins(self):
        gate = threading.Event()
        vo, client, sent = self.make("all", gate=gate)
        vo.offer({"id": 1, "ts": 1, "pattern": "urgent", "trigger_name": "First"})
        self.assertTrue(wait_for(lambda: vo._pending is None))     # first is in flight (blocked on the gate)
        vo.offer({"id": 2, "ts": 2, "pattern": "urgent", "trigger_name": "Second"})
        vo.offer({"id": 3, "ts": 3, "pattern": "urgent", "trigger_name": "Third"})
        gate.set()
        self.assertTrue(wait_for(lambda: any(m["event_id"] == 3 for m in sent)))
        ids = [m["event_id"] for m in sent]
        self.assertNotIn(2, ids)            # queued and superseded: never synthesized
        self.assertNotIn(1, ids)            # finished after a newer line arrived: dropped
        self.assertNotIn("Heads up: second.", client.said)
        self.assertEqual(ids, [3])

    def test_offer_never_blocks(self):
        vo, _, _ = self.make("all", delay=1.0)
        t0 = time.perf_counter()
        for i in range(20):
            vo.offer({"id": i, "ts": i, "pattern": "urgent", "trigger_name": "X"})
        self.assertLess(time.perf_counter() - t0, 0.1)

    def test_tts_error_is_survived(self):
        sent = []
        client = FakeEleven()
        client.tts = mock.Mock(side_effect=[V.VoiceError("boom"), MP3])
        vo = V.VoiceOut(client, sent.append, speak="all", log=lambda s: None)
        self.addCleanup(vo.close)
        vo.say("one")
        self.assertTrue(wait_for(lambda: vo.stats["errors"] == 1))
        vo.say("two")
        self.assertTrue(wait_for(lambda: sent))
        self.assertEqual(sent[0]["text"], "two")


class TestDraft(unittest.TestCase):
    def test_clamps_and_shape(self):
        raw = {"name": "Dog Coming Near Me Now Please", "describe": "  a dog   within two meters ",
               "gaze_mode": "sideways", "only_while_walking": "false", "temporal": "true", "feel": "scream",
               "cooldown_s": 9999, "min_urgency": 7, "summary": "Buzz me when a dog comes near me."}
        rule, summary = V.validate_draft(raw, existing_ids=["dog_coming_near_me"])
        self.assertEqual(rule["id"], "dog_coming_near_me_2")
        self.assertEqual(rule["name"], "Dog Coming Near Me")
        self.assertEqual(rule["describe"], "a dog within two meters")
        self.assertEqual((rule["kind"], rule["source"], rule["enabled"]), ("custom", "neon", True))
        self.assertEqual(rule["gaze_mode"], "unseen")
        self.assertEqual(rule["feel"], "urgent")
        self.assertEqual(rule["cooldown_s"], 120.0)
        self.assertEqual(rule["if"], {"min_urgency": 1.0, "only_while_walking": False})
        self.assertTrue(rule["temporal"])
        self.assertEqual(rule["trend"], {"ticks": 3, "sensitivity": "medium"})
        self.assertEqual(summary, "Buzz me when a dog comes near me.")
        # same keys as the triggers file
        self.assertTrue({"id", "name", "enabled", "kind", "describe", "source", "when", "if", "feel", "intensity",
                         "rgb", "dot", "cooldown_s", "gaze_mode", "notice_memory_s", "temporal"} <= set(rule))

    def test_defaults(self):
        rule, summary = V.validate_draft({"describe": "an open door", "only_while_walking": True})
        self.assertEqual(rule["if"], {"min_urgency": 0.3, "only_while_walking": True})
        self.assertFalse(rule["temporal"])
        self.assertEqual(rule["cooldown_s"], 5.0)
        self.assertIn("an open door", summary)

    def test_rejects(self):
        with self.assertRaises(ValueError):
            V.validate_draft({"error": "that's a question, not a trigger"})
        with self.assertRaises(ValueError):
            V.validate_draft({"name": "x"})

    def test_draft_trigger_with_stub_model(self):
        reply = ('```json\n{"name": "Dog nearby", "describe": "a dog within about two meters", "gaze_mode": "unseen",'
                 ' "only_while_walking": false, "temporal": false, "feel": "urgent", "cooldown_s": 6,'
                 ' "summary": "Buzz me when a dog comes near me, only if I haven\'t noticed it."}\n```')
        fa = FakeAnthropic(reply)
        d = V.draft_trigger("buzz me when a dog comes near", client=fa)
        self.assertIsInstance(d, dict)
        self.assertEqual(d["rule"]["id"], "dog_nearby")
        self.assertEqual(d["rule"]["cooldown_s"], 6.0)
        kw = fa.calls[0]
        self.assertEqual(kw["model"], "claude-haiku-5-5")
        self.assertEqual(kw["thinking"], {"type": "disabled"})
        self.assertLessEqual(kw["max_tokens"], 500)

    def test_draft_trigger_errors(self):
        self.assertIsInstance(V.draft_trigger("hi", client=FakeAnthropic("sorry, no")), str)
        self.assertIsInstance(V.draft_trigger("hi", client=FakeAnthropic('{"error": "not a trigger"}')), str)
        self.assertEqual(V.draft_trigger("  ", client=FakeAnthropic("{}")), "empty transcript")
        boom = FakeAnthropic("")
        boom.messages.create = mock.Mock(side_effect=RuntimeError("network down"))
        self.assertIn("network down", V.draft_trigger("dogs", client=boom))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestElevenLabsClient(unittest.TestCase):
    def test_unavailable_without_key(self):
        c = V.ElevenLabs(api_key="")
        self.assertFalse(c.available)
        with self.assertRaises(V.VoiceError):
            c.tts("hello")

    def test_tts_request_and_cache(self):
        c = V.ElevenLabs(api_key="k-test")
        with mock.patch("urllib.request.urlopen", return_value=_Resp(MP3)) as uo:
            self.assertEqual(c.tts("hello there"), MP3)
            self.assertEqual(c.tts("hello there"), MP3)
        self.assertEqual(uo.call_count, 1)
        req, timeout = uo.call_args[0][0], uo.call_args[1]["timeout"]
        self.assertIn("/text-to-speech/JBFqnCBsd6RMkjVDRZzb/stream?output_format=mp3_22050_32", req.full_url)
        self.assertEqual(req.get_header("Xi-api-key"), "k-test")
        self.assertEqual(json.loads(req.data)["model_id"], "eleven_flash_v2_5")
        self.assertEqual(timeout, 5.0)

    def test_stt_multipart(self):
        c = V.ElevenLabs(api_key="k-test")
        body = json.dumps({"text": "hello", "words": [], "language_code": "en"}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_Resp(body)) as uo:
            out = c.stt(b"AUDIO", "req.m4a")
        self.assertEqual(out["text"], "hello")
        req = uo.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/speech-to-text"))
        self.assertIn(b'name="model_id"\r\n\r\nscribe_v2', req.data)
        self.assertIn(b'filename="req.m4a"\r\nContent-Type: audio/mp4', req.data)
        self.assertEqual(uo.call_args[1]["timeout"], 15.0)


class Srv:
    clients, history = [], []

    def __init__(self):
        self.published, self.sent = [], []

    def publish(self, ev):
        self.published.append(ev)

    def broadcast(self, m):
        self.sent.append(m)

    def on(self, kind, fn):
        pass

    def add_periodic(self, *a):
        pass


class TestEngineWiring(unittest.TestCase):
    def make(self, fake):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cfg = {"sink": {"type": "console"}, "source": {"type": "fake"},
               "voice": {"enabled": True, "speak": "urgent", "env_file": str(Path(d.name) / "missing.env")}}
        with mock.patch.object(V, "make_client", return_value=fake):
            ctl = Controller(cfg, make_sink({"type": "console"}), "fake", rules_path=Path(d.name) / "t.json",
                             feedback_path=Path(d.name) / "fb.jsonl")
        ctl.attach(srv := Srv())
        if ctl.voice:
            self.addCleanup(ctl.voice.close)
        return ctl, srv

    def speech(self, srv):
        return [m for m in srv.sent if m.get("type") == "speech"]

    def test_emit_speaks_played_events(self):
        from spidey.types import HapticEvent
        ctl, srv = self.make(FakeEleven())
        ctl.emit([HapticEvent("urgent", None, "Lion: a lion", time.time(), "lion", trigger_name="Lion",
                              why="a lion on the screen")])
        self.assertTrue(wait_for(lambda: self.speech(srv)))
        self.assertEqual(self.speech(srv)[0]["event_id"], 1)
        self.assertEqual(self.speech(srv)[0]["text"], "Heads up: lion. A lion on the screen.")
        ctl.quiet = True     # quiet events are not spoken
        ctl.emit([HapticEvent("urgent", None, "Lion", time.time(), "lion", trigger_name="Lion")])
        time.sleep(0.2)
        self.assertEqual(len(self.speech(srv)), 1)

    def test_voice_command_and_settings(self):
        ctl, srv = self.make(FakeEleven())
        self.assertEqual(ctl.settings_msg()["voice"], {"speak": "urgent", "available": True, "voice_id": "testvoice"})
        ack = ctl.on_command({"type": "command", "name": "voice", "args": {"speak": "all"}})
        self.assertTrue(ack["ok"])
        self.assertEqual(ctl.voice.speak, "all")
        self.assertEqual([m for m in srv.sent if m["type"] == "settings"][-1]["voice"]["speak"], "all")
        bad = ctl.on_command({"type": "command", "name": "voice", "args": {"speak": "loud"}})
        self.assertFalse(bad["ok"])
        self.assertIn("settings", ctl.hello_extra())
        self.assertIn("voice", ctl.hello_extra()["settings"])

    def test_voice_draft_roundtrip(self):
        fake = FakeEleven()
        ctl, srv = self.make(fake)
        ctl.anthropic_client = FakeAnthropic(json.dumps(
            {"name": "Dog nearby", "describe": "a dog within about two meters", "gaze_mode": "unseen",
             "only_while_walking": False, "temporal": False, "feel": "urgent", "cooldown_s": 6,
             "summary": "Buzz me when a dog comes near me, only if I haven't noticed it."}))
        ack = ctl.on_voice_command({"type": "voice.command", "id": "r1", "format": "m4a",
                                    "audio_b64": base64.b64encode(b"AUDIO").decode()})
        self.assertEqual((ack["type"], ack["for"], ack["ok"]), ("ack", "voice.command", True))
        self.assertTrue(wait_for(lambda: any(m["type"] == "voice.draft" for m in srv.sent)))
        draft = next(m for m in srv.sent if m["type"] == "voice.draft")
        self.assertTrue(draft["ok"], draft)
        self.assertEqual(draft["id"], "r1")
        self.assertEqual(draft["transcript"], "buzz me when a dog comes near me if I haven't seen it")
        self.assertEqual(draft["rule"]["id"], "dog_nearby")
        self.assertEqual(fake.heard[0], (b"AUDIO", "request.m4a"))
        self.assertTrue(wait_for(lambda: any(m.get("kind") == "draft" for m in self.speech(srv))))
        s = next(m for m in self.speech(srv) if m.get("kind") == "draft")
        self.assertIsNone(s["event_id"])
        # the app then confirms with the usual upsert
        self.assertTrue(ctl.on_upsert({"rule": draft["rule"]})["ok"])
        self.assertIn("dog_nearby", [r.id for r in ctl.rules])

    def test_voice_command_bad_input(self):
        ctl, _ = self.make(FakeEleven())
        self.assertFalse(ctl.on_voice_command({"audio_b64": "!!notbase64", "format": "m4a"})["ok"])
        self.assertFalse(ctl.on_voice_command({"audio_b64": "QUJD", "format": "exe"})["ok"])
        self.assertFalse(ctl.on_voice_command({"audio_b64": "", "format": "wav"})["ok"])

    def test_unavailable_degrades(self):
        ctl, srv = self.make(FakeEleven(available=False))
        self.assertFalse(ctl.settings_msg()["voice"]["available"])
        ack = ctl.on_command({"type": "command", "name": "voice", "args": {"speak": "all"}})
        self.assertTrue(ack["ok"])
        self.assertIn("unavailable", ack["detail"])
        r = ctl.on_voice_command({"audio_b64": "QUJD", "format": "m4a"})
        self.assertFalse(r["ok"])
        from spidey.types import HapticEvent
        ctl.emit([HapticEvent("urgent", None, "x", time.time(), "x", trigger_name="X")])
        time.sleep(0.1)
        self.assertEqual(self.speech(srv), [])

    def test_voice_disabled_in_config(self):
        with tempfile.TemporaryDirectory() as d:
            ctl = Controller({"sink": {"type": "console"}}, make_sink({"type": "console"}), "fake",
                             rules_path=Path(d) / "t.json", feedback_path=Path(d) / "fb.jsonl")
        self.assertIsNone(ctl.voice)
        self.assertFalse(ctl.settings_msg()["voice"]["available"])
        self.assertFalse(ctl.on_voice_command({"audio_b64": "QUJD"})["ok"])

    def test_no_key_real_client(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ELEVENLABS_API_KEY", None)
            c = V.make_client({"env_file": "/nonexistent/.env"})
        self.assertFalse(c.available)


if __name__ == "__main__":
    unittest.main()

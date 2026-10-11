"""Voice: ElevenLabs speech out (fired events -> a short spoken line) and speech in (a spoken request ->
STT -> a trigger draft from Haiku). Everything degrades gracefully: no ELEVENLABS_API_KEY = voice
"available": false, no exceptions in the engine.

    ElevenLabs.tts(text) -> mp3 bytes            (eleven_flash_v2_5, LRU-cached by text)
    ElevenLabs.stt(audio, filename) -> dict      (scribe_v2: {text, words, language_code})
    spoken_line(event) -> "Heads up: table edge, about one second away."
    VoiceOut: background thread, newest wins; broadcasts {"type": "speech", ...} with base64 mp3
    draft_trigger(transcript) -> {"rule": {...}, "summary": "..."} or an error string

Keys come from the environment or the repo-root .env (never logged).
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from . import rules as R

REPO_ENV = Path(__file__).resolve().parents[2] / ".env"
API = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
DEFAULT_STT_MODEL = "scribe_v2"
DEFAULT_FORMAT = "mp3_22050_32"
SPEAK_MODES = ("all", "urgent", "off")
FEELS = ("urgent", "ambient", "pulse")
DRAFT_MODEL = "claude-haiku-5-5"
AUDIO_TYPES = {"m4a": "audio/mp4", "mp4": "audio/mp4", "aac": "audio/aac", "wav": "audio/wav",
               "mp3": "audio/mpeg", "caf": "audio/x-caf", "webm": "audio/webm", "ogg": "audio/ogg",
               "flac": "audio/flac"}


def load_env(env_file: str | Path | None = None) -> None:
    """Fill missing keys from a .env file (default: repo root). Never overrides the environment."""
    path = Path(env_file).expanduser() if env_file else REPO_ENV
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
    except Exception:
        pass


class VoiceError(RuntimeError):
    pass


# ---- ElevenLabs REST client ------------------------------------------------------------------
class ElevenLabs:
    def __init__(self, api_key: str | None = None, voice_id: str = DEFAULT_VOICE_ID,
                 model_id: str = DEFAULT_TTS_MODEL, stt_model: str = DEFAULT_STT_MODEL,
                 output_format: str = DEFAULT_FORMAT, voice_settings: dict | None = None,
                 tts_timeout_s: float = 5.0, stt_timeout_s: float = 15.0, cache_size: int = 64):
        self._key = api_key if api_key is not None else os.environ.get("ELEVENLABS_API_KEY", "")
        self._key = (self._key or "").strip()
        self.voice_id, self.model_id, self.stt_model = voice_id, model_id, stt_model
        self.output_format = output_format
        self.voice_settings = voice_settings or {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.1}
        self.tts_timeout_s, self.stt_timeout_s = tts_timeout_s, stt_timeout_s
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._cache_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self._key)

    def _open(self, req: urllib.request.Request, timeout: float) -> bytes:
        if not self._key:
            raise VoiceError("no ELEVENLABS_API_KEY")
        req.add_header("xi-api-key", self._key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                body = ""
            raise VoiceError(f"ElevenLabs HTTP {e.code}: {body}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise VoiceError(f"ElevenLabs unreachable: {getattr(e, 'reason', e)}") from None

    def tts(self, text: str) -> bytes:
        """Text -> mp3 bytes (streaming endpoint, read to the end). Cached by (voice, model, text)."""
        text = (text or "").strip()
        if not text:
            raise VoiceError("empty text")
        key = (self.voice_id, self.model_id, self.output_format, text)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        url = f"{API}/text-to-speech/{self.voice_id}/stream?output_format={self.output_format}"
        body = json.dumps({"text": text, "model_id": self.model_id, "voice_settings": self.voice_settings}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "audio/mpeg"})
        audio = self._open(req, self.tts_timeout_s)
        if len(audio) < 100:
            raise VoiceError(f"ElevenLabs TTS returned {len(audio)} bytes")
        with self._cache_lock:
            self._cache[key] = audio
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return audio

    def stt(self, audio: bytes, filename: str = "audio.m4a", tag_events: bool = False,
            language_code: str | None = None) -> dict:
        """Audio bytes -> {text, words: [{text, type, start, end}], language_code} (scribe_v2, batch)."""
        if not audio:
            raise VoiceError("empty audio")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "m4a"
        fields = {"model_id": self.stt_model, "tag_audio_events": "true" if tag_events else "false"}
        if language_code:
            fields["language_code"] = language_code
        body, ctype = multipart(fields, "file", filename, audio, AUDIO_TYPES.get(ext, "application/octet-stream"))
        req = urllib.request.Request(f"{API}/speech-to-text", data=body, method="POST",
                                     headers={"Content-Type": ctype, "Accept": "application/json"})
        raw = self._open(req, self.stt_timeout_s)
        try:
            out = json.loads(raw)
        except ValueError:
            raise VoiceError("ElevenLabs STT: response was not JSON") from None
        if not isinstance(out, dict):
            raise VoiceError("ElevenLabs STT: unexpected response")
        return out


def multipart(fields: dict, file_field: str, filename: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "spidey" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
                 f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ---- fired event -> one short sentence ---------------------------------------------------------
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
_AWAY = re.compile(r"\(?\s*about\s+([0-9.]+)\s*s(?:ec(?:ond)?s?)?\s+away\s*\)?", re.I)
MAX_WORDS = 12


def number_words(n: int) -> str:
    return _ONES[n] if 0 <= n < len(_ONES) else str(n)


def ttc_words(ttc: float | None) -> str | None:
    if ttc is None:
        return None
    try:
        ttc = float(ttc)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ttc) or ttc < 0 or ttc > 20:
        return None
    if ttc < 0.75:
        return "under a second away"
    n = max(1, int(ttc + 0.5))
    return f"about {number_words(n)} second{'s' if n != 1 else ''} away"


def _clean(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "", text or "")                  # engine notes: [gaze cache, box 3.1 s old]
    text = _AWAY.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ;:,.-")
    return text


def _words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n])


def spoken_line(event: dict) -> str:
    """A short natural sentence (<= ~12 words) for a fired event; no engine notes, ttc in words."""
    name = _clean(str(event.get("trigger_name") or event.get("rule") or "something")).replace("_", " ")
    why = str(event.get("why") or "")
    ttc = event.get("ttc_s")
    if ttc is None:
        m = _AWAY.search(why) or _AWAY.search(str(event.get("reason") or ""))
        if m:
            try:
                ttc = float(m.group(1))
            except ValueError:
                ttc = None
    why = _clean(why)
    if why.lower().startswith(name.lower() + ":"):
        why = why[len(name) + 1:].strip()
    prefix = "Heads up: " if event.get("pattern") == "urgent" else ""
    subject = name[:1].lower() + name[1:] if prefix and not name[:2].isupper() else name

    radar = event.get("radar")
    if event.get("sensor") == "radar" and isinstance(radar, dict):
        side = {"left": "behind you, left", "right": "behind you, right"}.get(radar.get("side"), "behind you")
        line = f"someone approaching {side}"
        d = radar.get("distance_m")
        if isinstance(d, (int, float)) and d > 0:
            m = max(1, int(d + 0.5))
            line += f", {number_words(m)} meter{'s' if m != 1 else ''} away"
        return _sentence((prefix or "Heads up: ") + line)

    tw = ttc_words(ttc)
    if tw:
        return _sentence(f"{prefix}{subject}, {tw}")
    budget = MAX_WORDS - len((prefix + subject).split())
    detail = re.split(r"[;.!?]| - | — ", why, maxsplit=1)[0].strip(" ,") if why else ""
    if detail and budget >= 3 and detail.lower() != subject.lower():
        return _sentence(f"{prefix}{subject}. {_cap(_words(detail, budget))}")
    return _sentence(f"{prefix}{subject}")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _sentence(s: str) -> str:
    s = _words(re.sub(r"\s+", " ", s).strip(" ,;:"), MAX_WORDS + 2).rstrip(" ,;:")
    s = _cap(s)
    return s if s.endswith((".", "!", "?")) else s + "."


# ---- speech out ---------------------------------------------------------------------------------
class VoiceOut:
    """Speaks played events on a background thread. Newest wins: a new line replaces any line still
    waiting; a finished line older than max_age_s is dropped (stale speech is worse than none)."""

    def __init__(self, client: ElevenLabs, broadcast: Callable[[dict], None], speak: str = "urgent",
                 max_age_s: float = 4.0, log: Callable[[str], None] | None = None):
        self.client, self.broadcast = client, broadcast
        self.speak = speak if speak in SPEAK_MODES else "urgent"
        self.max_age_s = max_age_s
        self.log = log or (lambda s: print(s, flush=True))
        self._cv = threading.Condition()
        self._pending: dict | None = None
        self._closed = False
        self.stats = {"spoken": 0, "dropped": 0, "errors": 0, "last_ms": None, "last_error": None}
        self._thread = threading.Thread(target=self._run, name="voice-out", daemon=True)
        self._thread.start()

    @property
    def available(self) -> bool:
        return self.client is not None and self.client.available

    def wants(self, ev: dict) -> bool:
        if not self.available or self.speak == "off" or ev.get("quiet") or ev.get("paused"):
            return False
        return self.speak == "all" or ev.get("pattern") == "urgent"

    def offer(self, ev: dict) -> bool:
        """Engine hook (non-blocking): queue a played event's line if the speak mode allows it."""
        if not self.wants(ev):
            return False
        return self.say(spoken_line(ev), event_id=ev.get("id"), ts=ev.get("ts"))

    def say(self, text: str, event_id=None, ts: float | None = None, kind: str = "event") -> bool:
        if not self.available or not text:
            return False
        with self._cv:
            if self._pending is not None:
                self.stats["dropped"] += 1
            self._pending = {"text": text, "event_id": event_id, "ts": ts if ts is not None else time.time(),
                             "queued": time.time(), "kind": kind}
            self._cv.notify()
        return True

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._pending is None and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                job, self._pending = self._pending, None
            t0 = time.perf_counter()
            try:
                audio = self.client.tts(job["text"])
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = str(e)[:200]
                self.log(f"voice: TTS failed ({e})")
                continue
            ms = round((time.perf_counter() - t0) * 1000, 1)
            self.stats["last_ms"] = ms
            with self._cv:
                newer = self._pending is not None
            if newer or time.time() - job["queued"] > self.max_age_s:
                self.stats["dropped"] += 1        # superseded or stale while synthesizing
                continue
            msg = {"type": "speech", "event_id": job["event_id"], "ts": job["ts"], "text": job["text"],
                   "format": "mp3", "audio_b64": base64.b64encode(audio).decode(), "tts_ms": ms}
            if job["kind"] != "event":
                msg["kind"] = job["kind"]
            try:
                self.broadcast(msg)
                self.stats["spoken"] += 1
            except Exception as e:
                self.stats["errors"] += 1
                self.log(f"voice: broadcast failed ({e})")


# ---- speech in: request -> trigger draft ----------------------------------------------------------
DRAFT_SYSTEM = """You turn a spoken request into ONE trigger for Spidey Sense: eye-tracking glasses whose \
scene camera is checked by a vision model, which buzzes a haptic dot on the wearer's skin when the \
trigger matches.

Reply with ONLY a JSON object, no prose, no code fence:
{"name": "<= 4 words, Title case",
 "describe": "plain English for a vision model: what is VISIBLE in the scene camera (or heard) when this \
should fire; concrete nouns, e.g. 'a dog within about two meters of the camera'",
 "gaze_mode": "unseen" | "looking_at" | "any",
 "only_while_walking": true | false,
 "temporal": true | false,
 "feel": "urgent" | "ambient" | "pulse",
 "cooldown_s": <number of seconds between buzzes, 3-30>,
 "summary": "one sentence to read back to the wearer, e.g. 'Buzz me when a dog comes near me, only if I \
haven't noticed it.'"}

Rules:
- gaze_mode "unseen" when the request implies 'if I haven't noticed/seen it' or it's a hazard/warning; \
"looking_at" when it is about what the wearer is looking at; otherwise "any".
- only_while_walking true when the request mentions walking, obstacles, tripping or bumping into things.
- temporal true ONLY when it is about something changing over time (getting closer, approaching).
- feel "urgent" for danger/safety, "pulse" for a gentle notice, "ambient" for background awareness.
- If the request is not a trigger at all, reply {"error": "<short reason>"}."""


def _json_obj(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        out = json.loads(text[i:j + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _num(v, default: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return max(lo, min(hi, x))


def _bool(v) -> bool:
    return v.strip().lower() in ("true", "yes", "1") if isinstance(v, str) else bool(v)


def validate_draft(raw: dict, transcript: str = "", existing_ids=()) -> tuple[dict, str]:
    """Model JSON -> (rule dict in Rule.to_dict shape, summary). Raises ValueError when unusable."""
    if not isinstance(raw, dict):
        raise ValueError("model reply was not a JSON object")
    if raw.get("error") and not raw.get("describe"):
        raise ValueError(str(raw["error"])[:200])
    describe = re.sub(r"\s+", " ", str(raw.get("describe") or "")).strip()
    if len(describe) < 3:
        raise ValueError("no description of what to watch for")
    describe = describe[:240]
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip(" .\"'")
    if not name:
        name = " ".join(re.findall(r"[A-Za-z]+", describe)[:3]).title() or "Voice trigger"
    name = " ".join(name.split()[:4])[:40]
    gaze = raw.get("gaze_mode") if raw.get("gaze_mode") in R.GAZE_MODES else "unseen"
    feel = raw.get("feel") if raw.get("feel") in FEELS else "urgent"
    temporal, walking = _bool(raw.get("temporal")), _bool(raw.get("only_while_walking"))
    if_ = {"min_urgency": round(_num(raw.get("min_urgency"), 0.3, 0.0, 1.0), 2), "only_while_walking": walking}
    rid, base, n = R.slug(name), R.slug(name), 2
    taken = set(existing_ids or ())
    while rid in taken:
        rid, n = f"{base}_{n}", n + 1
    rule = R.rule_from_dict({
        "id": rid, "name": name, "enabled": True, "kind": "custom", "describe": describe, "source": "neon",
        "when": "any", "if": if_, "feel": feel, "cooldown_s": round(_num(raw.get("cooldown_s"), 5.0, 1.0, 120.0), 1),
        "gaze_mode": gaze, "notice_memory_s": 3.0, "temporal": temporal,
        **({"trend": {"ticks": 3, "sensitivity": "medium"}} if temporal else {})}).to_dict()
    summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
    if not summary:
        tail = {"unseen": ", only if I haven't noticed it", "looking_at": " while I'm looking at it"}.get(gaze, "")
        summary = f"Buzz me when I see {describe}{tail}."
    return rule, summary[:200]


def draft_trigger(transcript: str, client=None, model: str = DRAFT_MODEL, existing_ids=(),
                  max_tokens: int = 300) -> dict | str:
    """Spoken request -> {"rule": {...}, "summary": "...", "ms": ...}, or an error string."""
    transcript = (transcript or "").strip()
    if not transcript:
        return "empty transcript"
    try:
        if client is None:
            import anthropic
            load_env()
            if not os.environ.get("ANTHROPIC_API_KEY"):
                return "no ANTHROPIC_API_KEY"
            client = anthropic.Anthropic(max_retries=1, timeout=10.0)
        t0 = time.perf_counter()
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, thinking={"type": "disabled"}, system=DRAFT_SYSTEM,
            messages=[{"role": "user", "content": f"Spoken request: {transcript}"}])
        ms = round((time.perf_counter() - t0) * 1000, 1)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "text") == "text")
    except Exception as e:
        return f"draft failed: {type(e).__name__}: {str(e)[:200]}"
    raw = _json_obj(text)
    if raw is None:
        return "draft failed: the model did not reply with JSON"
    try:
        rule, summary = validate_draft(raw, transcript, existing_ids)
    except ValueError as e:
        return f"not a trigger: {e}"
    return {"rule": rule, "summary": summary, "ms": ms}


# ---- engine glue ------------------------------------------------------------------------------------
def make_client(vcfg: dict) -> ElevenLabs:
    load_env(vcfg.get("env_file"))
    return ElevenLabs(voice_id=vcfg.get("voice_id") or DEFAULT_VOICE_ID,
                      model_id=vcfg.get("tts_model") or DEFAULT_TTS_MODEL,
                      stt_model=vcfg.get("stt_model") or DEFAULT_STT_MODEL,
                      output_format=vcfg.get("output_format") or DEFAULT_FORMAT,
                      voice_settings=vcfg.get("voice_settings"),
                      tts_timeout_s=float(vcfg.get("tts_timeout_s", 5.0)),
                      stt_timeout_s=float(vcfg.get("stt_timeout_s", 15.0)))

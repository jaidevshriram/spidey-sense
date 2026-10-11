"""Spidey "hearing": the Neon glasses' microphone -> ElevenLabs speech-to-text (with sound-event tags) ->
a one-line "what was heard in the last few seconds" that rides along in the SAME Haiku call as the video.

Design: rolling batch windows. Every `hop_s` of audio, the last `window_s` seconds (16 kHz mono s16) go to the
batch STT endpoint (model scribe_v2, tag_audio_events=true), at most `max_in_flight` requests at a time.
Batch, not the realtime websocket, because the realtime model does not tag sound events (horn, siren,
shouting), and those are half the point. Cost and latency control:
  - silence gate: a window is only sent if its newest (hop_s + 0.5) s has a loud 100 ms block (peak block RMS
    above max(min_rms, noise_k x rolling noise floor)), so a quiet room costs no calls;
  - overlapping windows (3 s window, 1.5 s hop): a word cut at one window's edge is whole in the next; each
    window "owns" the words that start after the middle of its overlap with the previous window, so the
    repeated text is never shown twice (see `assemble`);
  - one keep-alive HTTPS connection per worker thread (no TLS handshake per call).
Heard-to-Haiku latency is about hop/2 + STT time (~0.5-1 s) on average.

Key: ELEVENLABS_API_KEY from the environment, else the repo-root .env (never logged). No key -> hearing is
off and status() says why.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

SR = 16000
STT_HOST = "api.elevenlabs.io"
STT_PATH = "/v1/speech-to-text"
BLOCK_S = 0.1
# sound tags that carry no information for a trigger
IGNORED_EVENTS = {"silence", "background noise", "noise", "static", "inaudible"}
REPO_ENV = Path(__file__).resolve().parents[2] / ".env"


# ---- pure helpers (unit-tested) -------------------------------------------------------------
def block_rms(pcm: np.ndarray, sr: int = SR, block_s: float = BLOCK_S) -> np.ndarray:
    """RMS of each block_s block of int16 PCM (a trailing partial block is dropped unless it's all there is)."""
    n = max(1, int(sr * block_s))
    x = np.asarray(pcm, dtype=np.float32)
    if x.size == 0:
        return np.zeros(0, np.float32)
    k = x.size // n
    if k == 0:
        return np.array([float(np.sqrt(np.mean(x * x)))], np.float32)
    b = x[:k * n].reshape(k, n)
    return np.sqrt(np.mean(b * b, axis=1))


def window_samples(buf: np.ndarray, seconds: float, sr: int = SR) -> np.ndarray:
    """The newest `seconds` of a contiguous sample buffer (all of it if shorter)."""
    n = int(round(seconds * sr))
    return buf[-n:] if n < buf.size else buf


def clean_event(text: str) -> str | None:
    """'(car horn)' / '[Laughter]' -> 'car horn' / 'laughter'; None for empty or uninformative tags."""
    t = re.sub(r"[\(\)\[\]]", "", str(text or "")).strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t if t and t not in IGNORED_EVENTS else None


def parse_stt(resp: dict, t0: float) -> dict:
    """ElevenLabs STT JSON for a clip starting at unix time t0 -> {"words": [(start, end, text)],
    "events": [(start, end, name)], "text": str}, times absolute. Without word timings the whole text is one
    item at the clip's middle (parenthesized tags in it become events)."""
    words, events = [], []
    items = resp.get("words") or []
    for w in items:
        if not isinstance(w, dict):
            continue
        typ, txt = w.get("type", "word"), str(w.get("text") or "")
        try:
            s, e = float(w.get("start") or 0.0), float(w.get("end") or w.get("start") or 0.0)
        except (TypeError, ValueError):
            s = e = 0.0
        if typ == "audio_event":
            name = clean_event(txt)
            if name:
                events.append((t0 + s, t0 + e, name))
        elif typ == "word" and txt.strip():
            words.append((t0 + s, t0 + e, txt.strip()))
    text = str(resp.get("text") or "")
    if not items and text.strip():
        for m in re.finditer(r"\(([^)]*)\)|\[([^\]]*)\]", text):
            name = clean_event(m.group(0))
            if name:
                events.append((t0, t0, name))
        speech = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
        speech = re.sub(r"\s+", " ", speech).strip()
        if speech:
            words.append((t0, t0, speech))
    return {"words": words, "events": events, "text": text}


def _norm(tok: str) -> str:
    return re.sub(r"[^\w']", "", tok.lower())


def merge_overlap(prev: list[str], new: list[str], max_k: int = 4) -> list[str]:
    """Append `new` tokens to `prev`, dropping a repeated run at the seam ("watch out" + "out now" ->
    "watch out now"); compares up to max_k tokens, ignoring case and punctuation."""
    for k in range(min(max_k, len(prev), len(new)), 0, -1):
        if [_norm(t) for t in prev[-k:]] == [_norm(t) for t in new[:k]] and any(_norm(t) for t in new[:k]):
            return prev + new[k:]
    return prev + new


def assemble(windows: list[dict], since: float) -> tuple[str, list[str]]:
    """Stitch overlapping windows' results into (speech text, sound names) for items ending at/after `since`.
    windows: [{"t0", "t1", "words", "events"}] (any order). Each window owns [own_start, next own_start): its
    own_start is the middle of its overlap with the previous window (its t0 if they don't overlap), so text
    both windows heard is taken once, from the window that heard it with more context."""
    ws = sorted(windows, key=lambda w: w["t0"])
    starts = []
    for i, w in enumerate(ws):
        if i and ws[i - 1]["t1"] > w["t0"]:
            starts.append((w["t0"] + ws[i - 1]["t1"]) / 2)
        else:
            starts.append(w["t0"])
    tokens: list[str] = []
    events: list[str] = []
    for i, w in enumerate(ws):
        lo, hi = starts[i], (starts[i + 1] if i + 1 < len(ws) else float("inf"))
        seg = [t for s, e, t in sorted(w["words"]) if lo <= s < hi and e >= since]
        if seg:
            tokens = merge_overlap(tokens, " ".join(seg).split())
        for s, e, name in sorted(w["events"]):
            if lo <= s < hi and e >= since and name not in events:
                events.append(name)
    return " ".join(tokens), events


def format_heard(text: str, events: list[str], window_s: float, max_chars: int = 220) -> str | None:
    """'Heard (last 5 s): "hey Jaidev, watch out" · sounds: car horn', or None if nothing was heard."""
    text = text.strip()
    if len(text) > max_chars:
        text = "…" + text[-max_chars:].split(" ", 1)[-1]
    parts = []
    if text:
        parts.append('"' + text.replace('"', "'") + '"')
    if events:
        parts.append("sounds: " + ", ".join(events))
    if not parts:
        return None
    return f"Heard (last {window_s:g} s): " + " · ".join(parts)


def multipart(fields: dict, file_field: str, filename: str, data: bytes,
              content_type: str = "application/octet-stream") -> tuple[bytes, str]:
    """(body, content-type header) for a multipart/form-data POST."""
    boundary = "----spidey" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
               f"Content-Type: {content_type}\r\n\r\n".encode())
    out.append(data)
    out.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def load_key(env_file: str | Path | None = None) -> str | None:
    """ELEVENLABS_API_KEY from the environment, else from env_file / the repo-root .env (not exported)."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key.strip()
    try:
        from dotenv import dotenv_values
        for p in (env_file, REPO_ENV):
            if p and Path(p).expanduser().exists():
                v = dotenv_values(Path(p).expanduser()).get("ELEVENLABS_API_KEY")
                if v:
                    return v.strip()
    except Exception:
        pass
    return None


# ---- STT client ------------------------------------------------------------------------------
class ElevenLabsSTT:
    """Batch speech-to-text, one keep-alive HTTPS connection per calling thread."""

    def __init__(self, key: str, model_id: str = "scribe_v2", language_code: str | None = None,
                 timeout_s: float = 8.0):
        self._key, self.model_id, self.language_code, self.timeout_s = key, model_id, language_code, timeout_s
        self._local = threading.local()

    def _conn(self) -> http.client.HTTPSConnection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = http.client.HTTPSConnection(STT_HOST, timeout=self.timeout_s)
        return c

    def transcribe(self, pcm16k: np.ndarray) -> dict:
        """int16 mono 16 kHz PCM -> the API's JSON. Raises RuntimeError("HTTP <code>: ...") on errors."""
        fields = {"model_id": self.model_id, "tag_audio_events": "true", "file_format": "pcm_s16le_16"}
        if self.language_code:
            fields["language_code"] = self.language_code
        body, ctype = multipart(fields, "file", "clip.pcm", np.asarray(pcm16k, "<i2").tobytes())
        headers = {"xi-api-key": self._key, "Content-Type": ctype, "Accept": "application/json"}
        for attempt in (0, 1):                 # one retry on a dropped keep-alive connection
            c = self._conn()
            try:
                c.request("POST", STT_PATH, body=body, headers=headers)
                r = c.getresponse()
                raw = r.read()
                break
            except (http.client.HTTPException, OSError):
                c.close()
                self._local.conn = None
                if attempt:
                    raise
        if r.status != 200:
            msg = raw[:200].decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {r.status}: {msg}")
        return json.loads(raw)


# ---- Hearing ---------------------------------------------------------------------------------
class Hearing:
    """feed(ts, pcm16k) from the audio thread; transcription runs on a small pool; recent()/status() for
    the engine. ts = unix time (Mac clock) of the chunk's first sample."""

    def __init__(self, enabled: bool = True, window_s: float = 3.0, hop_s: float = 1.5, max_in_flight: int = 2,
                 min_rms: float = 200.0, noise_k: float = 3.0, keep_s: float = 10.0,
                 language_code: str | None = None, model_id: str = "scribe_v2", env_file: str | None = None,
                 stt=None, key: str | None = None):
        self.window_s, self.hop_s, self.keep_s = window_s, hop_s, keep_s
        self.max_in_flight, self.min_rms, self.noise_k = max(1, max_in_flight), min_rms, noise_k
        self._lock = threading.Lock()
        self._buf = np.zeros(0, np.int16)
        self._buf_end: float | None = None        # unix time just after the newest sample
        self._next_hop: float | None = None
        self._noise = deque(maxlen=300)           # block RMS history (~30 s) for the noise floor
        self._windows: deque = deque()            # transcribed windows {t0, t1, words, events, text}
        self._in_flight = 0
        self.n_calls = self.n_skipped_silent = self.n_skipped_busy = 0
        self.last_ms: float | None = None
        self.last_error: str | None = None
        self.last_audio: float | None = None      # wall time of the last feed()
        self.audio_error: str | None = None       # set by the source when the mic stream is unavailable
        self.stt = stt
        self.detail = ""
        if not enabled:
            self.enabled, self.detail = False, "hearing off (config)"
        elif stt is None:
            key = key or load_key(env_file)
            if not key:
                self.enabled, self.detail = False, "hearing off: no ELEVENLABS_API_KEY in the environment or .env"
            else:
                self.stt = ElevenLabsSTT(key, model_id=model_id, language_code=language_code)
                self.enabled = True
        else:
            self.enabled = True
        self._pool = ThreadPoolExecutor(max_workers=self.max_in_flight, thread_name_prefix="hearing") \
            if self.enabled else None

    # -- input --------------------------------------------------------------------------------
    def feed(self, ts: float, pcm16k: np.ndarray) -> None:
        if not self.enabled:
            return
        pcm = np.asarray(pcm16k, np.int16).reshape(-1)
        if pcm.size == 0:
            return
        launch = None
        with self._lock:
            self.last_audio = time.time()
            if self._buf_end is not None:
                gap = ts - self._buf_end
                if gap > 0.3:                          # dropped frames: keep the timeline with silence
                    pad = int(min(gap, self.window_s) * SR)
                    self._buf = np.concatenate([self._buf, np.zeros(pad, np.int16)])
                elif gap < -1.0:                       # clock jump backwards: start over
                    self._buf, self._next_hop = np.zeros(0, np.int16), None
            self._buf = np.concatenate([self._buf, pcm])[-int(SR * (self.window_s + 1)):]
            self._buf_end = ts + pcm.size / SR
            self._noise.extend(block_rms(pcm).tolist())
            if self._next_hop is None:
                self._next_hop = self._buf_end + self.hop_s
            if self._buf_end >= self._next_hop:
                self._next_hop = self._buf_end + self.hop_s
                win = window_samples(self._buf, self.window_s)
                if not self._loud(window_samples(win, self.hop_s + 0.5)):
                    self.n_skipped_silent += 1
                elif self._in_flight >= self.max_in_flight:
                    self.n_skipped_busy += 1           # the next window covers this one's new audio
                else:
                    self._in_flight += 1
                    launch = (self._buf_end - win.size / SR, self._buf_end, win.copy())
        if launch:
            self._pool.submit(self._transcribe, *launch)

    def threshold(self) -> float:
        floor = float(np.percentile(self._noise, 20)) if len(self._noise) >= 20 else 0.0
        return max(self.min_rms, self.noise_k * floor)

    def _loud(self, pcm: np.ndarray) -> bool:
        r = block_rms(pcm)
        return r.size > 0 and float(r.max()) >= self.threshold()

    # -- transcription ------------------------------------------------------------------------
    def _transcribe(self, t0: float, t1: float, pcm: np.ndarray) -> None:
        t_start = time.perf_counter()
        try:
            resp = self.stt.transcribe(pcm)
            self.add_result(t0, t1, resp)
            self.last_error = None
        except Exception as e:                       # never let a network error kill hearing
            msg = str(e)
            self.last_error = f"{type(e).__name__}: {msg[:120]}"
        finally:
            self.last_ms = round((time.perf_counter() - t_start) * 1000)
            with self._lock:
                self._in_flight -= 1
                self.n_calls += 1

    def add_result(self, t0: float, t1: float, resp: dict) -> None:
        """Store one window's STT JSON (also the test entry point)."""
        p = parse_stt(resp or {}, t0)
        with self._lock:
            self._windows.append({"t0": t0, "t1": t1, **p})
            newest = max(w["t1"] for w in self._windows)
            while self._windows and self._windows[0]["t1"] < newest - self.keep_s:
                self._windows.popleft()

    # -- output -------------------------------------------------------------------------------
    def heard(self, now: float, window_s: float = 5.0) -> tuple[str, list[str]]:
        with self._lock:
            ws = list(self._windows)
        return assemble(ws, now - window_s)

    def recent(self, now: float, window_s: float = 5.0) -> str | None:
        """'Heard (last 5 s): "..." · sounds: ...' for the Haiku call, or None if nothing was heard."""
        if not self.enabled:
            return None
        return format_heard(*self.heard(now, window_s), window_s)

    def status(self) -> dict:
        now = time.time()
        live = self.enabled and self.last_audio is not None and now - self.last_audio < 3.0
        text, events = self.heard(now, 10.0) if self.enabled else ("", [])
        last = format_heard(text, events, 10.0)
        last = last.split(": ", 1)[1] if last else None
        if not self.enabled:
            detail = self.detail
        elif self.audio_error and not live:
            detail = f"no microphone audio: {self.audio_error}"
        elif self.last_audio is None:
            detail = "waiting for microphone audio (enable the microphone in the Neon Companion app)"
        elif not live:
            detail = f"microphone audio stopped {now - self.last_audio:.0f} s ago"
        elif self.last_error:
            detail = f"speech-to-text error: {self.last_error}"
        else:
            detail = (f"listening · {self.n_calls} STT calls"
                      + (f", last {self.last_ms} ms" if self.last_ms is not None else "")
                      + f" · {self.n_skipped_silent} quiet windows skipped")
        return {"listening": bool(live), "last_heard": last, "detail": detail}

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)


# ---- Neon audio frame -> 16 kHz mono int16 -----------------------------------------------------
class Resampler16k:
    """av.AudioFrame (Neon: fltp mono, 8 kHz AAC) -> int16 mono 16 kHz numpy, via one shared PyAV resampler."""

    def __init__(self):
        import av
        self._r = av.AudioResampler(format="s16", layout="mono", rate=SR)

    def __call__(self, av_frame) -> np.ndarray:
        out = [f.to_ndarray().reshape(-1) for f in self._r.resample(av_frame)]
        return np.concatenate(out).astype(np.int16) if out else np.zeros(0, np.int16)

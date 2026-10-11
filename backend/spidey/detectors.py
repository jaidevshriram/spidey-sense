"""Frame -> Detection. FakeDetector follows a script; VLMDetector will ask a vision model."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path

import cv2

from .types import Detection, Frame


class Detector:
    def analyze(self, frame: Frame) -> Detection | None:
        """Return a Detection, or None if an async detector has no new result yet."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class FakeDetector(Detector):
    """Scripted timeline, looping every `period` seconds:
    hazard appears at `appear` s (unseen), user notices at `seen` s, gone at `gone` s."""

    def __init__(self, hazard: str = "bike", where: str = "left", appear: float = 3,
                 seen: float = 6, gone: float = 8, period: float = 10, urgency: float = 0.8):
        self.hazard, self.where = hazard, where
        self.appear, self.seen, self.gone, self.period = appear, seen, gone, period
        self.urgency = urgency
        self.t0 = None

    def analyze(self, frame: Frame) -> Detection:
        if self.t0 is None:
            self.t0 = frame.ts
        t = (frame.ts - self.t0) % self.period
        if not (self.appear <= t < self.gone):
            return Detection(None, None, False, 0.0, raw={"t": round(t, 2)})
        return Detection(self.hazard, self.where, seen_by_user=t >= self.seen,
                         urgency=self.urgency, raw={"t": round(t, 2)})


class VLMDetector(Detector):
    """Claude Haiku judges each frame (gaze drawn as a red dot): hazards + the user's custom triggers,
    all in ONE call per tick.

    Latency features (all configurable):
      concurrency   - up to N calls in flight, launched every min_interval_s on the newest frame;
                      results older than the newest delivered frame are dropped
      stream        - the reply is streamed; hazard/urgency/x/seen come first in the JSON, so an "early"
                      Detection is emitted as soon as they're parsed (built-in triggers act on it), then
                      the full one (custom triggers + reasons)
      max_side / jpeg_quality - smaller image = fewer input tokens
      cache         - static system prompt marked for prompt caching (only effective above the model's
                      minimum cacheable length)
    Key: ANTHROPIC_API_KEY from the environment, else loaded from `env_file` (never logged).
    """

    SYSTEM = (
        "You are a 'spidey sense' for the person wearing a head-mounted camera. The red dot in each image "
        "is where they are looking. Reply with strict JSON only, no prose, keys in exactly this order: "
        '{"hazard": string|null, "urgency": number 0-1, "x": number 0-1, "seen_by_user": bool, '
        '"reason": string, "triggers": {...}}. '
        "hazard: something dangerous (moving toward them, obstacle, vehicle, sharp object), else null with "
        "urgency 0. x: horizontal centre of the hazard (0 left edge, 1 right edge). seen_by_user: the red "
        "dot is on or near it. reason: under 15 words. "
        'triggers: for each custom trigger id listed by the user, {"match": bool, "urgency": 0-1, '
        '"x": 0-1, "seen_by_user": bool, "reason": string}; for a trigger that does not match return only '
        '{"match": false}. match only if the image clearly shows what the trigger describes. '
        "If there are no custom triggers, triggers is {}."
    )

    def __init__(self, model: str = "claude-haiku-5-5", max_side: int = 384, jpeg_quality: int = 70,
                 max_tokens: int = 200, min_interval_s: float = 0.4, concurrency: int = 2,
                 stream: bool = True, cache: bool = True, env_file: str | None = None):
        import os
        from concurrent.futures import ThreadPoolExecutor

        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY") and env_file:
            from dotenv import load_dotenv
            load_dotenv(Path(env_file).expanduser(), override=False)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("VLMDetector: set ANTHROPIC_API_KEY, or point detector.env_file "
                               "in config.yaml at a .env file that defines it")
        self.client = anthropic.Anthropic(max_retries=1, timeout=10.0)
        self.model, self.max_side, self.jpeg_quality = model, max_side, jpeg_quality
        self.max_tokens, self.min_interval_s = max_tokens, min_interval_s
        self.concurrency, self.stream, self.cache = max(1, concurrency), stream, cache
        self.triggers: list[tuple[str, str]] = []   # (id, plain-English description), set by the engine
        self._pool = ThreadPoolExecutor(max_workers=self.concurrency)
        self._lock = threading.Lock()
        self._pending: Frame | None = None
        self._results: list[Detection] = []
        self._in_flight = 0
        self._last_dispatch = 0.0
        self._last_delivered = (0.0, 0)     # (frame_ts, stage) stage 0 early, 1 full

    # ---- request building ---------------------------------------------------------------
    def set_triggers(self, triggers: list[tuple[str, str]]) -> None:
        """Enabled custom triggers to judge in the same call (engine calls this on every change)."""
        self.triggers = list(triggers)

    def system_blocks(self) -> list[dict]:
        blocks = [{"type": "text", "text": self.SYSTEM}]
        if self.cache:
            blocks[0]["cache_control"] = {"type": "ephemeral"}
        return blocks

    def user_text(self, trig) -> str:
        if not trig:
            return "Custom triggers: none."
        return "Custom triggers:\n" + "\n".join(f'- id "{tid}": {desc.strip()}' for tid, desc in trig)

    def prepare(self, frame: Frame) -> str:
        """Downscale to max_side px, draw the gaze dot, return base64 JPEG."""
        img = frame.image
        h, w = img.shape[:2]
        scale = self.max_side / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) \
            if scale < 1 else img.copy()
        g = frame.gaze_px()
        if g is not None:
            s = min(scale, 1.0)
            cv2.circle(small, (int(g[0] * s), int(g[1] * s)), max(5, int(8 * self.max_side / 512)), (0, 0, 255), -1)
        ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        return base64.b64encode(jpg.tobytes()).decode()

    # ---- one call -----------------------------------------------------------------------
    _EARLY = re.compile(r'"hazard"\s*:\s*(null|"(?:[^"\\]|\\.)*")\s*,\s*"urgency"\s*:\s*([0-9.]+)\s*,'
                        r'\s*"x"\s*:\s*([0-9.]+|null)\s*,\s*"seen_by_user"\s*:\s*(true|false)')

    def call(self, frame: Frame) -> Detection:
        """One model call for one frame. With stream=True, delivers an early Detection when the
        built-in fields have arrived, then returns the full one."""
        t0 = time.perf_counter()
        trig = list(self.triggers)
        kwargs = dict(model=self.model, max_tokens=self.max_tokens + 60 * len(trig),
                      thinking={"type": "disabled"}, output_config={"effort": "low"},
                      system=self.system_blocks(),
                      messages=[{"role": "user", "content": [
                          {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": self.prepare(frame)}},
                          {"type": "text", "text": self.user_text(trig)}]}])
        early_ms = None
        if self.stream:
            text = ""
            with self.client.messages.stream(**kwargs) as st:
                for chunk in st.text_stream:
                    text += chunk
                    if early_ms is None:
                        m = self._EARLY.search(text)
                        if m:
                            early_ms = (time.perf_counter() - t0) * 1000
                            hz = None if m.group(1) == "null" else json.loads(m.group(1)) or None
                            u = max(0.0, min(1.0, float(m.group(2))))
                            self._deliver(Detection(
                                hz, where_from_x(m.group(3)) if hz else None, m.group(4) == "true",
                                u if hz else 0.0, early_ms,
                                {"frame_ts": frame.ts, "gaze": frame.gaze_xy, "early": True}), frame.ts, 0)
                resp = st.get_final_message()
        else:
            resp = self.client.messages.create(**kwargs)
            text = "".join(b.text for b in resp.content if b.type == "text")
        latency = (time.perf_counter() - t0) * 1000
        u = resp.usage
        raw = {"frame_ts": frame.ts, "gaze": frame.gaze_xy, "text": text, "stop_reason": resp.stop_reason,
               "n_triggers": len(trig), "early_ms": early_ms, "input_tokens": u.input_tokens,
               "output_tokens": u.output_tokens, "cache_read": getattr(u, "cache_read_input_tokens", 0)}
        data = parse_json(text)
        if data is None:
            return Detection(None, None, False, 0.0, latency, raw | {"parse_error": True})
        hazard = data.get("hazard") or None
        try:
            urgency = max(0.0, min(1.0, float(data.get("urgency") or 0)))
        except (TypeError, ValueError):
            urgency = 0.0
        where = (where_from_x(data.get("x")) or data.get("where")) if hazard else None
        triggers = {}
        for tid, t in (data.get("triggers") or {}).items():
            if isinstance(t, dict):
                try:
                    tu = max(0.0, min(1.0, float(t.get("urgency") or 0)))
                except (TypeError, ValueError):
                    tu = 0.0
                triggers[str(tid)] = {"match": bool(t.get("match")), "urgency": tu,
                                      "where": where_from_x(t.get("x")), "seen_by_user": bool(t.get("seen_by_user")),
                                      "reason": str(t.get("reason") or "")[:200]}
        return Detection(hazard, where, bool(data.get("seen_by_user")), urgency if hazard else 0.0, latency, raw,
                         reason=str(data.get("reason") or "")[:200], triggers=triggers)

    # ---- scheduling ---------------------------------------------------------------------
    def _deliver(self, det: Detection, frame_ts: float, stage: int) -> None:
        with self._lock:
            if (frame_ts, stage) <= self._last_delivered:
                return      # an older frame finished after a newer one: stale, drop it
            self._last_delivered = (frame_ts, stage)
            self._results.append(det)

    def _run_one(self, frame: Frame) -> None:
        import anthropic

        t0 = time.time()
        try:
            det = self.call(frame)
        except anthropic.APIError as e:
            det = Detection(None, None, False, 0.0, (time.time() - t0) * 1000,
                            {"frame_ts": frame.ts, "error": f"{type(e).__name__}: {e}"})
        finally:
            with self._lock:
                self._in_flight -= 1
        self._deliver(det, frame.ts, 1)

    def analyze(self, frame: Frame) -> Detection | None:
        now = time.time()
        with self._lock:
            self._pending = frame
            launch = (self._in_flight < self.concurrency and now - self._last_dispatch >= self.min_interval_s)
            if launch:
                self._in_flight += 1
                self._last_dispatch = now
                f, self._pending = self._pending, None
            det = self._results.pop(0) if self._results else None
        if launch:
            self._pool.submit(self._run_one, f)
        return det

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def where_from_x(x) -> str | None:
    """Direction from horizontal position in the scene camera (the model's own "where" was unreliable)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return "left" if x < 0.33 else "right" if x > 0.67 else "ahead"


def parse_json(text: str) -> dict | None:
    """Pull a JSON object out of a model reply (tolerates code fences and stray prose)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        return None
    try:
        obj = json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def make_detector(cfg: dict) -> Detector:
    kind = cfg.get("type", "fake")
    params = {k: v for k, v in cfg.items() if k != "type"}
    if kind == "fake":
        return FakeDetector(**params)
    if kind == "vlm":
        return VLMDetector(**params)
    raise ValueError(f"unknown detector type {kind!r}")

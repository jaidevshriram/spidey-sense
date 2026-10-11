"""Frame -> Detection. FakeDetector follows a script; VLMDetector will ask a vision model."""
from __future__ import annotations

import base64
import json
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
    """Claude Haiku looks at the frame (with your gaze drawn on it) and returns JSON.

    Calls run on a background thread: analyze() hands over the newest frame and returns
    the latest finished Detection (or None), so the frame loop never waits on the network.
    Frames that arrive while a call is in flight replace each other; only the newest is sent.

    Key: ANTHROPIC_API_KEY from the environment, else loaded from `env_file` (never logged).
    """

    PROMPT = (
        "You are a 'spidey sense' for the person wearing this camera. The red dot is where they "
        "are looking. Is there a hazard (something moving toward them, an obstacle, a vehicle, "
        "a sharp or dangerous object)? Reply with strict JSON only, no prose: "
        '{"hazard": string|null, "x": number 0-1, "seen_by_user": bool, "urgency": number 0-1}. '
        "x is the horizontal centre of the hazard in the image (0 = left edge, 1 = right edge). "
        "seen_by_user is true if the red dot is on or near the hazard. "
        "Use null hazard and urgency 0 when nothing is dangerous."
    )

    def __init__(self, model: str = "claude-haiku-5-5", max_side: int = 512,
                 max_tokens: int = 150, min_interval_s: float = 0.0, env_file: str | None = None):
        import os
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY") and env_file:
            from dotenv import load_dotenv
            load_dotenv(Path(env_file).expanduser(), override=False)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("VLMDetector: set ANTHROPIC_API_KEY, or point detector.env_file "
                               "in config.yaml at a .env file that defines it")
        self.client = anthropic.Anthropic(max_retries=1, timeout=10.0)
        self.model, self.max_side, self.max_tokens = model, max_side, max_tokens
        self.min_interval_s = min_interval_s
        self._pending: Frame | None = None
        self._result: Detection | None = None
        self._cv = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def prepare(self, frame: Frame) -> str:
        """Downscale to max_side px, draw the gaze dot, return base64 JPEG."""
        img = frame.image
        h, w = img.shape[:2]
        scale = self.max_side / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img.copy()
        g = frame.gaze_px()
        if g is not None:
            s = min(scale, 1.0)
            cv2.circle(small, (int(g[0] * s), int(g[1] * s)), 8, (0, 0, 255), -1)
        ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(jpg.tobytes()).decode()

    def call(self, frame: Frame) -> Detection:
        """One blocking model call for one frame."""
        t0 = time.perf_counter()
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "disabled"},          # speed over deliberation
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": self.prepare(frame)}},
                {"type": "text", "text": self.PROMPT},
            ]}],
        )
        latency = (time.perf_counter() - t0) * 1000
        text = "".join(b.text for b in resp.content if b.type == "text")
        raw = {"frame_ts": frame.ts, "gaze": frame.gaze_xy, "text": text,
               "stop_reason": resp.stop_reason}
        data = parse_json(text)
        if data is None:
            return Detection(None, None, False, 0.0, latency, raw | {"parse_error": True})
        hazard = data.get("hazard") or None
        try:
            urgency = max(0.0, min(1.0, float(data.get("urgency") or 0)))
        except (TypeError, ValueError):
            urgency = 0.0
        where = (where_from_x(data.get("x")) or data.get("where")) if hazard else None
        return Detection(hazard, where,
                         bool(data.get("seen_by_user")), urgency if hazard else 0.0, latency, raw)

    def _worker(self):
        import anthropic

        last = 0.0
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._pending is not None or self._stop)
                if self._stop:
                    return
                frame, self._pending = self._pending, None
            wait = self.min_interval_s - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
                with self._cv:  # a newer frame may have arrived while waiting
                    if self._pending is not None:
                        frame, self._pending = self._pending, None
            last = time.time()
            try:
                det = self.call(frame)
            except anthropic.APIError as e:
                det = Detection(None, None, False, 0.0, (time.time() - last) * 1000,
                                {"frame_ts": frame.ts, "error": f"{type(e).__name__}: {e}"})
            with self._cv:
                self._result = det

    def analyze(self, frame: Frame) -> Detection | None:
        with self._cv:
            self._pending = frame  # replaces any stale frame not yet sent
            self._cv.notify()
            det, self._result = self._result, None
        return det

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()


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

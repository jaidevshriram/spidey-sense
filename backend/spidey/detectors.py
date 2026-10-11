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

    SYSTEM = (       # v2: recall-first, literal triggers, camera facts, calibrated urgency (detector.prompt: v2)
        "You are the 'spidey sense' of a person wearing eye-tracking glasses with a wide-angle head-mounted "
        "camera. Your answer decides, within about a second, whether a small device on their body taps them, "
        "so be literal and decisive.\n"
        "IMAGE: what the wearer's head is facing. A red dot marks where their eyes are looking; with no red dot "
        "the gaze is unknown and every seen_by_user is false. The lens is wide-angle: the lower part of the image "
        "is the ground near their feet, and things that look bigger are closer. The view may be tilted, blurred "
        "or upside down; judge the content anyway.\n"
        "Reply with strict JSON only, no prose, keys in exactly this order: "
        '{"hazard": string|null, "urgency": number 0-1, "x": number 0-1, "seen_by_user": bool, '
        '"triggers": {...}, "reason": string}.\n'
        "hazard: a short noun for something that could hurt the wearer in the next few seconds (something "
        "moving toward them, an obstacle in their path, a vehicle, a sharp or hot object near them), else null "
        "with urgency 0. x: horizontal centre of the hazard (0 left edge, 1 right edge). seen_by_user: the red "
        "dot is on or very near it.\n"
        "triggers: one entry per custom trigger id the user lists. Each trigger describes something visible: "
        "match when the image shows it, even partly, small, far away or in an unusual setting. Do not reject a "
        "match because of the apparent mood or intent (smiling, playful, friendly, staged, indoors, on a screen) "
        "unless the trigger itself asks about intent. A missed match is worse than an extra tap: if it plausibly "
        "matches, set match true and show doubt with a lower urgency. "
        'Matching entry: {"match": true, "urgency": 0-1, "x": 0-1, "seen_by_user": bool, "bbox": [x0, y0, x1, y1], '
        '"reason": string}; non-matching entry: {"match": false}. urgency: 0.3 plausible or partly visible, '
        "0.6 clearly present, 0.9 about to make contact or clearly imminent. bbox: tight box around the matching "
        "thing, fractions 0-1 of image width/height, origin top-left. Trigger reason: under 12 words, what you "
        "see (e.g. \"fist moving toward the camera from the left\"). If there are no custom triggers, triggers "
        "is {}.\n"
        "reason: the scene in under 12 words.\n"
        "Several images labelled t-1.4s, t-0.7s, now are consecutive frames from the same camera, oldest "
        "first: judge the newest ('now'); every x and box refers to it; use the earlier ones only for change "
        "over time. For a trigger marked (temporal), whenever its subject and the target it moves relative to "
        'are visible (even if match is false), use {"match": bool, "urgency": 0-1, "subject_bbox": [..], '
        '"target_bbox": [..], "ordinal": "closer"|"same"|"farther", "touching": bool, "reason": string}: '
        "ordinal = is the subject closer to the target now than in the earlier frames; touching = subject "
        "touches or enters the target now."
    )

    SYSTEM_V1 = (   # original prompt, kept for A/B (detector.prompt: v1)
        "You are a 'spidey sense' for the person wearing a head-mounted camera. The red dot in each image "
        "is where they are looking. Reply with strict JSON only, no prose, keys in exactly this order: "
        '{"hazard": string|null, "urgency": number 0-1, "x": number 0-1, "seen_by_user": bool, '
        '"reason": string, "triggers": {...}}. '
        "hazard: something dangerous (moving toward them, obstacle, vehicle, sharp object), else null with "
        "urgency 0. x: horizontal centre of the hazard (0 left edge, 1 right edge). seen_by_user: the red "
        "dot is on or near it. reason: under 15 words. "
        'triggers: for each custom trigger id listed by the user, {"match": bool, "urgency": 0-1, '
        '"x": 0-1, "seen_by_user": bool, "bbox": [x0, y0, x1, y1], "reason": string}; bbox = tight box around '
        "the matching thing, as fractions 0-1 of the image width/height (origin top-left). For a trigger that "
        'does not match return only {"match": false}. match only if the image clearly shows what the trigger '
        "describes. If there are no custom triggers, triggers is {}. "
        "Several images labelled t-1.4s, t-0.7s, now are consecutive frames from the same camera, oldest "
        "first: judge the newest ('now'); every x and box refers to it; use the earlier ones only for change "
        "over time. For a trigger marked (temporal), whenever its subject and the target it moves relative to "
        'are visible (even if match is false), use {"match": bool, "urgency": 0-1, "subject_bbox": [..], '
        '"target_bbox": [..], "ordinal": "closer"|"same"|"farther", "touching": bool, "reason": string}: '
        "ordinal = is the subject closer to the target now than in the earlier frames; touching = subject "
        "touches or enters the target now."
    )

    SCREENS_AS_REAL = (
        " This is a desk simulation of the real world: treat anything shown on a screen, monitor or picture as if it "
        "were physically in front of the wearer at that position. Do not discount hazards or triggers for being an "
        "image or on a monitor; judge urgency as if real."
    )

    TEMPORAL_OFFSETS = (1.4, 0.7)      # older frames sent with temporal triggers (seconds before "now")
    RING_EVERY_S = 0.1                 # keep a small copy of a frame at most this often
    RING_S = 2.5

    def __init__(self, model: str = "claude-haiku-5-5", max_side: int = 384, jpeg_quality: int = 70,
                 max_tokens: int = 200, min_interval_s: float = 0.4, concurrency: int = 2,
                 stream: bool = True, cache: bool = True, env_file: str | None = None,
                 treat_screens_as_real: bool = False, prompt: str = "v1"):
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
        # desk tests: on-screen content counts as real (appended to the system prompt; JSON key order unchanged)
        self.system_text = (self.SYSTEM_V1 if prompt == "v1" else self.SYSTEM) + (self.SCREENS_AS_REAL if treat_screens_as_real else "")
        self.triggers: list[tuple[str, str, bool]] = []   # (id, description, temporal), set by the engine
        self._ring: list[tuple[float, Frame]] = []    # (ts, downscaled frame) for temporal triggers
        self._pool = ThreadPoolExecutor(max_workers=self.concurrency)
        self._lock = threading.Lock()
        self._pending: Frame | None = None
        self._results: list[Detection] = []
        self._in_flight = 0
        self._last_dispatch = 0.0
        self._last_delivered = [0.0, 0.0]   # newest frame_ts delivered per stage: [early, full]
        self.trace = False                  # engine sets True: keep the exact JPEGs + prompt on Detection.trace
        self.on_stale = None                # engine callback(det): a full result dropped as stale (for the tracer)
        self._call_id = 0

    # ---- request building ---------------------------------------------------------------
    def set_triggers(self, triggers: list[tuple]) -> None:
        """Enabled custom triggers to judge in the same call (engine calls this on every change).
        Items: (id, description) or (id, description, temporal)."""
        self.triggers = [(t[0], t[1], bool(t[2]) if len(t) > 2 else False) for t in triggers]

    @property
    def temporal(self) -> bool:
        return any(t[2] for t in self.triggers)

    def system_blocks(self) -> list[dict]:
        blocks = [{"type": "text", "text": self.system_text}]
        if self.cache:
            blocks[0]["cache_control"] = {"type": "ephemeral"}
        return blocks

    def user_text(self, trig) -> str:
        if not trig:
            return "Custom triggers: none."
        return "Custom triggers:\n" + "\n".join(f'- id "{t[0]}"{" (temporal)" if len(t) > 2 and t[2] else ""}: '
                                                f'{t[1].strip()}' for t in trig)

    def _small(self, frame: Frame) -> Frame:
        """A max_side copy of the frame (gaze rescaled) for the temporal ring buffer."""
        img = frame.image
        h, w = img.shape[:2]
        scale = self.max_side / max(h, w)
        if scale >= 1:
            return frame
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        g = frame.gaze_xy
        if g is not None and not frame.gaze_norm:
            g = (g[0] * scale, g[1] * scale)
        return Frame(frame.ts, small, g, frame.gaze_norm)

    def _remember(self, frame: Frame) -> None:
        if not self._ring or frame.ts - self._ring[-1][0] >= self.RING_EVERY_S:
            self._ring.append((frame.ts, self._small(frame)))
        while self._ring and self._ring[0][0] < frame.ts - self.RING_S:
            self._ring.pop(0)

    def history_for(self, frame: Frame) -> list[tuple[str, Frame]]:
        """[(label, frame)] oldest first, ending with ("now", frame); older frames only with temporal triggers."""
        out = []
        if self.temporal:
            for off in self.TEMPORAL_OFFSETS:
                cands = [(abs(ts - (frame.ts - off)), f) for ts, f in self._ring if ts < frame.ts - off / 2]
                if cands:
                    out.append((f"t-{off:.1f}s", min(cands, key=lambda c: c[0])[1]))
        return out + [("now", frame)]

    def prepare(self, frame: Frame) -> str:
        """Downscale to max_side px, draw the gaze dot, return base64 JPEG."""
        return base64.b64encode(self.prepare_jpeg(frame)).decode()

    def prepare_jpeg(self, frame: Frame) -> bytes:
        """The JPEG bytes sent to the model: downscaled to max_side px, gaze dot drawn."""
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
        return jpg.tobytes()

    # ---- one call -----------------------------------------------------------------------
    _EARLY = re.compile(r'"hazard"\s*:\s*(null|"(?:[^"\\]|\\.)*")\s*,\s*"urgency"\s*:\s*([0-9.]+)\s*,'
                        r'\s*"x"\s*:\s*([0-9.]+|null)\s*,\s*"seen_by_user"\s*:\s*(true|false)')

    def call(self, frame: Frame, history: list[tuple[str, Frame]] | None = None, call_id: int = 0,
             t_submit: float | None = None) -> Detection:
        """One model call for one frame (plus, with temporal triggers, 2 older labelled frames in the same
        request). With stream=True, delivers an early Detection when the built-in fields have arrived,
        then returns the full one."""
        t0, t_start = time.perf_counter(), time.time()
        trig = list(self.triggers)
        history = history or [("now", frame)]
        content, sent = [], []
        for label, f in history:
            if len(history) > 1:
                content.append({"type": "text", "text": label})
            jpg = self.prepare_jpeg(f)
            if self.trace:
                sent.append({"label": label, "frame_ts": f.ts, "jpeg": jpg, "gaze": f.gaze_xy, "gaze_norm": f.gaze_norm,
                             "src_wh": [f.image.shape[1], f.image.shape[0]]})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(jpg).decode()}})
        user_text = self.user_text(trig)
        content.append({"type": "text", "text": user_text})
        tr = {"call_id": call_id, "t_submit": t_submit or t_start, "t_start": t_start, "images": sent,
              "user_text": user_text, "triggers": [list(t) for t in trig], "model": self.model} if self.trace else None
        n_temporal = sum(1 for t in trig if t[2])
        kwargs = dict(model=self.model, max_tokens=self.max_tokens + 90 * len(trig) + 40 * n_temporal,
                      thinking={"type": "disabled"} if "haiku" in self.model else {"type": "between_tools"}, output_config={"effort": "low"},
                      system=self.system_blocks(), messages=[{"role": "user", "content": content}])
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
                            if tr is not None:
                                tr["t_early"] = time.time()
                            hz = None if m.group(1) == "null" else json.loads(m.group(1)) or None
                            u = max(0.0, min(1.0, float(m.group(2))))
                            self._deliver(Detection(
                                hz, where_from_x(m.group(3)) if hz else None, m.group(4) == "true",
                                u if hz else 0.0, early_ms,
                                {"frame_ts": frame.ts, "gaze": frame.gaze_xy, "early": True, "call_id": call_id},
                                frame=frame),
                                frame.ts, 0)
                resp = st.get_final_message()
        else:
            resp = self.client.messages.create(**kwargs)
            text = "".join(b.text for b in resp.content if b.type == "text")
        latency = (time.perf_counter() - t0) * 1000
        u = resp.usage
        if tr is not None:
            tr["t_full"] = time.time()
        raw = {"frame_ts": frame.ts, "call_id": call_id, "gaze": frame.gaze_xy, "text": text, "stop_reason": resp.stop_reason,
               "n_triggers": len(trig), "n_images": len(history), "early_ms": early_ms, "input_tokens": u.input_tokens,
               "output_tokens": u.output_tokens, "cache_read": getattr(u, "cache_read_input_tokens", 0)}
        data = parse_json(text)
        if data is None:
            return Detection(None, None, False, 0.0, latency, raw | {"parse_error": True}, frame=frame, trace=tr)
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
                out = {"match": bool(t.get("match")), "urgency": tu,
                       "where": where_from_x(t.get("x")), "seen_by_user": bool(t.get("seen_by_user")),
                       "reason": str(t.get("reason") or "")[:200]}
                for k in ("bbox", "subject_bbox", "target_bbox", "ordinal", "touching"):
                    if k in t:
                        out[k] = t[k]
                triggers[str(tid)] = out
        return Detection(hazard, where, bool(data.get("seen_by_user")), urgency if hazard else 0.0, latency, raw,
                         reason=str(data.get("reason") or "")[:200], triggers=triggers, frame=frame, trace=tr)

    # ---- scheduling ---------------------------------------------------------------------
    def _deliver(self, det: Detection, frame_ts: float, stage: int) -> None:
        with self._lock:
            # stale = an older frame finished after a newer one. Full results are only compared with full ones
            # (a newer call's early fields often arrive before an older call's full reply, which still carries
            # the custom triggers); an early result is stale if any newer result of either stage was delivered.
            newest = self._last_delivered[1] if stage == 1 else max(self._last_delivered)
            if frame_ts <= newest:
                # an older call that FOUND a custom trigger still counts (calls overlap, so with concurrency 3 a
                # match often finishes after a newer no-match call); the engine's staleness gates still apply
                if stage == 1 and any(isinstance(t, dict) and t.get("match") for t in (det.triggers or {}).values()):
                    det.raw["out_of_order"] = True
                    self._results.append(det)
                    return
                if stage == 1 and self.on_stale is not None:
                    det.raw["stale"] = True
                    try:
                        self.on_stale(det)
                    except Exception:
                        pass
                return
            self._last_delivered[stage] = frame_ts
            self._results.append(det)

    def _run_one(self, frame: Frame, history: list | None = None, call_id: int = 0,
                 t_submit: float | None = None) -> None:
        import anthropic

        t0 = time.time()
        try:
            det = self.call(frame, history, call_id, t_submit)
        except anthropic.APIError as e:
            det = Detection(None, None, False, 0.0, (time.time() - t0) * 1000,
                            {"frame_ts": frame.ts, "call_id": call_id, "error": f"{type(e).__name__}: {e}"},
                            frame=frame,
                            trace={"call_id": call_id, "t_submit": t_submit or t0, "t_start": t0,
                                   "t_full": time.time(), "images": [], "user_text": self.user_text(self.triggers),
                                   "triggers": [list(t) for t in self.triggers], "model": self.model}
                            if self.trace else None)
        finally:
            with self._lock:
                self._in_flight -= 1
        self._deliver(det, frame.ts, 1)

    def analyze(self, frame: Frame) -> Detection | None:
        now = time.time()
        if self.temporal:
            self._remember(frame)
        with self._lock:
            self._pending = frame
            launch = (self._in_flight < self.concurrency and now - self._last_dispatch >= self.min_interval_s)
            if launch:
                self._in_flight += 1
                self._last_dispatch = now
                f, self._pending = self._pending, None
                self._call_id += 1
                cid = self._call_id
            det = self._results.pop(0) if self._results else None
        if launch:
            self._pool.submit(self._run_one, f, self.history_for(f), cid, now)
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

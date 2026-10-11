"""Fast gaze decisions: don't wait for Haiku to re-judge gaze.

Haiku is still the single detector; it *localises* what each custom trigger describes (bbox per trigger id). The
cache keeps the latest box per trigger for `ttl_s`, and every gaze sample is checked against those boxes, so
gaze transitions on an object Haiku has already found fire within ~dwell/grace (0.2–0.3 s) instead of ~1–2 s.

    cache = GazeCache()
    cache.update(trigger_id, bbox, frame_ts, matched=True, label="lion")   # from each full Haiku result
    cache.motion(frame_small_gray, ts)                                     # every frame: invalidates on head motion
    for d in cache.on_gaze(x, y, ts, triggers):                            # every gaze sample (normalized 0..1)
        ...  # d = Decision(trigger_id, fire=True/False, why="...")

`triggers` is a list of (id, gaze_mode, notice_memory_s). Cooldowns stay in the rules; fire=True means "the gaze
condition was met just now on a fresh box".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MARGIN = 0.04


def inside(box, x, y, margin=MARGIN) -> bool:
    x0, y0, x1, y1 = box
    return x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin


@dataclass
class Entry:
    box: tuple
    frame_ts: float
    label: str = ""
    urgency: float = 0.0
    in_since: float | None = None      # gaze continuously inside since (looking_at dwell)
    out_since: float | None = None     # gaze continuously outside since (unseen grace)
    noticed_ts: float | None = None    # last time the gaze was inside (noticed memory)
    fired_ts: float | None = None


@dataclass
class Decision:
    trigger_id: str
    fire: bool
    why: str
    box: tuple | None = None
    label: str = ""
    urgency: float = 0.0
    box_age_s: float = 0.0
    looked_at_s_ago: float | None = None


@dataclass
class GazeCache:
    ttl_s: float = 1.5            # a box is trusted this long after the frame Haiku saw
    dwell_s: float = 0.2          # looking_at: gaze inside the box this long
    grace_s: float = 0.3          # unseen: gaze outside the box this long (and not noticed recently)
    motion_thresh: float = 18.0   # mean abs diff (0-255) on a 32x24 gray thumbnail = big head move
    entries: dict = field(default_factory=dict)
    _ref: np.ndarray | None = None
    invalid_reason: str = ""

    # ---- inputs -----------------------------------------------------------------------
    def update(self, trigger_id: str, bbox, frame_ts: float, matched: bool, label: str = "",
               urgency: float = 0.0) -> None:
        """From each full Haiku result. Not matched -> drop the box (the thing is gone)."""
        if not matched or not bbox or len(bbox) != 4:
            self.entries.pop(trigger_id, None)
            return
        e = self.entries.get(trigger_id)
        box = tuple(float(v) for v in bbox)
        if e is None:
            self.entries[trigger_id] = Entry(box, frame_ts, label, urgency)
        elif frame_ts >= e.frame_ts:
            e.box, e.frame_ts, e.label, e.urgency = box, frame_ts, label or e.label, urgency
        self.invalid_reason = ""

    def motion(self, gray_small: np.ndarray, ts: float) -> bool:
        """Call per frame with a small grayscale image (e.g. 32x24). Returns True if the cache was invalidated."""
        g = gray_small.astype(np.float32)
        if self._ref is not None and self._ref.shape == g.shape:
            if float(np.mean(np.abs(g - self._ref))) > self.motion_thresh and self.entries:
                self.entries.clear()
                self.invalid_reason = "head moved"
                self._ref = g
                return True
        if self._ref is None or not self.entries:
            self._ref = g                  # reference = the view the current boxes belong to
        return False

    # ---- per gaze sample ----------------------------------------------------------------
    def on_gaze(self, x: float, y: float, ts: float, triggers) -> list[Decision]:
        out = []
        for tid, mode, notice_s in triggers:
            e = self.entries.get(tid)
            if e is None:
                out.append(Decision(tid, False, self.invalid_reason or "not matched (no box)"))
                continue
            age = ts - e.frame_ts
            if age > self.ttl_s:
                out.append(Decision(tid, False, f"box stale ({age:.1f} s)", e.box, e.label, e.urgency, age))
                continue
            now_in = inside(e.box, x, y)
            if now_in:
                e.in_since = e.in_since if e.in_since is not None else ts
                e.out_since = None
                e.noticed_ts = ts
            else:
                e.out_since = e.out_since if e.out_since is not None else ts
                e.in_since = None
            ago = None if e.noticed_ts is None else round(ts - e.noticed_ts, 2)
            d = Decision(tid, False, "", e.box, e.label, e.urgency, round(age, 2), ago)
            if mode == "looking_at":
                if now_in and ts - e.in_since >= self.dwell_s:
                    d.fire, d.why = True, f"gaze in box {ts - e.in_since:.2f} s"
                else:
                    d.why = "gaze outside box" if not now_in else f"dwell {ts - e.in_since:.2f}/{self.dwell_s} s"
            elif mode == "unseen":
                noticed_recently = e.noticed_ts is not None and ts - e.noticed_ts <= notice_s
                if not now_in and not noticed_recently and ts - e.out_since >= self.grace_s:
                    d.fire, d.why = True, "matched, gaze elsewhere"
                elif now_in:
                    d.why = "looking at it"
                elif noticed_recently:
                    d.why = f"noticed {ts - e.noticed_ts:.1f} s ago"
                else:
                    d.why = f"grace {ts - e.out_since:.2f}/{self.grace_s} s"
            else:
                d.why = "gaze mode any: decided per Haiku result"
            out.append(d)
        return out

    def snapshot(self, ts: float) -> dict:
        """For the /live overlay."""
        return {tid: {"box": e.box, "label": e.label, "age_s": round(ts - e.frame_ts, 2),
                      "valid": ts - e.frame_ts <= self.ttl_s,
                      "looked_at_s_ago": None if e.noticed_ts is None else round(ts - e.noticed_ts, 2)}
                for tid, e in self.entries.items()} | ({"_invalid": self.invalid_reason} if self.invalid_reason else {})

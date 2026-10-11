"""Data passed between pipeline stages: source -> detector -> rules -> sink."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Frame:
    ts: float                                # capture time, unix seconds
    image: np.ndarray                        # BGR scene frame
    gaze_xy: tuple[float, float] | None      # None when gaze is missing / not worn
    gaze_norm: bool = False                  # True: gaze in 0..1, False: scene-camera pixels
    # origin is always top-left (Neon/Capture's bottom-left y is flipped in the source)

    def gaze_px(self) -> tuple[float, float] | None:
        if self.gaze_xy is None:
            return None
        if not self.gaze_norm:
            return self.gaze_xy
        h, w = self.image.shape[:2]
        return self.gaze_xy[0] * w, self.gaze_xy[1] * h


@dataclass
class Detection:
    hazard: str | None          # e.g. "bike", None when nothing found
    where: str | None           # e.g. "left", "behind-right"
    seen_by_user: bool
    urgency: float              # 0..1
    latency_ms: float = 0.0     # time the detector took
    raw: dict = field(default_factory=dict)


@dataclass
class HapticEvent:
    pattern: str                # a key of spidey_haptics.PATTERNS
    dot: int | None             # 1-based dot index, None = all dots
    reason: str
    ts: float
    rule: str = ""

"""Walking / head-turn detection from the Neon glasses' IMU (accelerometer in g, gyro in deg/s, ~110 Hz).

    w = WalkDetector()
    w.add(ts, (ax, ay, az), (gx, gy, gz))       # every IMU sample
    w.state(now) -> {"walking": bool | None, "cadence_spm": float | None, "turning": bool | None}

Walking = the steady up-and-down bounce of steps: the acceleration magnitude oscillates (std above ~0.04 g)
with a clear period of 0.33-0.8 s (75-180 steps/min), found by autocorrelation over the last 2.5 s. A single
nod or random vibration has no repeating period, so it doesn't count. Hysteresis: walking starts after the
pattern holds for `on_s` and ends after it's gone for `off_s`. Turning = gyro speed above 60 deg/s (median of
the last 0.3 s): the view is changing fast. Relative signals only; no distances.
None = no IMU data (old engine, fake source): callers treat unknown as "don't block".
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

WINDOW_S = 2.5
MIN_STD_G = 0.04
MIN_ACORR = 0.4
PERIOD_S = (0.33, 0.8)
TURN_DPS = 60.0
RESAMPLE_HZ = 50.0


def walk_metrics(ts: np.ndarray, mag: np.ndarray) -> tuple[bool, float | None, float, float]:
    """(steps_like, cadence_spm, std_g, acorr_peak) for one window of |accel| samples (pure, for tests)."""
    if len(ts) < 20 or ts[-1] - ts[0] < WINDOW_S * 0.8:
        return False, None, 0.0, 0.0
    grid = np.arange(ts[0], ts[-1], 1 / RESAMPLE_HZ)
    x = np.interp(grid, ts, mag)
    x = x - x.mean()
    std = float(x.std())
    if std < 1e-6:
        return False, None, std, 0.0
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac = ac / ac[0]
    lo, hi = int(PERIOD_S[0] * RESAMPLE_HZ), min(int(PERIOD_S[1] * RESAMPLE_HZ), len(ac) - 1)
    if hi <= lo:
        return False, None, std, 0.0
    k = lo + int(np.argmax(ac[lo:hi + 1]))
    peak = float(ac[k])
    ok = std >= MIN_STD_G and peak >= MIN_ACORR
    return ok, (round(60.0 / (k / RESAMPLE_HZ), 0) if ok else None), std, peak


class WalkDetector:
    def __init__(self, on_s: float = 0.8, off_s: float = 1.2):
        self.samples: deque = deque()          # (ts, |accel| g, |gyro| deg/s)
        self.on_s, self.off_s = on_s, off_s
        self.walking = False
        self._since: float | None = None       # when the raw verdict last flipped away from self.walking
        self.cadence: float | None = None
        self.last_ts: float | None = None
        self._eval_ts = 0.0
        self._cache = {"walking": None, "cadence_spm": None, "turning": None}

    def add(self, ts: float, accel, gyro) -> None:
        a = math.sqrt(sum(float(v) ** 2 for v in accel))
        g = math.sqrt(sum(float(v) ** 2 for v in gyro))
        self.samples.append((ts, a, g))
        self.last_ts = ts
        while self.samples and self.samples[0][0] < ts - WINDOW_S:
            self.samples.popleft()

    def state(self, now: float | None = None) -> dict:
        """Cheap to call per frame: re-evaluates at most every 0.1 s."""
        if self.last_ts is None:
            return {"walking": None, "cadence_spm": None, "turning": None}
        now = self.last_ts if now is None else now
        if now - self.last_ts > 1.0:                      # IMU stream stalled: unknown
            return {"walking": None, "cadence_spm": None, "turning": None}
        if now - self._eval_ts < 0.1:
            return self._cache
        self._eval_ts = now
        s = list(self.samples)
        ts = np.array([x[0] for x in s])
        steps, cad, _, _ = walk_metrics(ts, np.array([x[1] for x in s]))
        if steps != self.walking:
            self._since = self._since if self._since is not None else now
            if now - self._since >= (self.on_s if steps else self.off_s):
                self.walking, self._since = steps, None
        else:
            self._since = None
        if steps and cad:
            self.cadence = cad
        recent = [x[2] for x in s if x[0] >= self.last_ts - 0.3]
        turning = bool(recent) and float(np.median(recent)) > TURN_DPS
        self._cache = {"walking": self.walking, "cadence_spm": self.cadence if self.walking else None,
                       "turning": turning}
        return self._cache

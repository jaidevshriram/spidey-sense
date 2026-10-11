"""Scene frames + gaze. Swap FakeSource for NeonSource without touching the rest.

Every source yields Frame objects from frames(); that's the whole contract.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from .types import Frame


class GazeFrameSource:
    def frames(self) -> Iterator[Frame]:
        raise NotImplementedError

    def stats(self) -> dict:
        """Live source health for the app's status message (fps/gaze_hz are measured by the engine)."""
        return {}

    def close(self) -> None:
        pass


class FakeSource(GazeFrameSource):
    """Loops a still image or video file, or draws a synthetic scene with a moving circle.

    paths: several stills shown in turn, `hold_s` seconds each (overrides path).
    gaze: "fixed" (looks at gaze_point), "sweep" (scans left-right),
          "follow" (tracks the synthetic circle, a little late) or
          "script": gaze_script = [[t, x, y], ...] normalized, held until the next t,
          looping every gaze_period seconds (default: one playlist cycle).
    """

    def __init__(self, path: str | None = None, paths: list[str] | None = None, hold_s: float = 5,
                 fps: float = 15, size=(800, 600), gaze: str = "fixed", gaze_point=(0.5, 0.5),
                 gaze_script: list | None = None, gaze_period: float | None = None,
                 start_at: float | None = None, loop: bool = True):
        self.path, self.fps, self.size = path, fps, size
        self.gaze, self.gaze_point = gaze, tuple(gaze_point)
        self.gaze_script = sorted(gaze_script or [[0, 0.5, 0.5]])
        self.hold_s = hold_s
        self.start_at = start_at      # unix time of scene t=0 (desk-test harness: known timeline), else first frame
        self.loop = loop              # video: False = hold the last frame instead of looping
        self._cap = None
        self._last_img, self._vidx = None, -1
        self._still = None
        self._playlist = []
        for p in paths or []:
            img = cv2.imread(str(Path(p).expanduser()))
            if img is None:
                raise FileNotFoundError(p)
            self._playlist.append(img)
        self.gaze_period = gaze_period or (len(self._playlist) * hold_s if self._playlist else 10)
        if path and not paths:
            if Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                self._still = cv2.imread(path)
                if self._still is None:
                    raise FileNotFoundError(path)
            else:
                self._cap = cv2.VideoCapture(path)
                if not self._cap.isOpened():
                    raise FileNotFoundError(path)

    def object_pos(self, t: float) -> tuple[float, float]:
        """Synthetic circle path, normalized: drifts in from the left edge every 10 s."""
        p = (t % 10) / 10
        return 0.05 + 0.6 * p, 0.5 + 0.15 * math.sin(t)

    def _gaze(self, t: float) -> tuple[float, float]:
        if self.gaze == "sweep":
            return 0.5 + 0.35 * math.sin(t * 0.8), 0.5
        if self.gaze == "follow":
            return self.object_pos(t - 0.5)
        if self.gaze == "script":
            tt = t % self.gaze_period
            x, y = self.gaze_script[0][1:]
            for k, kx, ky in self.gaze_script:
                if tt >= k:
                    x, y = kx, ky
            return x, y
        return self.gaze_point

    def current_index(self, t: float) -> int:
        return int(t // self.hold_s) % len(self._playlist) if self._playlist else 0

    def _image(self, t: float) -> np.ndarray:
        if self._playlist:
            return self._playlist[self.current_index(t)].copy()
        if self._still is not None:
            return self._still.copy()
        if self._cap is not None:
            # follow the clock (not one file frame per yielded frame), so the video plays in real time
            vfps = self._cap.get(cv2.CAP_PROP_FPS) or self.fps
            n = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            idx = int(t * vfps)
            idx = idx % n if (n and self.loop) else min(idx, max(n - 1, 0))
            if self._last_img is None or idx != self._vidx:
                if idx != self._vidx + 1:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, img = self._cap.read()
                if ok:
                    self._last_img, self._vidx = img, idx
            return self._last_img.copy()
        w, h = self.size
        img = np.full((h, w, 3), 45, np.uint8)
        img[::50, :] = 60
        img[:, ::50] = 60
        ox, oy = self.object_pos(t)
        r = int(20 + 60 * ((t % 10) / 10))  # grows as it "approaches"
        cv2.circle(img, (int(ox * w), int(oy * h)), r, (40, 120, 255), -1)
        return img

    def frames(self) -> Iterator[Frame]:
        t0 = self.start_at or time.time()
        if t0 > time.time():          # harness: scene t=0 is a wall-clock moment; no frames before it
            time.sleep(t0 - time.time())
        while True:
            now = time.time()
            t = max(0.0, now - t0)
            yield Frame(ts=now, image=self._image(t), gaze_xy=self._gaze(t), gaze_norm=True)
            time.sleep(max(0.0, 1 / self.fps - (time.time() - now)))

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()


class NeonSource(GazeFrameSource):
    """Pupil Labs Neon, via neon/neon_app in this repo (copied from neon-gaze-app).

    mode="capture":  headset on USB -> Pupil Capture on this Mac (Pupil Remote, port 50020)
    mode="realtime": Neon Companion phone over Wi-Fi (pupil_labs.realtime_api.simple):
                     host (phone IP) -> else the default gateway (phone hotspot) -> else mDNS.
                     Frame.ts = phone capture time on the Mac clock, so e2e_ms includes Wi-Fi.
    Both return gaze in scene-camera pixels, origin top-left.
    Needs in this venv: pyzmq + msgpack (capture) or
    pupil-labs-realtime-api (realtime).
    """

    APP_DIR = Path(__file__).resolve().parents[2] / "neon"

    def __init__(self, mode: str = "realtime", host: str | None = None, app_dir: str | None = None,
                 max_frame_age_ms: float = 600, keep_every: int = 30):
        app = Path(app_dir).expanduser() if app_dir else self.APP_DIR
        sys.path.insert(0, str(app))
        from neon_app import open_source  # lazy: only needed with real hardware

        self.mode = mode
        self._src = open_source(mode, host=host, with_video=True)
        self._ages: list[float] = []   # realtime: phone capture -> Mac receive, ms
        self._delay_ms: float | None = None   # last median capture -> Mac age
        self._recent_ages: list[float] = []
        self._worn: bool | None = None
        self._battery: tuple[float, int | None] = (0.0, None)   # (checked_at, percent)
        # backlog drain: frames older than this are dropped so we always work on "now" (hotspot hiccups
        # queue up seconds of video); every keep_every-th old frame still passes so a slow link never starves
        self.max_frame_age_ms, self.keep_every = max_frame_age_ms, keep_every
        self._dropped, self._drop_run = 0, 0
        self.min_yield_s, self._last_yield = 0.25, 0.0

    def stats(self) -> dict:
        """worn / battery / delay_ms for the app's status message. Battery comes from the Companion's
        status updates (realtime mode only); read at most every 10 s."""
        out = {"mode": self.mode, "worn": self._worn, "delay_ms": self._delay_ms}
        dev = getattr(self._src, "device", None)
        if dev is not None:
            checked, pct = self._battery
            if time.time() - checked > 10:
                try:
                    pct = int(dev.battery_level_percent)
                except Exception:
                    pct = None
                self._battery = (time.time(), pct)
            out["battery"] = pct
        return out

    def frames(self) -> Iterator[Frame]:
        while True:
            s = self._src.next()
            if s.frame is None:
                continue
            now = time.time()
            gaze = None if (not s.worn or math.isnan(s.x)) else (s.x, s.y)
            self._worn = bool(s.worn)
            if self.mode == "realtime":
                # s.t = the phone's capture time already converted to this Mac's clock (Time Echo
                # offset), so ts -> cue (e2e_ms) includes the Wi-Fi hop. Guard against a bad offset.
                age = (now - s.t) * 1000
                self._ages.append(age)
                self._recent_ages = (self._recent_ages + [age])[-30:]
                if len(self._recent_ages) >= 5:
                    r = sorted(self._recent_ages)
                    self._delay_ms = round(r[len(r) // 2])
                if len(self._ages) >= 150:
                    a = sorted(self._ages)
                    print(f"[neon] {len(a)} frames · capture->Mac age median {a[len(a) // 2]:.0f} ms, "
                          f"p90 {a[int(len(a) * .9)]:.0f} ms · gaze {'ok' if gaze else 'NOT WORN'}", flush=True)
                    self._ages.clear()
                ts = s.t if 0 <= age < 5000 else now
                # a lagging link (all frames late) still yields >= 4 fps, so gaze + live view keep moving;
                # the staleness gates downstream use the true capture time
                if age > self.max_frame_age_ms and self._drop_run < self.keep_every \
                        and now - self._last_yield < self.min_yield_s:
                    self._dropped += 1
                    self._drop_run += 1
                    if self._dropped % 100 == 1:
                        print(f"[neon] dropping stale frames (age {age:.0f} ms > {self.max_frame_age_ms:.0f}); "
                              f"{self._dropped} dropped so far", flush=True)
                    continue
                self._drop_run = 0
                self._last_yield = now
            else:
                ts = now   # Capture's s.t is Pupil time, not unix time: use local receive time
            yield Frame(ts=ts, image=s.frame, gaze_xy=gaze, gaze_norm=False)

    def close(self) -> None:
        self._src.close()


def make_source(cfg: dict) -> GazeFrameSource:
    kind = cfg.get("type", "fake")
    params = {k: v for k, v in cfg.items() if k != "type"}
    if kind == "fake":
        return FakeSource(**params)
    if kind == "neon":
        return NeonSource(**params)
    raise ValueError(f"unknown source type {kind!r}")

"""Gaze sources behind one small interface, so app code doesn't care where gaze comes from.

    src = open_source("capture")   # Neon/Core plugged into the Mac, via Pupil Capture (default)
    src = open_source("mock")      # fake gaze, no hardware
    src = open_source("realtime")  # Neon Companion phone over Wi-Fi (Real-time API)

    s = src.next()   # -> Sample(frame, x, y, confidence, worn, t)
    src.start_recording(); src.event("trial_start"); src.stop_recording(); src.close()
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

# Neon scene camera resolution (used by mock / as a fallback)
SCENE_W, SCENE_H = 1600, 1200


@dataclass
class Sample:
    frame: np.ndarray | None  # BGR scene frame (None in gaze-only mode)
    x: float                  # gaze x in scene-camera pixels (origin top-left)
    y: float                  # gaze y in scene-camera pixels
    confidence: float         # 0..1 (Capture: pupil detection confidence; Neon phone: always 1)
    worn: bool                # True when the sample is usable
    t: float                  # timestamp in seconds (Capture: Pupil time; others: unix time)


# ---------------------------------------------------------------------------
# Pupil Capture (headset on USB, no phone)
# ---------------------------------------------------------------------------
class CaptureSource:
    """Live data from Pupil Capture's Network API (Pupil Remote, default port 50020).

    Capture must already be running with the headset connected and calibrated.
    """

    MIN_CONFIDENCE = 0.6

    def __init__(self, host: str = "127.0.0.1", port: int = 50020, with_video: bool = True):
        import msgpack
        import zmq

        self._msgpack = msgpack
        self._zmq = zmq
        self.with_video = with_video
        self.ctx = zmq.Context.instance()

        print(f"Connecting to Pupil Capture at {host}:{port} ...")
        self.remote = self.ctx.socket(zmq.REQ)
        self.remote.setsockopt(zmq.RCVTIMEO, 3000)
        self.remote.setsockopt(zmq.LINGER, 0)
        self.remote.connect(f"tcp://{host}:{port}")
        try:
            sub_port = self._req("SUB_PORT")
            pub_port = self._req("PUB_PORT")
        except zmq.Again:
            raise RuntimeError(
                "Pupil Capture didn't answer. Is it running (with sudo on macOS) and is the "
                "'Pupil Remote' plugin enabled on port 50020?"
            ) from None

        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.connect(f"tcp://{host}:{sub_port}")
        self.sub.subscribe("gaze.")
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.connect(f"tcp://{host}:{pub_port}")

        # Annotations = timestamped event markers saved into Capture recordings.
        self._start_plugin("Annotation_Capture")
        if with_video:
            # Scene frames are published by Capture's Network API plugin (jpeg by default).
            self.sub.subscribe("frame.world")

        self._last_gaze = None  # (x_norm, y_norm, confidence, t)
        time.sleep(0.5)  # let PUB/SUB connections settle
        print(f"Connected. Pupil time = {self.pupil_time():.3f}")

    # -- Pupil Remote helpers ------------------------------------------------
    def _req(self, cmd: str) -> str:
        self.remote.send_string(cmd)
        return self.remote.recv_string()

    def _notify(self, payload: dict) -> str:
        topic = "notify." + payload["subject"]
        self.remote.send_string(topic, flags=self._zmq.SNDMORE)
        self.remote.send(self._msgpack.packb(payload, use_bin_type=True))
        return self.remote.recv_string()

    def _start_plugin(self, name: str, args: dict | None = None) -> None:
        self._notify({"subject": "start_plugin", "name": name, "args": args or {}})

    def pupil_time(self) -> float:
        return float(self._req("t"))

    def calibrate(self) -> None:
        """Start the calibration currently selected in Capture's Calibration menu."""
        self._req("C")

    # -- Streaming -------------------------------------------------------------
    def _recv(self):
        parts = self.sub.recv_multipart()
        topic = parts[0].decode()
        payload = self._msgpack.unpackb(parts[1], raw=False)
        return topic, payload, parts[2:]

    def next(self) -> Sample:
        while True:
            topic, p, extra = self._recv()
            if topic.startswith("gaze."):
                x, y = p["norm_pos"]
                self._last_gaze = (x, y, p["confidence"], p["timestamp"])
                if not self.with_video:
                    return self._sample(None, SCENE_W, SCENE_H)
            elif topic == "frame.world" and extra:
                frame = self._decode_frame(p, extra[0])
                if frame is None:
                    continue
                h, w = frame.shape[:2]
                return self._sample(frame, w, h, frame_t=p["timestamp"])

    @staticmethod
    def _decode_frame(p: dict, blob: bytes):
        """Capture's frame publisher can send jpeg (default), bgr, gray or yuv."""
        import cv2

        fmt = p.get("format", "jpeg")
        w, h = p["width"], p["height"]
        buf = np.frombuffer(blob, np.uint8)
        if fmt == "jpeg":
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if fmt == "bgr":
            return buf.reshape(h, w, 3)
        if fmt == "gray":
            return cv2.cvtColor(buf.reshape(h, w), cv2.COLOR_GRAY2BGR)
        if fmt == "yuv":  # planar YUV 4:2:2
            y = buf[: w * h].reshape(h, w)
            return cv2.cvtColor(y, cv2.COLOR_GRAY2BGR)
        print(f"Unknown frame format {fmt!r}; skipping frame")
        return None

    def _sample(self, frame, w, h, frame_t=None) -> Sample:
        if self._last_gaze is None:
            return Sample(frame, float("nan"), float("nan"), 0.0, False, frame_t or 0.0)
        xn, yn, conf, t = self._last_gaze
        # Pupil's normalized coords have origin bottom-left; flip y for image pixels.
        return Sample(frame, xn * w, (1.0 - yn) * h, conf, conf >= self.MIN_CONFIDENCE, t)

    # -- Recording / events ----------------------------------------------------
    def start_recording(self) -> str:
        return self._req("R")

    def stop_recording(self) -> None:
        self._req("r")

    def event(self, name: str) -> None:
        msg = {"topic": "annotation", "label": name, "timestamp": self.pupil_time(), "duration": 0.0}
        self.pub.send_string(msg["topic"], flags=self._zmq.SNDMORE)
        self.pub.send(self._msgpack.packb(msg, use_bin_type=True))

    def close(self) -> None:
        for s in (self.sub, self.pub, self.remote):
            s.close(linger=0)


# ---------------------------------------------------------------------------
# Neon Companion phone (Real-time API)
# ---------------------------------------------------------------------------
def default_gateway() -> str | None:
    """This machine's default gateway IP. On a phone hotspot that IS the Companion phone."""
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            out = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True,
                                 timeout=3).stdout
            for line in out.splitlines():
                if line.strip().startswith("gateway:"):
                    return line.split(":", 1)[1].strip()
        else:
            out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True,
                                 timeout=3).stdout.split()
            return out[out.index("via") + 1] if "via" in out else None
    except Exception:
        return None
    return None


def companion_answers(ip: str, port: int = 8080, timeout: float = 2.0) -> bool:
    """True if a Neon Companion Real-time API answers at ip:port (GET /api/status)."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{ip}:{port}/api/status", timeout=timeout) as r:
            return json.load(r).get("message") == "Success"
    except Exception:
        return False


def connect_companion(address: str | None = None, port: int = 8080, search_s: float = 10):
    """Device for the Companion phone: given IP, else mDNS discovery, else the default gateway
    (phone hotspot: mDNS usually doesn't work there, but the phone is the gateway)."""
    from pupil_labs.realtime_api.simple import Device, discover_one_device

    if address:
        return Device(address=address, port=port)
    gw = default_gateway()
    if gw and companion_answers(gw, port):   # hotspot case: instant, skip the mDNS wait
        print(f"Neon Companion answered at the default gateway {gw} (phone hotspot)")
        return Device(address=gw, port=port)
    print(f"Searching for a Neon Companion on the local network ({search_s:.0f}s) ...")
    dev = discover_one_device(max_search_duration_seconds=search_s)
    if dev is None:
        raise RuntimeError("No Neon Companion found: not at the default gateway"
                           f" ({gw}) and not via mDNS. Set source.host / pass the phone's IP.")
    return dev


class NeonRealtimeSource:
    """Scene + gaze from the Neon Companion phone. Sample.t is the phone's frame timestamp
    converted to THIS machine's clock (Time Echo offset), so now - t = true frame age."""

    def __init__(self, address: str | None = None, port: int = 8080, with_video: bool = True):
        self.device = connect_companion(address, port)
        self.with_video = with_video
        self.clock_offset_s = 0.0   # mac_time - phone_time
        try:
            est = self.device.estimate_time_offset(number_of_measurements=20)
            if est is not None:
                self.clock_offset_s = est.time_offset_ms.median / 1000
        except Exception as e:  # older Companion app: fall back to NTP-synced clocks
            print(f"(time offset estimation failed: {e}; assuming synced clocks)")
        print(f"Connected: {self.device.phone_name} @ {self.device.phone_ip} · battery "
              f"{self.device.battery_level_percent}% · module {self.device.module_serial or 'NOT CONNECTED'}"
              f" · clock offset {self.clock_offset_s * 1000:+.0f} ms")

    def next(self) -> Sample:
        while True:
            if self.with_video:
                item = self.device.receive_matched_scene_video_frame_and_gaze(timeout_seconds=5)
                if item is None:   # the first frame can take >5 s while the RTSP streams start
                    self._waits = getattr(self, "_waits", 0) + 1
                    if self._waits > 1:
                        print("(no scene video from the Companion: glasses plugged in, app open?)")
                    continue
                self._waits = 0
                g = item.gaze
                t = item.frame.timestamp_unix_seconds + self.clock_offset_s
                return Sample(item.frame.bgr_pixels, g.x, g.y, 1.0, bool(g.worn), t)
            g = self.device.receive_gaze_datum(timeout_seconds=5)
            if g is None:
                continue
            return Sample(None, g.x, g.y, 1.0, bool(g.worn), g.timestamp_unix_seconds + self.clock_offset_s)

    def start_recording(self) -> str:
        return self.device.recording_start()

    def stop_recording(self) -> None:
        self.device.recording_stop_and_save()

    def event(self, name: str) -> None:
        self.device.send_event(name)

    def close(self) -> None:
        self.device.close()


# ---------------------------------------------------------------------------
# Mock (no hardware)
# ---------------------------------------------------------------------------
class MockSource:
    """Synthetic gaze wandering in a figure-8 over a grid."""

    def __init__(self, with_video: bool = True, hz: float = 30.0):
        self.with_video = with_video
        self.dt = 1.0 / hz
        self.t0 = time.time()
        print("Using MOCK gaze source (no device).")

    def _frame(self) -> np.ndarray:
        img = np.full((SCENE_H, SCENE_W, 3), 40, np.uint8)
        img[::100, :] = 70
        img[:, ::100] = 70
        return img

    def next(self) -> Sample:
        time.sleep(self.dt)
        t = time.time()
        s = t - self.t0
        x = SCENE_W / 2 + 500 * math.sin(s * 0.7)
        y = SCENE_H / 2 + 300 * math.sin(s * 1.4)
        return Sample(self._frame() if self.with_video else None, x, y, 1.0, True, t)

    def start_recording(self) -> str:
        return "mock-recording"

    def stop_recording(self) -> None:
        pass

    def event(self, name: str) -> None:
        print(f"[mock event] {name}")

    def calibrate(self) -> None:
        print("[mock] calibrate")

    def close(self) -> None:
        pass


SOURCES = ("capture", "mock", "realtime")


def open_source(kind: str = "capture", host: str | None = None, port: int | None = None,
                with_video: bool = True):
    if kind == "capture":
        return CaptureSource(host or "127.0.0.1", port or 50020, with_video)
    if kind == "mock":
        return MockSource(with_video)
    if kind == "realtime":
        return NeonRealtimeSource(host, port or 8080, with_video)
    raise ValueError(f"unknown source {kind!r}; choose from {SOURCES}")

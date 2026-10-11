"""source -> detector -> rules -> sink, with a JSONL log of every analyzed frame.

The phone app can also edit rules, send feedback and run commands over the WebSocket
(see docs/protocol.md); `Controller` handles those messages.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import yaml

from . import heart as H
from . import rules as R
from . import trace as T
from .detectors import make_detector
from .gaze_cache import GazeCache
from .types import Detection
from .server import EventServer
from .sinks import make_sink
from .sources import make_source

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ("urgent", "pulse", "ambient")   # keys of spidey_haptics.PATTERNS (not imported: no hardware deps)


def load_config(path: str | Path = ROOT / "configs" / "config.yaml") -> dict:
    """Load YAML; file paths inside it (source.path/paths, detector.env_file) are relative to the file."""
    path = Path(path).resolve()
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    def rel(p):
        q = Path(str(p)).expanduser()
        return str(q if q.is_absolute() else (path.parent / q).resolve())

    src, det = cfg.get("source") or {}, cfg.get("detector") or {}
    if src.get("path"):
        src["path"] = rel(src["path"])
    if src.get("paths"):
        src["paths"] = [rel(p) for p in src["paths"]]
    if det.get("env_file"):
        det["env_file"] = rel(det["env_file"])
    cfg["_config_path"] = str(path)
    return cfg


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=3).stdout.strip() or None
    except Exception:
        return None


def make_thumb(frame, bbox=None, target_bbox=None, width: int = 240, quality: int = 70) -> str | None:
    """Base64 JPEG (~240 px wide) of the fired frame with the gaze dot (red) and box(es) drawn."""
    if frame is None or frame.image is None:
        return None
    img = frame.image
    h, w = img.shape[:2]
    s = width / w
    th = max(1, int(h * s))
    small = cv2.resize(img, (width, th), interpolation=cv2.INTER_AREA)
    for box, color in ((target_bbox, (255, 160, 0)), (bbox, (0, 220, 255))):
        if box:
            x0, y0, x1, y1 = box
            cv2.rectangle(small, (int(x0 * width), int(y0 * th)), (int(x1 * width), int(y1 * th)), color, 2)
    g = frame.gaze_px()
    if g is not None:
        cv2.circle(small, (int(g[0] * s), int(g[1] * s)), 5, (0, 0, 255), -1)
        cv2.circle(small, (int(g[0] * s), int(g[1] * s)), 6, (255, 255, 255), 1)
    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(jpg.tobytes()).decode() if ok else None


class Controller:
    """Live engine state the phone app can change: rules, quiet mode, intensity, feedback."""

    def __init__(self, cfg: dict, sink, source_type: str, rules_path: Path, feedback_path: Path):
        self.cfg, self.sink, self.source_type = cfg, sink, source_type
        self.rules_path, self.feedback_path = rules_path, feedback_path
        self.lock = threading.RLock()        # guards rules + settings (socket thread vs engine loop)
        self.sink_lock = threading.Lock()    # one sink call at a time
        self.quiet = False
        self.vision_off = False              # Haiku off: frames + gaze + live view keep flowing, no API calls
        self.paused = False                  # pause: detection + Log keep running, nothing reaches the sink
        self.intensity = 1.0                 # master intensity, applied via sink.control
        self.haptics = getattr(sink, "control", None)   # HapticControl (real or mock dots)
        self.last_frame_ts: float | None = None
        self.last_play_ts: float | None = None
        self.server: EventServer | None = None
        self.detector = None                 # set by run(): receives custom triggers for its prompt
        self.source = None                   # set by run(): .stats() for the status message
        self.memory = R.TriggerMemory()      # gaze history + per-trigger noticed/trend memory
        self.radar = None                    # RadarSense when cfg radar.enabled
        self.heart = None                    # HeartRateSense when cfg heart.enabled
        self._frame_times: deque = deque()   # (ts, has_gaze) receive times, for fps / gaze Hz
        self._ev_lock = threading.Lock()     # event ids + sink calls from the engine loop and the radar thread
        self.n_events = 0
        self.commit = git_commit()
        self.radar_log = None                # the engine's JSONL log (set by run), shared with the radar thread
        self._log_lock = threading.Lock()
        self.trace_sub_ts = 0.0              # last trace.subscribe {on: true} from an app (stream while < 5 s old)
        # One source of truth: the triggers file. Seeded once (config rules, else defaults); after that
        # config `rules:` are ignored and the file (edited live by the app) wins.
        seed = cfg.get("rules_seed")          # optional: a triggers file to copy when rules_path is missing
        if seed and not rules_path.exists():
            seed_path = ROOT / seed
            if seed_path.exists():
                rules_path.parent.mkdir(parents=True, exist_ok=True)
                seeded = json.loads(seed_path.read_text())
                on = set(cfg.get("rules_seed_enable") or [])
                for r in seeded.get("rules") or []:
                    if r.get("id") in on:
                        r["enabled"] = True
                rules_path.write_text(json.dumps(seeded, indent=2) + "\n")
                print(f"triggers: seeded {rules_path} from {seed_path}" + (f" (enabled: {sorted(on)})" if on else ""),
                      flush=True)
        if rules_path.exists():
            try:
                saved = json.loads(rules_path.read_text()).get("rules") or []
                self.rules = R.load_rules(saved) if saved else []   # empty file = no triggers, not defaults
                src = f"{rules_path.name}"
            except (ValueError, OSError) as e:
                print(f"triggers: could not read {rules_path} ({e}); using defaults")
                self.rules, src = R.load_rules(cfg.get("rules")), "defaults (file unreadable)"
        else:
            self.rules = R.load_rules(cfg.get("rules"))
            src = "config rules (seeded)" if cfg.get("rules") else "defaults (seeded)"
            self._save_rules()
        print(f"triggers ({src}, {rules_path}): "
              + ", ".join(f"{r.id}{'' if r.enabled else ' [off]'}{' [custom]' if r.kind == 'custom' else ''}"
                          for r in self.rules), flush=True)

    # ---- used by the engine loop -------------------------------------------------------
    def active_rules(self) -> list[R.Rule]:
        with self.lock:
            return [r for r in self.rules if r.enabled]

    def play(self, pattern: str, dot: int | None, intensity: float | None = None, rgb=None) -> bool:
        """Play unless quiet or paused. Returns True if it reached the sink."""
        if self.quiet or self.paused:
            return False
        with self.sink_lock:
            self.sink.play(pattern, dot, intensity, rgb)
        self.last_play_ts = time.time()
        return True

    # ---- messages for the app ----------------------------------------------------------
    def rules_list(self) -> dict:
        with self.lock:
            return {"type": "rules.list", "rules": [r.to_dict() for r in self.rules]}

    def settings_msg(self) -> dict:
        return {"type": "settings", "quiet": self.quiet, "intensity": self.intensity}

    def devices(self) -> list[dict]:
        now = time.time()
        sink_cfg = self.cfg.get("sink", {}) or {}
        sink_type = sink_cfg.get("type", "console")
        transport = {"datafeel": "usb", "datafeel-ble": "ble"}.get(sink_type, sink_type)
        link = self.haptics.link_info() if self.haptics else {}
        if sink_type.startswith("datafeel"):
            jefe_status = {True: "connected", False: "disconnected"}.get(link.get("ok"), "connecting")
            jefe_detail = (f"{transport.upper()} · dots {link.get('dots')} · read {link.get('ms')} ms"
                           if link.get("ok") else link.get("reason", ""))
        else:
            jefe_status = "simulated"
            jefe_detail = f"{sink_type} sink (mock dots)"
        neon_status = "connected" if self.source_type == "neon" else "simulated"
        if self.source_type == "neon" and (self.last_frame_ts is None or now - self.last_frame_ts > 3):
            neon_status = "disconnected"
        src = self.source_status()
        neon_detail = f"source: {self.source_type}" + (
            f" ({(self.cfg.get('source') or {}).get('mode', 'capture')})" if self.source_type == "neon" else "")
        bits = [f"{src['fps']:.0f} fps" if src.get("fps") else None,
                f"gaze {src['gaze_hz']:.0f} Hz" if src.get("gaze_hz") else None,
                {True: "worn", False: "NOT worn"}.get(src.get("worn")),
                f"battery {src['battery']}%" if src.get("battery") is not None else None,
                f"delay {src['delay_ms']:.0f} ms" if src.get("delay_ms") is not None else None]
        neon_detail += "".join(f" · {b}" for b in bits if b)
        radar = self.radar_status()
        if radar is None:
            radar_card = {"id": "radar", "name": "Radar", "kind": "radar", "status": "off",
                          "detail": "off (radar.enabled: false)", "last_seen": None}
        else:
            last = radar.get("last") or {}
            radar_card = {"id": "radar", "name": "Radar", "kind": "radar",
                          "status": "connected" if radar.get("connected") else "disconnected",
                          "detail": f"{radar.get('url')} · {radar.get('mode') or '?'}"
                                    + (f" · {radar['rate_hz']:.0f} Hz" if radar.get("rate_hz") else "")
                                    + (f" · {last.get('state')} {last.get('distance_m')} m" if last else ""),
                          "last_seen": last.get("ts")}
        n = len(self.server.clients) if self.server else 0
        return [
            {"id": "mac_hub", "name": "Mac hub", "kind": "websocket", "status": "connected",
             "detail": f"{n} app(s) connected", "last_seen": now},
            {"id": "el_jefe", "name": sink_cfg.get("ble_name", "DataFeelCE"), "kind": "haptics",
             "status": jefe_status, "detail": jefe_detail, "transport": transport,
             "sink": sink_type, "last_seen": link.get("last_seen") or self.last_play_ts,
             "link": link},
            {"id": "neon", "name": "Neon", "kind": "eye_tracker", "status": neon_status,
             "detail": neon_detail, "last_seen": self.last_frame_ts, "stats": src},
            radar_card,
            H.oura_card(self.heart_status()),
        ]

    def device_status(self) -> dict:
        return {"type": "device.status", "devices": self.devices()}

    def hello_extra(self) -> dict:
        return {"rules": self.rules_list()["rules"], "devices": self.devices(),
                "settings": {k: v for k, v in self.settings_msg().items() if k != "type"},
                "status": {k: v for k, v in self.status_msg().items() if k != "type"}}

    # ---- status (on connect, every 2 s, on change) --------------------------------------
    def note_frame(self, frame) -> None:
        now = time.time()
        self.last_frame_ts = now
        self._frame_times.append((now, frame.gaze_xy is not None))
        while self._frame_times and self._frame_times[0][0] < now - 2.0:
            self._frame_times.popleft()

    def source_status(self) -> dict:
        now = time.time()
        ft = [x for x in list(self._frame_times) if x[0] >= now - 2.0]
        span = (ft[-1][0] - ft[0][0]) if len(ft) > 1 else 0.0
        fps = round((len(ft) - 1) / span, 1) if span > 0.2 else 0.0
        gaze_hz = round(fps * sum(1 for x in ft if x[1]) / len(ft), 1) if ft else 0.0
        out = {"type": self.source_type, "live": self.last_frame_ts is not None and now - self.last_frame_ts < 3,
               "fps": fps, "gaze_hz": gaze_hz, "worn": None, "battery": None, "delay_ms": None}
        try:
            out.update({k: v for k, v in (self.source.stats() if self.source else {}).items() if k in
                        ("worn", "battery", "delay_ms", "mode")})
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        if self.source_type != "neon":
            out["worn"] = None if not ft else bool(ft[-1][1])
        return out

    def radar_status(self) -> dict | None:
        if self.radar is None:
            return None
        try:
            return self.radar.status()
        except Exception as e:
            return {"connected": False, "error": f"{type(e).__name__}: {e}"}

    def status_msg(self) -> dict:
        det = self.cfg.get("detector") or {}
        sink_type = (self.cfg.get("sink") or {}).get("type", "console")
        return {"type": "status", "paused": self.paused, "haiku": not self.vision_off, "source": self.source_status(),
                "radar": self.radar_status(),
                "about": {"config": Path(self.cfg.get("_config_path", "config.yaml")).name,
                          "model": det.get("model", "claude-haiku-5-5") if det.get("type") == "vlm"
                          else det.get("type", "fake"),
                          "commit": self.commit, "sink": sink_type}}

    def broadcast_status(self) -> None:
        if self.server:
            self.server.broadcast(self.status_msg())

    # ---- firing: one path for camera + radar events --------------------------------------
    def emit(self, events, det=None, t0: float | None = None) -> list[dict]:
        """Print, play (unless quiet/paused), publish to the app. Returns the event dicts (without thumbs)
        for the JSONL log. Thread-safe (engine loop + radar thread)."""
        out = []
        if not events:
            return out
        quiet, paused = self.quiet, self.paused
        with self._ev_lock:
            for e in events:
                flag = " [paused: not played]" if paused else " [quiet: not played]" if quiet else ""
                print(f"[{time.time() - (t0 or time.time()):5.1f}s] -> {e.pattern} [{e.sensor}] ({e.reason}){flag}",
                      flush=True)
                self.play(e.pattern, e.dot, e.intensity, e.rgb)
                if e.sensor == "camera" and det is not None and e.rule:
                    r = next((r for r in self.rules if r.id == e.rule), None)
                    if r is not None and r.temporal:
                        self.memory.fired(r.id)      # hysteresis: disarm until the trend reverses
            t_done = time.time()
            for e in events:
                self.n_events += 1
                ev = {"id": self.n_events, "ts": e.ts, "pattern": e.pattern, "dot": e.dot,
                      "rule": e.rule, "reason": e.reason, "trigger": e.rule,
                      "trigger_name": e.trigger_name, "why": e.why, "sensor": e.sensor,
                      **{k: v for k, v in e.extra.items() if k != "radar_ts"}}
                if det is not None:
                    frame_ts = det.raw.get("frame_ts", t_done)
                    ev["detection"] = {"hazard": det.hazard, "where": det.where,
                                       "seen_by_user": det.seen_by_user, "urgency": det.urgency}
                    ev["latency_ms"] = round((t_done - frame_ts) * 1000, 1)
                    ev["frame_ts"] = frame_ts
                elif e.extra.get("radar") is not None and e.extra.get("radar_ts"):
                    ev["latency_ms"] = round((t_done - e.extra["radar_ts"]) * 1000, 1)
                if quiet:
                    ev["quiet"] = True
                if paused:
                    ev["paused"] = True
                out.append(dict(ev))
                if det is not None and det.raw.get("call_id"):
                    ev["call_id"] = det.raw["call_id"]          # app: link to the trace / raw Haiku output
                    if det.raw.get("text"):
                        ev["raw"] = det.raw["text"][:2000]
                if self.server:
                    if e.sensor == "camera" and det is not None:
                        thumb = make_thumb(det.frame, e.extra.get("bbox"), e.extra.get("target_bbox"))
                        if thumb:
                            ev["thumb"] = thumb
                    self.server.publish(ev)
        return out

    # ---- heart rate (Oura) -----------------------------------------------------------------
    def start_heart(self) -> None:
        self.heart = H.start_from_config(self.cfg, self.on_heart_sample)

    def heart_status(self) -> dict | None:
        if self.heart is None:
            return None
        try:
            return self.heart.status()
        except Exception as e:
            return {"connected": False, "backend": None, "detail": f"{type(e).__name__}: {e}"}

    def on_heart_sample(self, sample: dict) -> None:
        """Called on the heart thread for every reading; evaluates source: heart_rate triggers."""
        with self.lock:
            rules = [r for r in self.rules if r.enabled and r.source == "heart_rate"]
            events = R.apply_heart(rules, sample, time.time()) if rules else []
        out = self.emit(events)
        if out and self.radar_log is not None:
            with self._log_lock:
                self.radar_log.write(json.dumps({"ts": {"heart": sample.get("ts"), "sent": time.time()},
                                                 "heart": sample, "events": out}) + "\n")
                self.radar_log.flush()

    # ---- radar ------------------------------------------------------------------------------
    def start_radar(self) -> None:
        rc = self.cfg.get("radar") or {}
        if not rc.get("enabled"):
            return
        try:
            from .radar import RadarSense
        except ImportError as e:
            print(f"radar: enabled in config but spidey/radar.py is not importable ({e}); continuing without radar",
                  flush=True)
            return
        url = rc.get("url", "ws://localhost:8090")
        try:
            self.radar = RadarSense(url, on_target=self.on_radar_target, side_sign=int(rc.get("side_sign", 1)))
            print(f"radar: listening to {url}", flush=True)
        except Exception as e:
            print(f"radar: could not start ({type(e).__name__}: {e}); continuing without radar", flush=True)
            self.radar = None

    def on_radar_target(self, target: dict) -> None:
        """Called on the radar thread for every non-calm update (and once on calm)."""
        if target.get("state") not in ("approaching", "too_close"):
            return
        now = time.time()
        with self.lock:
            rules = [r for r in self.rules if r.enabled and r.source == "radar"]
            events = R.apply_radar(rules, target, now)
        for e in events:
            if target.get("ts"):
                e.extra["radar_ts"] = target["ts"]
        out = self.emit(events)
        if out and self.radar_log is not None:
            with self._log_lock:
                self.radar_log.write(json.dumps({"ts": {"radar": target.get("ts"), "sent": time.time()},
                                                 "radar": target, "events": out}) + "\n")
                self.radar_log.flush()

    # ---- handlers (run on the server's worker threads) ---------------------------------
    def _save_rules(self) -> None:
        tmp = self.rules_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"rules": [r.to_dict() for r in self.rules]}, indent=2) + "\n")
        tmp.replace(self.rules_path)

    def push_triggers(self) -> None:
        """Hand the enabled custom triggers to the VLM so they're judged in its single call."""
        if self.detector is not None and hasattr(self.detector, "set_triggers"):
            with self.lock:
                self.detector.set_triggers(R.custom_triggers(self.rules))

    def on_reset(self, msg: dict) -> dict:
        with self.lock:
            self.rules = [R.rule_from_dict(r.to_dict()) for r in R.DEFAULT_RULES]
            self._rules_changed()
        return self.rules_list()

    def _rules_changed(self) -> None:
        self.push_triggers()
        self._save_rules()
        if self.server:
            self.server.broadcast(self.rules_list())
        print(f"rules: {[(r.id, r.enabled) for r in self.rules]}", flush=True)

    def on_upsert(self, msg: dict) -> dict:
        new = R.rule_from_dict(msg.get("rule") or {})
        with self.lock:
            last = {r.id: r._last for r in self.rules}   # keep each rule's cooldown clock
            heart = {r.id: r._heart for r in self.rules}  # and heart triggers' armed / sustain state
            dicts = [r.to_dict() for r in self.rules]
            i = next((i for i, d in enumerate(dicts) if d["id"] == new.id), None)
            if i is None:
                dicts.append(new.to_dict())
            else:
                dicts[i] = new.to_dict()
            self.rules = R.load_rules(dicts) if dicts else []  # rebuild the Rule list (unique ids)
            for r in self.rules:
                r._last = last.get(r.id, r._last)
                r._heart = heart.get(r.id, r._heart)
            self._rules_changed()
        return {"type": "ack", "for": "rules.upsert", "ok": True, "id": new.id}

    def on_delete(self, msg: dict) -> dict:
        rid = msg.get("id")
        with self.lock:
            before = len(self.rules)
            self.rules = [r for r in self.rules if r.id != rid]
            ok = len(self.rules) < before
            if ok:
                self._rules_changed()
        return {"type": "ack", "for": "rules.delete", "ok": ok, "id": rid}

    def on_feedback(self, msg: dict) -> dict:
        verdict = msg.get("verdict")
        if verdict not in ("useful", "noisy"):
            return {"type": "ack", "for": "feedback", "ok": False, "error": "verdict must be useful|noisy"}
        event = None
        if self.server:
            event = next((e for e in self.server.history
                          if e.get("id") == msg.get("event_id") and e.get("ts") == msg.get("ts")), None)
        rec = {"received_at": time.time(), "event_id": msg.get("event_id"), "ts": msg.get("ts"),
               "verdict": verdict, "rule": (event or {}).get("rule"), "event": event}
        self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.feedback_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"feedback: event {msg.get('event_id')} -> {verdict}", flush=True)
        return {"type": "ack", "for": "feedback", "ok": True, "event_id": msg.get("event_id")}

    def on_command(self, msg: dict) -> dict:
        name, args = msg.get("name"), msg.get("args") or {}
        ack = {"type": "ack", "for": "command", "name": name, "ok": True}
        if name == "test_pattern":
            pattern = args.get("pattern", "urgent")
            if pattern not in PATTERNS:
                return {**ack, "ok": False, "error": f"unknown pattern {pattern!r}"}
            with self.sink_lock:   # a test ignores quiet mode on purpose
                self.sink.play(pattern, args.get("dot"))
            self.last_play_ts = time.time()
            ack["detail"] = f"played {pattern}"
        elif name == "reset_dot":
            r = self.haptics.baseline("reset button", thermal=True) if self.haptics else {"ok": True}
            ack.update(ok=r["ok"], detail="Dot reset: LED off, vibration stopped, thermal off", ms=r.get("ms"))
        elif name == "stop_all":
            r = self.haptics.stop_all("stop_all") if self.haptics else {"ok": True}
            ack.update(ok=r["ok"], detail="vibration, thermal and LEDs off", ms=r.get("ms"))
        elif name == "set_intensity":
            self.intensity = max(0.0, min(1.0, float(args.get("value", 1.0))))
            if self.haptics:
                self.haptics.intensity = self.intensity   # scales every pattern from now on
            ack["detail"] = f"intensity {self.intensity:.2f}"
            if self.server:
                self.server.broadcast(self.settings_msg())
        elif name == "pause":
            self.paused = bool(args.get("on", not self.paused))
            ack["detail"] = f"pause {'on' if self.paused else 'off'}"
            self.broadcast_status()
        elif name == "haiku":
            self.vision_off = not bool(args.get("on", self.vision_off))
            ack["detail"] = f"haiku {'off' if self.vision_off else 'on'}"
            self.broadcast_status()
        elif name == "quiet":
            self.quiet = bool(args.get("on", not self.quiet))
            ack["detail"] = f"quiet {'on' if self.quiet else 'off'}"
            if self.server:
                self.server.broadcast(self.settings_msg())
        else:
            return {**ack, "ok": False, "error": f"unknown command {name!r}"}
        print(f"command: {name} {args} -> {ack.get('detail')}", flush=True)
        return ack

    def attach(self, server: EventServer) -> None:
        self.server = server
        server.hello_extra = self.hello_extra
        server.on("rules.upsert", self.on_upsert)
        server.on("rules.delete", self.on_delete)
        server.on("rules.reset", self.on_reset)
        server.on("feedback", self.on_feedback)
        server.on("command", self.on_command)
        server.on("trace.subscribe", self.on_trace_subscribe)
        server.add_periodic(5.0, self.device_status)
        server.add_periodic(2.0, self.status_msg)
        if self.haptics:
            for kind in ("haptic.led", "haptic.vibe.sequence", "haptic.vibe.manual", "haptic.preset",
                         "haptic.thermal", "haptic.stop_all", "haptic.reset", "haptic.subscribe", "link.ping"):
                server.on(kind, self.on_haptic)
            self.haptics.on_telemetry = server.broadcast
            self.haptics.on_system = self.system_event

    # ---- "why didn't it fire": per-call trace stream for the app ---------------------------
    def on_trace_subscribe(self, msg: dict) -> dict:
        self.trace_sub_ts = time.time() if msg.get("on", True) else 0.0
        return {"type": "ack", "for": "trace.subscribe", "ok": True}

    def trace_subscribed(self) -> bool:
        return self.server is not None and time.time() - self.trace_sub_ts < 5.0

    # ---- Haptics tab + link check -------------------------------------------------------
    def system_event(self, level: str, text: str) -> None:
        """Link drops etc.; the app shows these in its Log as system rows."""
        print(f"[system/{level}] {text}", flush=True)
        if self.server:
            self.server.broadcast({"type": "system", "ts": time.time(), "level": level, "text": text})

    def on_haptic(self, msg: dict) -> dict:
        h, kind = self.haptics, msg.get("type")
        addr = msg.get("address")
        if kind == "haptic.subscribe":            # app's Haptics tab is open; resend every ~2 s
            h.last_subscribe = time.time() if msg.get("on", True) else 0.0
            return {"type": "ack", "for": kind, "ok": True}
        if kind == "link.ping":
            t0 = time.perf_counter()
            p = h.ping(flash=msg.get("flash", True))
            return {"type": "link.pong", "id": msg.get("id"), "t_app": msg.get("t_app"),
                    "mac_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "ble_ms": p["health"]["ms"], "flash_ms": p["flash_ms"], "link": h.link_info()}
        if kind == "haptic.led":
            r = h.led(msg.get("mode", "global"), tuple(msg.get("rgb") or (255, 255, 255)),
                      msg.get("index"), msg.get("leds"), addr)
        elif kind == "haptic.vibe.sequence":
            r = h.sequence(msg.get("steps"), addr)
        elif kind == "haptic.vibe.manual":
            r = h.manual(msg.get("hz", 170), msg.get("intensity", 0.5), msg.get("on", True), addr)
        elif kind == "haptic.preset":
            import spidey_haptics as sh
            name = msg.get("name")
            if name not in sh.PATTERNS:
                return {"type": "ack", "for": kind, "ok": False, "error": f"unknown preset {name!r}"}
            seq, rgb = sh.PATTERNS[name]
            r = h.play_pattern(name, seq, rgb)
        elif kind == "haptic.thermal":
            r = h.thermal_set(msg.get("on", True), msg.get("intensity"), msg.get("target_c"), addr)
        elif kind == "haptic.stop_all":
            r = h.stop_all("stop_all")
        elif kind == "haptic.reset":
            r = h.baseline("reset button", thermal=True)
        else:
            return {"type": "ack", "for": kind, "ok": False, "error": "unknown haptic message"}
        print(f"haptic: {kind} -> {'ok' if r.get('ok') else r.get('error')} {r.get('ms')} ms", flush=True)
        return {"type": "ack", "for": kind, **r}


def trace_message(rec: dict, det) -> dict:
    """The app's `trace` message; near misses (a trigger matched but didn't fire) carry a thumbnail."""
    msg = T.light_record(rec)
    miss = next((d for d in rec["decisions"] if d.get("matched") and not d.get("fired")), None)
    if miss is not None:
        msg["near_miss"] = True
        t = det.triggers.get(miss["id"]) or {}
        thumb = make_thumb(det.frame, t.get("subject_bbox") or t.get("bbox"), t.get("target_bbox"))
        if thumb:
            msg["thumb"] = thumb
    return msg


def live_state(ctl: Controller, frame, last_call: dict, last_decision: dict) -> dict:
    """Small dict for the viewer's /live page (written by the tracer's live thread): gaze trail (last 1 s),
    each enabled camera trigger's newest box + state."""
    now = time.time()
    g = ctl.memory.gaze
    trail = [[round(ts - frame.ts, 2), round(x, 3), round(y, 3)] for ts, x, y in list(g)[-200:] if ts >= frame.ts - 1.0]
    gnow = trail[-1][1:] if trail else None
    trig = []
    for r in ctl.active_rules():
        if r.source not in R.VISION_SOURCES:
            continue
        d = last_decision.get(r.id) or {}
        box = d.get("bbox")
        cool = r.cooldown_s - (now - r._last)
        if d.get("fired_ts") and now - d["fired_ts"] < 1.5:
            state = "fired"
        elif cool > 0.05:
            state = f"cooldown {cool:.1f} s"
        else:
            state = "armed"
        hint = None
        if box and gnow and r.effective_gaze_mode != "any" and not r.temporal:
            hint = "gaze inside box" if R.in_box(gnow[0], gnow[1], box) else "gaze outside box"
        trig.append({"id": r.id, "name": r.name, "gaze_mode": r.effective_gaze_mode, "temporal": r.temporal,
                     "state": state, "why": d.get("why"), "matched": d.get("matched"), "fired_ts": d.get("fired_ts"),
                     "bbox": box, "target_bbox": d.get("target_bbox"), "hint": hint,
                     "box_age_s": round(now - d["frame_ts"], 2) if d.get("frame_ts") else None})
    return {"frame_ts": frame.ts, "now": now, "frame_age_s": round(now - frame.ts, 3), "gaze": gnow,
            "trail": trail, "paused": ctl.paused, "quiet": ctl.quiet,
            "last_call": {**last_call, "age_s": round(now - last_call["frame_ts"], 2)} if last_call else None,
            "triggers": trig}


def run(cfg: dict, duration_s: float | None = None) -> None:
    source = make_source(cfg.get("source", {}))
    detector = make_detector(cfg.get("detector", {}))
    sink = make_sink(cfg.get("sink", {}))
    ctl = Controller(cfg, sink, (cfg.get("source") or {}).get("type", "fake"),
                     rules_path=ROOT / cfg.get("rules_file", "triggers.json"),
                     feedback_path=ROOT / cfg.get("feedback_log", "logs/feedback.jsonl"))
    ctl.detector, ctl.source = detector, source
    ctl.vision_off = os.environ.get("HAIKU_OFF") == "1"   # start with Haiku paused (network tests)
    ctl.push_triggers()
    every = cfg.get("detect_every_s", 0)  # throttle slow detectors (e.g. a VLM)
    srv_cfg = cfg.get("server", {"enabled": True})
    server = EventServer(port=srv_cfg.get("port", 8765)) if srv_cfg.get("enabled", True) else None
    if server:
        ctl.attach(server)

    det_cfg = cfg.get("detector") or {}
    tracer = T.make_tracer(cfg, ROOT, {"commit": ctl.commit, "detector": {k: v for k, v in det_cfg.items()
                                                                          if k != "env_file"},
                                       "source": (cfg.get("source") or {}).get("type", "fake"),
                                       "sink": (cfg.get("sink") or {}).get("type", "console")})
    system_sha = None
    if hasattr(detector, "system_text"):
        system_sha = tracer.add_system_prompt(detector.system_text) if tracer else T.sha(detector.system_text)
    if tracer and hasattr(detector, "trace"):
        detector.trace = True

        def on_stale(d):        # a full result dropped because a newer frame's result was already delivered
            rec, imgs = T.call_record(d, decisions=None, gaze=None, early=None, t_result=time.time(), events=None,
                                      paused=ctl.paused, quiet=ctl.quiet, system_sha=system_sha, stale=True)
            tracer.submit(rec, imgs)
        detector.on_stale = on_stale
    # staleness gate: don't fire on a result whose frame is older than this (the scene has likely changed)
    max_age = cfg.get("max_result_age_s", 2.5)
    early_info: dict = {}                   # call id -> {t, fired} of its early (streamed) result
    live_every, last_live = 1.0 / float(((cfg.get("trace") or {}).get("live_hz") or 10)), 0.0
    last_call: dict = {}                    # newest full call, for the live view
    last_decision: dict = {}                # trigger id -> newest decision record (+ ts, bbox)

    log_path = ROOT / cfg.get("log", "logs/events.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"source={type(source).__name__} detector={type(detector).__name__} "
          f"sink={type(sink).__name__} rules={[r.name for r in ctl.active_rules()]}  log={log_path}")

    gc_cfg = cfg.get("gaze_cache", {}) or {}
    gcache = GazeCache(ttl_s=gc_cfg.get("ttl_s", 1.5), dwell_s=gc_cfg.get("dwell_s", 0.2),
                       grace_s=gc_cfg.get("grace_s", 0.3)) if gc_cfg.get("enabled", True) else None
    ctl.gaze_cache = gcache

    def gaze_rules():
        return [r for r in ctl.active_rules() if r.kind == "custom" and r.source in R.VISION_SOURCES
                and not r.temporal and r.effective_gaze_mode in ("looking_at", "unseen")]

    def gaze_cache_step(frame):
        """Per frame: gaze vs Haiku's cached boxes -> fire gaze triggers without waiting for the next call."""
        g = frame.gaze_xy
        if gcache is None or g is None:
            return
        h, w = frame.image.shape[:2]
        gx, gy = (g if frame.gaze_norm else (g[0] / w, g[1] / h))
        small = cv2.resize(cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY), (32, 24), interpolation=cv2.INTER_AREA)
        gcache.motion(small, frame.ts)
        rules = {r.id: r for r in gaze_rules()}
        if os.environ.get("DEBUG_GC") and int(frame.ts * 2) % 2 == 0 and abs(frame.ts * 2 - int(frame.ts * 2)) < 0.05:
            print(f"[gc] rules={list(rules)} entries={ {k: (round(frame.ts - e.frame_ts, 2), e.box) for k, e in gcache.entries.items()} } gaze=({gx:.2f},{gy:.2f}) inval={gcache.invalid_reason!r}", flush=True)
        if not rules or not gcache.entries:
            return
        now = time.time()
        for d in gcache.on_gaze(gx, gy, frame.ts, [(r.id, r.effective_gaze_mode, r.notice_memory_s)
                                                   for r in rules.values()]):
            r = rules.get(d.trigger_id)
            if not d.fire or r is None or now - r._last < r.cooldown_s:
                continue
            reason = f"{d.label or r.name}; {d.why} [gaze cache, box {d.box_age_s:.1f} s old]"
            fake = Detection(None, None, r.effective_gaze_mode == "looking_at", d.urgency, 0.0,
                             {"frame_ts": frame.ts, "gaze": frame.gaze_xy, "gaze_cache": True},
                             reason=reason,
                             triggers={r.id: {"match": True, "urgency": d.urgency, "bbox": list(d.box),
                                              "reason": reason, "looked_at_s_ago": d.looked_at_s_ago}})
            fake.frame = frame
            ev = r._fire(fake, now)
            ev.extra["fast"] = "gaze_cache"
            logged = ctl.emit([ev], fake, t_start)
            log.write(json.dumps({"ts": {"frame": frame.ts, "sent": time.time()}, "gaze_cache": True,
                                  "events": logged, "e2e_ms": round((time.time() - frame.ts) * 1000, 1)}) + "\n")

    def gaze_cache_update(det, rules):
        """Each full Haiku result refreshes the boxes (matched + urgent enough) or drops them."""
        if gcache is None or det.raw.get("early"):
            return
        fts = det.raw.get("frame_ts", time.time())
        for r in rules:
            if r.kind != "custom" or r.temporal or r.effective_gaze_mode not in ("looking_at", "unseen"):
                continue
            t = det.triggers.get(r.id) or {}
            ok = bool(t.get("match")) and float(t.get("urgency") or 0) >= r.if_.get("min_urgency", 0.0)
            now = time.time()
            ago = t.get("looked_at_s_ago")
            gcache.update(r.id, t.get("bbox"), fts, ok, (t.get("reason") or "")[:80], float(t.get("urgency") or 0),
                          result_ts=now, noticed_ts=None if ago is None else now - float(ago))

    t_start, last_detect, prev_hazard = time.time(), 0.0, None
    try:
        with open(log_path, "a") as log:
            ctl.radar_log = log
            ctl.start_radar()
            ctl.start_heart()
            for frame in source.frames():
                ctl.note_frame(frame)
                ctl.memory.add_frame(frame)          # gaze history for looked_at_s_ago
                gaze_cache_step(frame)               # fast path: gaze vs cached Haiku boxes, every frame
                if tracer and frame.ts - last_live >= live_every:
                    last_live = frame.ts
                    tracer.live(frame, live_state(ctl, frame, last_call, last_decision))
                if duration_s and time.time() - t_start > duration_s:
                    break
                if ctl.vision_off or time.time() - last_detect < every:
                    continue
                last_detect = t_detect = time.time()

                det = detector.analyze(frame)
                if det is None:  # async detector, no new result yet
                    continue
                t_detected = time.time()
                if not det.latency_ms:
                    det.latency_ms = (t_detected - t_detect) * 1000
                if det.frame is None:
                    det.frame = frame
                # Async detectors report which frame they actually looked at.
                frame_ts = det.raw.setdefault("frame_ts", frame.ts)

                rules = ctl.active_rules()
                full = not det.raw.get("early")
                gaze_tr = {} if full else None
                ctl.memory.annotate(det, rules, t_detected, gaze_tr)     # real gaze vs boxes, temporal trends
                gaze_cache_update(det, rules)
                ctx = {"max_result_age_s": max_age, "gaze_now": ctl.memory.newest_gaze(),
                       "view_changed_ts": gcache.invalidated_ts if gcache else None}
                events, decisions = R.apply_explained(rules, det, t_detected, ctx)
                state = (det.hazard, det.seen_by_user)
                if state != prev_hazard:
                    print(f"[{t_detected - t_start:5.1f}s] hazard={det.hazard} where={det.where} "
                          f"seen={det.seen_by_user} urgency={det.urgency:.2f}", flush=True)
                    prev_hazard = state
                logged = ctl.emit(events, det, t_start)
                t_done = time.time()
                cid = det.raw.get("call_id")
                if cid and not full:
                    early_info[cid] = {"t": t_detected, "fired": [d["id"] for d in decisions if d["fired"]]}
                    while len(early_info) > 50:
                        early_info.pop(next(iter(early_info)))
                elif cid and (tracer or ctl.trace_subscribed()):
                    rec, imgs = T.call_record(det, decisions=decisions, gaze=gaze_tr, early=early_info.pop(cid, None),
                                              t_result=t_detected, events=logged, paused=ctl.paused,
                                              quiet=ctl.quiet, system_sha=system_sha, ctx=ctx)
                    if tracer:
                        tracer.submit(rec, imgs)
                    if ctl.trace_subscribed():
                        ctl.server.broadcast(trace_message(rec, det))
                if full and cid:
                    last_call.update(id=cid, ts=t_detected, frame_ts=frame_ts, hazard=det.hazard, reason=det.reason)
                    for d in decisions:
                        t = det.triggers.get(d["id"]) or {}
                        box = t.get("subject_bbox") if t.get("subject_bbox") else t.get("bbox")
                        last_decision[d["id"]] = {**d, "ts": t_detected, "frame_ts": frame_ts, "bbox": box,
                                                  "target_bbox": t.get("target_bbox"),
                                                  "fired_ts": t_detected if d["fired"]
                                                  else last_decision.get(d["id"], {}).get("fired_ts")}

                with ctl._log_lock:
                    log.write(json.dumps({
                        "ts": {"frame": frame_ts, "detect_start": t_detect,
                               "detected": t_detected, "sent": t_done},
                        "gaze": det.raw.get("gaze", frame.gaze_xy), "gaze_norm": frame.gaze_norm,
                        "detection": {"hazard": det.hazard, "where": det.where,
                                      "seen_by_user": det.seen_by_user, "urgency": det.urgency,
                                      "latency_ms": round(det.latency_ms, 2), "raw": det.raw,
                                      **({"triggers": det.triggers} if det.triggers else {})},
                        "events": logged,
                        **({"paused": True} if ctl.paused else {}),
                        "e2e_ms": round((t_done - frame_ts) * 1000, 2),
                    }, default=str) + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        if ctl.radar is not None:
            try:
                ctl.radar.close()
            except Exception:
                pass
        if ctl.heart is not None:
            try:
                ctl.heart.close()
            except Exception:
                pass
        ctl.radar_log = None
        if tracer:
            tracer.close()
        sink.close()
        detector.close()
        source.close()

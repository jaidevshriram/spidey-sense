"""source -> detector -> rules -> sink, with a JSONL log of every analyzed frame.

The phone app can also edit rules, send feedback and run commands over the WebSocket
(see docs/protocol.md); `Controller` handles those messages.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import yaml

from . import rules as R
from .detectors import make_detector
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
    return cfg


class Controller:
    """Live engine state the phone app can change: rules, quiet mode, intensity, feedback."""

    def __init__(self, cfg: dict, sink, source_type: str, rules_path: Path, feedback_path: Path):
        self.cfg, self.sink, self.source_type = cfg, sink, source_type
        self.rules_path, self.feedback_path = rules_path, feedback_path
        self.lock = threading.RLock()        # guards rules + settings (socket thread vs engine loop)
        self.sink_lock = threading.Lock()    # one sink call at a time
        self.quiet = False
        self.intensity = 1.0                 # master intensity, applied via sink.control
        self.haptics = getattr(sink, "control", None)   # HapticControl (real or mock dots)
        self.last_frame_ts: float | None = None
        self.last_play_ts: float | None = None
        self.server: EventServer | None = None
        if rules_path.exists():
            try:
                saved = json.loads(rules_path.read_text()).get("rules") or []
                self.rules = R.load_rules(saved) if saved else []   # empty file = no rules, not defaults
                print(f"rules: loaded {len(self.rules)} from {rules_path}")
            except (ValueError, OSError) as e:
                print(f"rules: could not read {rules_path} ({e}); using config.yaml")
                self.rules = R.load_rules(cfg.get("rules"))
        else:
            self.rules = R.load_rules(cfg.get("rules"))

    # ---- used by the engine loop -------------------------------------------------------
    def active_rules(self) -> list[R.Rule]:
        with self.lock:
            return [r for r in self.rules if r.enabled]

    def play(self, pattern: str, dot: int | None) -> bool:
        """Play unless quiet. Returns True if it reached the sink."""
        if self.quiet:
            return False
        with self.sink_lock:
            self.sink.play(pattern, dot)
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
        n = len(self.server.clients) if self.server else 0
        return [
            {"id": "mac_hub", "name": "Mac hub", "kind": "websocket", "status": "connected",
             "detail": f"{n} app(s) connected", "last_seen": now},
            {"id": "el_jefe", "name": sink_cfg.get("ble_name", "DataFeelCE"), "kind": "haptics",
             "status": jefe_status, "detail": jefe_detail, "transport": transport,
             "sink": sink_type, "last_seen": link.get("last_seen") or self.last_play_ts,
             "link": link},
            {"id": "neon", "name": "Neon", "kind": "eye_tracker", "status": neon_status,
             "detail": f"source: {self.source_type}" + (
                 f" ({(self.cfg.get('source') or {}).get('mode', 'capture')})" if self.source_type == "neon" else ""),
             "last_seen": self.last_frame_ts},
            {"id": "oura", "name": "Oura", "kind": "heart_rate", "status": "coming_soon",
             "detail": "TODO: heart-rate source not built", "last_seen": None},
        ]

    def device_status(self) -> dict:
        return {"type": "device.status", "devices": self.devices()}

    def hello_extra(self) -> dict:
        return {"rules": self.rules_list()["rules"], "devices": self.devices(),
                "settings": {k: v for k, v in self.settings_msg().items() if k != "type"}}

    # ---- handlers (run on the server's worker threads) ---------------------------------
    def _save_rules(self) -> None:
        tmp = self.rules_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"rules": [r.to_dict() for r in self.rules]}, indent=2) + "\n")
        tmp.replace(self.rules_path)

    def _rules_changed(self) -> None:
        self._save_rules()
        if self.server:
            self.server.broadcast(self.rules_list())
        print(f"rules: {[(r.id, r.enabled) for r in self.rules]}", flush=True)

    def on_upsert(self, msg: dict) -> dict:
        new = R.rule_from_dict(msg.get("rule") or {})
        with self.lock:
            last = {r.id: r._last for r in self.rules}   # keep each rule's cooldown clock
            dicts = [r.to_dict() for r in self.rules]
            i = next((i for i, d in enumerate(dicts) if d["id"] == new.id), None)
            if i is None:
                dicts.append(new.to_dict())
            else:
                dicts[i] = new.to_dict()
            self.rules = R.load_rules(dicts) if dicts else []  # rebuild the Rule list (unique ids)
            for r in self.rules:
                r._last = last.get(r.id, r._last)
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
        server.on("feedback", self.on_feedback)
        server.on("command", self.on_command)
        server.add_periodic(5.0, self.device_status)
        if self.haptics:
            for kind in ("haptic.led", "haptic.vibe.sequence", "haptic.vibe.manual", "haptic.preset",
                         "haptic.thermal", "haptic.stop_all", "haptic.subscribe", "link.ping"):
                server.on(kind, self.on_haptic)
            self.haptics.on_telemetry = server.broadcast
            self.haptics.on_system = self.system_event

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
        else:
            return {"type": "ack", "for": kind, "ok": False, "error": "unknown haptic message"}
        print(f"haptic: {kind} -> {'ok' if r.get('ok') else r.get('error')} {r.get('ms')} ms", flush=True)
        return {"type": "ack", "for": kind, **r}


def run(cfg: dict, duration_s: float | None = None) -> None:
    source = make_source(cfg.get("source", {}))
    detector = make_detector(cfg.get("detector", {}))
    sink = make_sink(cfg.get("sink", {}))
    ctl = Controller(cfg, sink, (cfg.get("source") or {}).get("type", "fake"),
                     rules_path=ROOT / cfg.get("rules_file", "rules.json"),
                     feedback_path=ROOT / cfg.get("feedback_log", "logs/feedback.jsonl"))
    every = cfg.get("detect_every_s", 0)  # throttle slow detectors (e.g. a VLM)
    srv_cfg = cfg.get("server", {"enabled": True})
    server = EventServer(port=srv_cfg.get("port", 8765)) if srv_cfg.get("enabled", True) else None
    if server:
        ctl.attach(server)
    n_events = 0

    log_path = ROOT / cfg.get("log", "logs/events.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"source={type(source).__name__} detector={type(detector).__name__} "
          f"sink={type(sink).__name__} rules={[r.name for r in ctl.active_rules()]}  log={log_path}")

    t_start, last_detect, prev_hazard = time.time(), 0.0, None
    try:
        with open(log_path, "a") as log:
            for frame in source.frames():
                ctl.last_frame_ts = time.time()
                if duration_s and time.time() - t_start > duration_s:
                    break
                if time.time() - last_detect < every:
                    continue
                last_detect = t_detect = time.time()

                det = detector.analyze(frame)
                if det is None:  # async detector, no new result yet
                    continue
                t_detected = time.time()
                if not det.latency_ms:
                    det.latency_ms = (t_detected - t_detect) * 1000
                # Async detectors report which frame they actually looked at.
                frame_ts = det.raw.get("frame_ts", frame.ts)

                events = R.apply(ctl.active_rules(), det, t_detected)
                state = (det.hazard, det.seen_by_user)
                if state != prev_hazard:
                    print(f"[{t_detected - t_start:5.1f}s] hazard={det.hazard} where={det.where} "
                          f"seen={det.seen_by_user} urgency={det.urgency:.2f}", flush=True)
                    prev_hazard = state
                quiet = ctl.quiet
                for e in events:
                    print(f"[{t_detected - t_start:5.1f}s] -> {e.pattern} ({e.reason})"
                          f"{' [quiet: not played]' if quiet else ''}", flush=True)
                    ctl.play(e.pattern, e.dot)
                t_done = time.time()
                for e in events:
                    n_events += 1
                    if server:
                        ev = {
                            "id": n_events, "ts": e.ts, "pattern": e.pattern, "dot": e.dot,
                            "rule": e.rule, "reason": e.reason,
                            "detection": {"hazard": det.hazard, "where": det.where,
                                          "seen_by_user": det.seen_by_user, "urgency": det.urgency},
                            "latency_ms": round((t_done - frame_ts) * 1000, 1),
                        }
                        if quiet:
                            ev["quiet"] = True
                        server.publish(ev)

                log.write(json.dumps({
                    "ts": {"frame": frame_ts, "detect_start": t_detect,
                           "detected": t_detected, "sent": t_done},
                    "gaze": det.raw.get("gaze", frame.gaze_xy), "gaze_norm": frame.gaze_norm,
                    "detection": {"hazard": det.hazard, "where": det.where,
                                  "seen_by_user": det.seen_by_user, "urgency": det.urgency,
                                  "latency_ms": round(det.latency_ms, 2), "raw": det.raw},
                    "events": [{"pattern": e.pattern, "dot": e.dot, "reason": e.reason,
                                **({"quiet": True} if quiet else {})} for e in events],
                    "e2e_ms": round((t_done - frame_ts) * 1000, 2),
                }) + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        sink.close()
        detector.close()
        source.close()

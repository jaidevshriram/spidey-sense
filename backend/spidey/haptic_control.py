"""Everything the app can do to the Dots, behind one lock: patterns (with master intensity), playground
controls (LED / sequences / manual vibration / thermal), telemetry, a link health check, and thermal safety.

Used by the DataFeel sinks (real dots over USB or BLE) and, with mock dots, by the console sink so the
iOS Haptics tab works in the simulator. Only one process may own El Jefe's BLE link: run either the
engine (this) or the web playground (backend/playground), not both.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable

from datafeel.device import LedMode, ThermalMode, VibrationMode, VibrationWaveforms as W

TARGET_CAP_C = 40.0       # thermal target hard cap
SKIN_CUTOFF_C = 41.0      # immediate thermal off above this skin temp
THERMAL_MAX_S = 30.0      # thermal auto-off
SUBSCRIBER_TIMEOUT_S = 5.0  # app stopped sending haptic.subscribe -> thermal off ("off on disconnect")
HEALTH_EVERY_S = 2.0

LED_MODES = {"off": LedMode.OFF, "global": LedMode.GLOBAL_MANUAL, "individual": LedMode.INDIVIDUAL_MANUAL,
             "breathe": LedMode.BREATHE, "track_thermal": LedMode.TRACK_THERMAL}

# ---------------------------------------------------------------------------------------------
# Intensity: library waveforms have fixed strength, so scaling = swapping to a weaker variant.
# ---------------------------------------------------------------------------------------------
_FAMILY_FALLBACK = {"STRONG_BUZZ": "BUZZ", "TRIPLE_CLICK": "STRONG_CLICK", "ALERT_750MS": "BUZZ",
                    "ALERT_1000MS": "BUZZ", "LONG_BUZZ_FOR_PROGRAMMATIC_STOPPING": "BUZZ",
                    "DOUBLE_CLICK": "SHORT_DOUBLE_CLICK_STRONG"}


def _family(name: str) -> tuple[str, int] | None:
    m = re.match(r"^(.*?)\d*_P(\d+)$", name)
    return (m.group(1), int(m.group(2))) if m else None


_VARIANTS: dict[str, list[tuple[int, W]]] = {}
for _w in W:
    _f = _family(_w.name)
    if _f:
        _VARIANTS.setdefault(_f[0], []).append((_f[1], _w))


def scale_waveform(code: int, intensity: float) -> int | None:
    """Weaker variant of a waveform code for `intensity` (0..1). Rests (>=0x80) pass through.
    Returns None to drop the step (intensity 0)."""
    if code >= 0x80 or intensity >= 0.999:
        return code
    if intensity <= 0.01:
        return None
    try:
        fam = _family(W(code).name)
    except ValueError:
        return code
    if not fam:
        return code
    family, p = fam
    target = p * intensity
    cands = list(_VARIANTS.get(family, []))
    if family in _FAMILY_FALLBACK:
        cands += _VARIANTS.get(_FAMILY_FALLBACK[family], [])
    below = [c for c in cands if c[0] <= target + 1e-6]
    pick = max(below, key=lambda c: c[0]) if below else min(cands, key=lambda c: c[0])
    return int(pick[1])


def scale_sequence(seq, intensity: float) -> list[int]:
    out = [scale_waveform(int(c), intensity) for c in seq]
    return [c for c in out if c is not None]


# ---------------------------------------------------------------------------------------------
# Mock dots (console sink / simulator)
# ---------------------------------------------------------------------------------------------
class _MockRegs:
    def __init__(self):
        self.skin, self.sink_t, self.mode, self.intensity, self.target = 31.0, 30.0, 0, 0.0, None
        self.dev = type("Dev", (), {"write_registers": lambda *a, **k: None})()

    def __getattr__(self, name):
        return lambda *a, **k: None

    def get_skin_temperature(self):
        if self.mode == ThermalMode.MANUAL:
            self.skin += 0.08 * self.intensity
        elif self.mode == ThermalMode.TEMPERATURE_TARGET and self.target is not None:
            self.skin += max(-0.08, min(0.08, (self.target - self.skin) * 0.1))
        else:
            self.skin += (31.0 - self.skin) * 0.05
        return self.skin

    def get_sink_temperature(self):
        return self.sink_t + (self.skin - 31.0) * -0.3

    def get_thermal_power(self):
        return round(self.intensity * 2.0, 3) if self.mode else 0.0

    def set_thermal_mode(self, m):
        self.mode = m


class MockDot:
    def __init__(self, id=3):
        self.id = id
        self.registers = _MockRegs()

    def __getattr__(self, name):
        return lambda *a, **k: None

    def disable_all_thermal(self):
        self.registers.mode = 0

    def activate_thermal_intensity_control(self, v):
        self.registers.mode, self.registers.intensity = ThermalMode.MANUAL, v

    def activate_thermal_temperature_control(self, t):
        self.registers.mode, self.registers.target = ThermalMode.TEMPERATURE_TARGET, t
        self.registers.intensity = 0.5


# ---------------------------------------------------------------------------------------------
class HapticControl:
    def __init__(self, dots, transport: str, device_name: str, link=None, rssi: int | None = None,
                 mock: bool = False):
        from dots import fastfire  # haptics/ package

        self.ff = fastfire
        self.dots, self.transport, self.device_name = dots, transport, device_name
        self.link, self.rssi, self.mock = link, rssi, mock
        self.lock = threading.RLock()            # one Modbus transaction at a time on the link
        self.intensity = 1.0
        self.last_cmd_ms: float | None = None
        self.thermal: dict | None = None
        self.last_subscribe = 0.0
        self.health: dict = {"ok": None, "ms": None, "last_seen": None, "dots": [], "reason": "not checked yet"}
        self.on_telemetry: Callable[[dict], None] | None = None   # set by the engine (broadcast)
        self.on_system: Callable[[str, str], None] | None = None  # (level, text) for link drops
        self._stop = threading.Event()
        if not mock:
            with self.lock:
                fastfire.prepare(self.dots)
        threading.Thread(target=self._watchdog, daemon=True).start()

    @classmethod
    def mocked(cls):
        return cls([MockDot(3)], "mock", "mock El Jefe", mock=True)

    # ---- helpers ------------------------------------------------------------------------
    def targets(self, address: int | None):
        if address is None:
            return self.dots
        ts = [d for d in self.dots if d.id == address]
        if not ts:
            raise ValueError(f"no dot at address {address}")
        return ts

    def _run(self, name: str, fn) -> dict:
        t0 = time.perf_counter()
        try:
            with self.lock:
                fn()
            ok, err = True, None
        except Exception as e:  # hardware errors go back to the app, not up the stack
            ok, err = False, f"{type(e).__name__}: {e}"
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self.last_cmd_ms = ms
        return {"ok": ok, "ms": ms, "error": err, "cmd": name}

    # ---- patterns (engine + app presets) -------------------------------------------------
    def play_pattern(self, name: str, seq, rgb, dot_index: int | None = None) -> dict:
        """dot_index: 0-based position in self.dots (engine rules), None = all."""
        scaled = scale_sequence(seq, self.intensity)
        targets = self.dots if dot_index is None else [self.dots[dot_index]]

        def go():
            if any(c < 0x80 for c in scaled):
                self.ff.fire(targets, scaled, rgb)
            else:  # intensity 0: light only
                for d in targets:
                    d.registers.set_led_mode(LedMode.GLOBAL_MANUAL)
                    d.registers.set_global_led(*rgb)
        r = self._run(f"pattern:{name}", go)
        r["intensity"] = self.intensity
        return r

    # ---- playground controls -------------------------------------------------------------
    def led(self, mode="global", rgb=(255, 255, 255), index=None, leds=None, address=None) -> dict:
        if mode not in LED_MODES:
            return {"ok": False, "error": f"mode must be one of {list(LED_MODES)}", "cmd": "led"}
        ds = self.targets(address)

        def go():
            for d in ds:
                if index is not None:   # one LED: individual mode
                    d.registers.set_led_mode(LedMode.INDIVIDUAL_MANUAL)
                    d.registers.set_individual_led(int(index), *rgb)
                    continue
                d.registers.set_led_mode(LED_MODES[mode])
                if mode == "global":
                    d.registers.set_global_led(*rgb)
                elif mode == "individual":
                    for i, c in enumerate((leds or [rgb] * 8)[:8]):
                        d.registers.set_individual_led(i, *c)
        return self._run("led", go)

    @staticmethod
    def parse_steps(steps) -> list[int]:
        seq = []
        for s in (steps or [])[:8]:
            if isinstance(s, (int, float)) or (isinstance(s, str) and s.replace(".", "", 1).isdigit()):
                seq.append(W.Rest(min(1.27, max(0.0, float(s)))))
            elif s:
                seq.append(int(W[str(s)]))
        return seq

    def sequence(self, steps, address=None) -> dict:
        try:
            seq = scale_sequence(self.parse_steps(steps), self.intensity)
        except KeyError as e:
            return {"ok": False, "error": f"unknown waveform {e}", "cmd": "sequence"}
        ds = self.targets(address)
        regs = [r for w in self.ff._seq_words(seq) for r in self.ff._long_regs(w)]

        def go():
            for d in ds:
                d.registers.set_vibration_mode(VibrationMode.LIBRARY)
                d.registers.dev.write_registers(self.ff.SEQ_0123, regs)
                d.start_vibration_sequence()
        return self._run("sequence", go)

    def manual(self, hz=170.0, intensity=0.5, on=True, address=None) -> dict:
        hz = min(300.0, max(50.0, float(hz)))
        amp = min(1.0, max(0.0, float(intensity))) * self.intensity
        ds = self.targets(address)

        def go():
            for d in ds:
                if on:
                    d.play_frequency(hz, amp)
                else:
                    d.stop_vibration()
                    d.registers.set_vibration_mode(VibrationMode.LIBRARY)   # back to patterns
        r = self._run("manual", go)
        r.update(hz=hz, intensity=amp, on=on)
        return r

    def thermal_set(self, on=True, intensity=None, target_c=None, address=None, headless=False) -> dict:
        if not on:
            return self.thermal_off("user")
        ds = self.targets(address)
        if target_c is not None:
            v, mode = min(TARGET_CAP_C, float(target_c)), "target"

            def go():
                for d in ds:
                    d.activate_thermal_temperature_control(v)
        else:
            v, mode = min(1.0, max(-1.0, float(intensity or 0.0))), "manual"

            def go():
                for d in ds:
                    d.activate_thermal_intensity_control(v)
        base = self._read_temps(ds[0])[0] if ds else None
        r = self._run("thermal", go)
        if r["ok"]:
            self.thermal = {"mode": mode, "value": v, "t0": time.time(), "base": base, "reached": {},
                            "address": ds[0].id, "headless": headless}
        r.update(mode=mode, value=v, capped=(mode == "target" and target_c is not None and float(target_c) > TARGET_CAP_C))
        return r

    def thermal_off(self, reason: str) -> dict:
        def go():
            for d in self.dots:
                d.disable_all_thermal()
        was, self.thermal = self.thermal, None
        r = self._run("thermal_off", go)
        r["reason"] = reason
        if was and reason not in ("user", "stop_all") and self.on_system:
            self.on_system("warning", f"Thermal turned off: {reason}")
        return r

    def stop_all(self, reason="stop_all") -> dict:
        self.thermal_off(reason)

        def go():
            for d in self.dots:
                d.stop_vibration()
                d.registers.set_vibration_mode(VibrationMode.LIBRARY)
                d.set_led_off()
        r = self._run("stop_all", go)
        r["reason"] = reason
        return r

    # ---- telemetry + health --------------------------------------------------------------
    def _read_temps(self, d):
        with self.lock:
            return (d.registers.get_skin_temperature(), d.registers.get_sink_temperature(),
                    d.registers.get_thermal_power())

    def telemetry(self) -> dict:
        th = self.thermal
        d = self.targets(th["address"])[0] if th else self.dots[0]
        skin, sink_t, power = self._read_temps(d)
        now = time.time()
        if th:
            if th["base"] is None:
                th["base"] = skin
            delta = skin - th["base"]
            heating = th["value"] >= (th["base"] if th["mode"] == "target" else 0)
            sign = 1 if heating else -1
            for k in (2, 4):
                key = f"{sign * k:+d}C"
                if key not in th["reached"] and sign * delta >= k:
                    th["reached"][key] = round(now - th["t0"], 1)
            if th["mode"] == "target" and "target" not in th["reached"] and abs(skin - th["value"]) <= 0.3:
                th["reached"]["target"] = round(now - th["t0"], 1)
            th["rate_c_per_s"] = round(delta / max(0.5, now - th["t0"]), 3)
        return {"type": "haptic.telemetry", "ts": now, "skin_c": round(skin, 2), "sink_c": round(sink_t, 2),
                "thermal_power": round(power, 3), "last_cmd_ms": self.last_cmd_ms, "intensity": self.intensity,
                "thermal": None if not th else {
                    "mode": th["mode"], "value": th["value"], "base_c": th["base"], "reached": th["reached"],
                    "elapsed_s": round(now - th["t0"], 1),
                    "remaining_s": round(max(0.0, THERMAL_MAX_S - (now - th["t0"])), 1),
                    "rate_c_per_s": th.get("rate_c_per_s")},
                "limits": {"target_cap_c": TARGET_CAP_C, "skin_cutoff_c": SKIN_CUTOFF_C,
                           "thermal_max_s": THERMAL_MAX_S}}

    def check_health(self) -> dict:
        """Read a register from every dot: proves Mac -> El Jefe (BLE/USB) -> Dot is alive."""
        t0 = time.perf_counter()
        answering, err = [], None
        link_up = self.link.is_open if self.link is not None else True
        if link_up:
            for d in self.dots:
                try:
                    with self.lock:
                        d.registers.get_skin_temperature()
                    answering.append(d.id)
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
        ms = round((time.perf_counter() - t0) * 1000, 1)
        ok = link_up and len(answering) == len(self.dots)
        if not link_up:
            reason = f"{self.transport.upper()}: link to {self.device_name} is down. Is El Jefe on and in range?"
        elif not answering:
            reason = f"{self.transport.upper()}: no response from {self.device_name}'s dots ({err})"
        elif not ok:
            reason = f"only dots {answering} answered"
        else:
            reason = "ok"
        prev = self.health.get("ok")
        self.health = {"ok": ok, "ms": ms, "last_seen": time.time() if answering else self.health.get("last_seen"),
                       "dots": answering, "link_up": link_up, "reason": reason}
        if prev is not None and prev != ok and self.on_system:
            self.on_system("info" if ok else "error",
                           f"El Jefe link {'restored' if ok else 'lost'}: {reason}")
        return self.health

    def ping(self, flash=True) -> dict:
        """Full Mac-side round trip for the app's 'Ping all': BLE register read (+ 0.5 s green flash)."""
        h = self.check_health()
        flash_ms = None
        if flash and h["dots"]:
            t0 = time.perf_counter()
            self.led("global", (0, 255, 0))
            flash_ms = round((time.perf_counter() - t0) * 1000, 1)
            threading.Timer(0.5, lambda: self._run("flash_off", lambda: [d.set_led_off() for d in self.dots])).start()
        return {"health": h, "flash_ms": flash_ms}

    def link_info(self) -> dict:
        return {"transport": self.transport, "device": self.device_name, "rssi": self.rssi, **self.health,
                "addresses": [d.id for d in self.dots]}

    # ---- background: safety, telemetry stream, health -----------------------------------
    def _watchdog(self):
        last_health = 0.0
        while not self._stop.is_set():
            try:
                now = time.time()
                subscribed = now - self.last_subscribe < SUBSCRIBER_TIMEOUT_S
                th = self.thermal
                if th or subscribed:
                    t = self.telemetry()
                    if th and t["skin_c"] > SKIN_CUTOFF_C:
                        self.thermal_off(f"skin {t['skin_c']} °C > {SKIN_CUTOFF_C} °C")
                    elif th and now - th["t0"] > THERMAL_MAX_S:
                        self.thermal_off(f"auto-off after {THERMAL_MAX_S:.0f} s")
                    elif th and not subscribed and not th.get("headless"):
                        self.thermal_off("app disconnected")
                    if subscribed and self.on_telemetry:
                        self.on_telemetry(t)
                if now - last_health >= HEALTH_EVERY_S:
                    last_health = now
                    self.check_health()
            except Exception as e:
                print("[haptics] watchdog:", e, flush=True)
            self._stop.wait(0.5)

    def close(self):
        self._stop.set()
        try:
            self.stop_all("exit")
        finally:
            if self.link is not None:
                self.link.shutdown()

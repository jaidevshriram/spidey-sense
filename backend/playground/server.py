"""Haptic Playground: poke every DataFeel Dot feature from a web page (phone or laptop).

    scripts/playground.sh                         # Bluetooth (default), from Terminal.app
    python3 playground/server.py --transport usb  # USB serial
    python3 playground/server.py --mock           # no hardware, for UI work

Holds ONE connection to El Jefe; every command is timed and logged.
Thermal safety: target capped at 40 °C, auto-off after 30 s, immediate off if skin > 41 °C,
off when the page stops polling (closed), and off on server exit.
"""
from __future__ import annotations

import argparse
import atexit
import collections
import signal
import sys
import threading
import time
from pathlib import Path


import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import spidey_haptics as sh  # noqa: E402
from datafeel.device import LedMode, ThermalMode, VibrationMode, VibrationWaveforms  # noqa: E402
from dots import fastfire  # noqa: E402
from spidey.server import lan_ip  # noqa: E402

TARGET_CAP_C = 40.0
SKIN_CUTOFF_C = 41.0
THERMAL_MAX_S = 30.0
PAGE_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------
class MockDot:
    """Stands in for datafeel Dot so the UI can be developed without hardware."""

    class _Regs:
        def __init__(self):
            self.skin, self.sink, self.power, self.mode, self.intensity = 31.0, 30.0, 0.0, 0, 0.0
            self.dev = type("Dev", (), {"write_registers": lambda *a, **k: None})()

        def __getattr__(self, name):
            return lambda *a, **k: None

        def get_skin_temperature(self):
            if self.mode == ThermalMode.MANUAL:
                self.skin += 0.1 * self.intensity
            return self.skin

        def get_sink_temperature(self):
            return self.sink

        def get_thermal_power(self):
            return self.intensity * 2.0 if self.mode else 0.0

        def set_thermal_mode(self, m):
            self.mode = m

        def set_skin_temp_target(self, t):
            self.mode, self.intensity = ThermalMode.MANUAL, 0.5 if t > self.skin else -0.5

        def set_thermal_intensity(self, i):
            self.intensity = i

    def __init__(self, id):
        self.id = id
        self.registers = self._Regs()

    def __getattr__(self, name):
        return lambda *a, **k: None

    def disable_all_thermal(self):
        self.registers.mode = 0

    def activate_thermal_intensity_control(self, v):
        self.registers.mode, self.registers.intensity = ThermalMode.MANUAL, v

    def activate_thermal_temperature_control(self, t):
        self.registers.set_skin_temp_target(t)


class Hardware:
    def __init__(self, transport: str, ble_name: str, mock: bool):
        self.transport = "mock" if mock else transport
        self.device_name = "mock"
        self.link = None
        self.lock = threading.RLock()  # one Modbus transaction at a time on the link
        if mock:
            self.dots = [MockDot(3)]
        elif transport == "ble":
            from dots.ble import discover_ble_dots
            self.link, self.dots = discover_ble_dots(sh.MAX_DOTS, name_hint=ble_name)
            self.device_name = ble_name
        else:
            self.dots = sh.connect()
            self.device_name = "USB serial"
        if not self.dots:
            raise SystemExit("No dots found")
        if not mock:
            fastfire.prepare(self.dots)
        self.connected_at = time.time()

    def targets(self, address: int | None):
        if address is None:
            return self.dots
        ts = [d for d in self.dots if d.id == address]
        if not ts:
            raise HTTPException(404, f"no dot at address {address}")
        return ts


# ---------------------------------------------------------------------------
# State: command log, telemetry, thermal session + safety watchdog
# ---------------------------------------------------------------------------
class State:
    def __init__(self, hw: Hardware):
        self.hw = hw
        self.log = collections.deque(maxlen=300)
        self.last_ms = None
        self.telemetry = collections.deque(maxlen=600)   # ~5 min at 2 Hz
        self.thermal = None    # {"mode", "value", "t0", "base", "reached": {...}, "address"}
        self.last_page_poll = 0.0
        self.stop = threading.Event()

    def run(self, name: str, fn, **info):
        t0 = time.perf_counter()
        ok, err = True, None
        try:
            with self.hw.lock:
                fn()
        except HTTPException:
            raise
        except Exception as e:  # report hardware errors to the page instead of 500s
            ok, err = False, f"{type(e).__name__}: {e}"
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self.last_ms = ms
        entry = {"ts": time.time(), "cmd": name, "ms": ms, "ok": ok, "error": err, **info}
        self.log.appendleft(entry)
        print(f"[{time.strftime('%H:%M:%S')}] {name} {info or ''} {ms} ms {'' if ok else err}", flush=True)
        return entry

    # -- thermal -----------------------------------------------------------
    def thermal_off(self, reason: str):
        def off():
            for d in self.hw.dots:
                d.disable_all_thermal()
        was = self.thermal
        self.thermal = None
        if was is not None or reason in ("stop_all", "exit"):
            self.run("thermal_off", off, reason=reason)

    def stop_all(self, reason="stop_all"):
        def stop():
            for d in self.hw.dots:
                d.stop_vibration()
                d.set_led_off()
        self.thermal_off(reason)
        return self.run("stop_all", stop, reason=reason)

    def poll_once(self):
        d = self.hw.dots[0] if not self.thermal else self.hw.targets(self.thermal["address"])[0]
        with self.hw.lock:
            skin = d.registers.get_skin_temperature()
            sink = d.registers.get_sink_temperature()
            power = d.registers.get_thermal_power()
        now = time.time()
        sample = {"t": now, "skin": round(skin, 2), "sink": round(sink, 2), "power": round(power, 3)}
        self.telemetry.append(sample)
        th = self.thermal
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
            if th["mode"] == "target" and "target" not in th["reached"] and \
                    abs(skin - th["value"]) <= 0.3:
                th["reached"]["target"] = round(now - th["t0"], 1)
            th["rate_c_per_s"] = round(delta / max(0.5, now - th["t0"]), 3)
        return sample

    def watchdog(self):
        while not self.stop.is_set():
            th = self.thermal
            polling = time.time() - self.last_page_poll < PAGE_TIMEOUT_S
            try:
                if th or polling:
                    s = self.poll_once()
                    if th and s["skin"] > SKIN_CUTOFF_C:
                        self.thermal_off(f"skin {s['skin']} C > {SKIN_CUTOFF_C} C")
                    elif th and time.time() - th["t0"] > THERMAL_MAX_S:
                        self.thermal_off(f"auto-off after {THERMAL_MAX_S:.0f} s")
                    elif th and not polling and not th.get("headless"):
                        self.thermal_off("page closed")
            except Exception as e:
                print("[watchdog] poll error:", e, flush=True)
            self.stop.wait(0.5)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class Led(BaseModel):
    mode: str = "global"             # off | global | individual | breathe | track_thermal
    rgb: list[int] = [255, 255, 255]
    individual: list[list[int]] | None = None   # 8 x [r, g, b]
    address: int | None = None


class Sequence(BaseModel):
    slots: list[str | float]         # waveform names, or numbers = rest seconds (0-1.27)
    address: int | None = None


class Manual(BaseModel):
    on: bool = True
    frequency: float = 170
    intensity: float = 0.5
    address: int | None = None


class Thermal(BaseModel):
    mode: str = "manual"             # off | manual | target
    intensity: float = 0.0           # -1 cool .. +1 heat (manual)
    target_c: float = 34.0           # target mode, capped at 40 C
    address: int | None = None
    headless: bool = False           # characterization runs: don't auto-off on page close


def make_app(st: State) -> FastAPI:
    app = FastAPI(title="Haptic Playground")
    hw = st.hw
    page = Path(__file__).with_name("index.html")

    @app.get("/")
    def index():
        return FileResponse(page)

    @app.get("/api/status")
    def status():
        return {"transport": hw.transport, "device": hw.device_name,
                "dots": [d.id for d in hw.dots], "connected": True,
                "connected_at": hw.connected_at, "last_ms": st.last_ms,
                "limits": {"target_cap_c": TARGET_CAP_C, "skin_cutoff_c": SKIN_CUTOFF_C,
                           "thermal_max_s": THERMAL_MAX_S}}

    @app.get("/api/waveforms")
    def waveforms():
        return {"waveforms": [w.name for w in VibrationWaveforms if w.value != 0],
                "presets": {k: [int(x) for x in v[0]] for k, v in sh.PATTERNS.items()},
                "preset_rgb": {k: v[1] for k, v in sh.PATTERNS.items()}}

    @app.get("/api/codes")
    def codes():
        return {int(w.value): w.name for w in VibrationWaveforms if w.value != 0}

    @app.post("/api/led")
    def led(req: Led):
        ds = hw.targets(req.address)
        modes = {"off": LedMode.OFF, "global": LedMode.GLOBAL_MANUAL,
                 "individual": LedMode.INDIVIDUAL_MANUAL, "breathe": LedMode.BREATHE,
                 "track_thermal": LedMode.TRACK_THERMAL}
        if req.mode not in modes:
            raise HTTPException(400, f"mode must be one of {list(modes)}")

        def go():
            for d in ds:
                d.registers.set_led_mode(modes[req.mode])
                if req.mode == "global":
                    d.registers.set_global_led(*req.rgb)
                elif req.mode == "individual":
                    for i, c in enumerate((req.individual or [req.rgb] * 8)[:8]):
                        d.registers.set_individual_led(i, *c)
        return st.run("led", go, mode=req.mode, rgb=req.rgb if req.mode == "global" else None)

    def parse_slots(slots):
        seq = []
        for s in slots[:8]:
            if isinstance(s, (int, float)) or str(s).replace(".", "", 1).isdigit():
                seq.append(VibrationWaveforms.Rest(min(1.27, max(0.0, float(s)))))
            elif s:
                try:
                    seq.append(VibrationWaveforms[str(s)])
                except KeyError:
                    raise HTTPException(400, f"unknown waveform {s}")
        return seq

    @app.post("/api/vibrate/sequence")
    def vibrate_sequence(req: Sequence):
        ds, seq = hw.targets(req.address), parse_slots(req.slots)

        def go():
            for d in ds:
                d.registers.set_vibration_mode(VibrationMode.LIBRARY)
                d.registers.dev.write_registers(fastfire.SEQ_0123, [
                    r for w in fastfire._seq_words(seq) for r in fastfire._long_regs(w)])
                d.start_vibration_sequence()
        return st.run("vibrate_sequence", go, slots=req.slots)

    @app.post("/api/vibrate/preset/{name}")
    def vibrate_preset(name: str, address: int | None = None):
        if name not in sh.PATTERNS:
            raise HTTPException(404, f"unknown preset {name}")
        seq, rgb = sh.PATTERNS[name]
        ds = hw.targets(address)

        def go():
            for d in ds:
                d.registers.set_vibration_mode(VibrationMode.LIBRARY)
            fastfire.fire(ds, seq, rgb)
        return st.run("preset", go, preset=name)

    @app.post("/api/vibrate/manual")
    def vibrate_manual(req: Manual):
        ds = hw.targets(req.address)
        f = min(300.0, max(50.0, req.frequency))
        i = min(1.0, max(0.0, req.intensity))

        def go():
            for d in ds:
                if req.on:
                    d.play_frequency(f, i)
                else:
                    d.stop_vibration()
        return st.run("vibrate_manual", go, on=req.on, frequency=f, intensity=i)

    @app.post("/api/thermal")
    def thermal(req: Thermal):
        ds = hw.targets(req.address)
        if req.mode == "off":
            st.thermal_off("user")
            return st.log[0] if st.log else {"ok": True}
        if req.mode == "manual":
            v = min(1.0, max(-1.0, req.intensity))

            def go():
                for d in ds:
                    d.activate_thermal_intensity_control(v)
        elif req.mode == "target":
            v = min(TARGET_CAP_C, req.target_c)

            def go():
                for d in ds:
                    d.activate_thermal_temperature_control(v)
        else:
            raise HTTPException(400, "mode must be off | manual | target")
        base = st.telemetry[-1]["skin"] if st.telemetry and time.time() - st.telemetry[-1]["t"] < 2 else None
        entry = st.run("thermal", go, mode=req.mode, value=v)
        if entry["ok"]:
            st.thermal = {"mode": req.mode, "value": v, "t0": time.time(), "base": base,
                          "reached": {}, "address": ds[0].id, "headless": req.headless}
        return entry

    @app.get("/api/telemetry")
    def telemetry(since: float = 0):
        st.last_page_poll = time.time()
        th = st.thermal
        return {"samples": [s for s in st.telemetry if s["t"] > since],
                "thermal": None if not th else {
                    **{k: th[k] for k in ("mode", "value", "base", "reached")},
                    "elapsed_s": round(time.time() - th["t0"], 1),
                    "remaining_s": round(max(0, THERMAL_MAX_S - (time.time() - th["t0"])), 1),
                    "rate_c_per_s": th.get("rate_c_per_s")},
                "last_ms": st.last_ms}

    @app.post("/api/stop")
    def stop():
        return st.stop_all("stop_all")

    @app.get("/api/log")
    def log():
        return {"log": list(st.log)}

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["ble", "usb"], default="ble")
    ap.add_argument("--ble-name", default="DataFeelCE")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--mock", action="store_true", help="no hardware")
    args = ap.parse_args()

    hw = Hardware(args.transport, args.ble_name, args.mock)
    st = State(hw)
    threading.Thread(target=st.watchdog, daemon=True).start()

    def shutdown(*_):
        st.stop.set()
        try:
            st.stop_all("exit")
        finally:
            if hw.link:
                hw.link.shutdown()
    atexit.register(shutdown)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    print(f"\nHaptic Playground ({hw.transport}: {hw.device_name}, dots {[d.id for d in hw.dots]})")
    print(f"  on this Mac:  http://localhost:{args.port}")
    print(f"  on the phone: http://{lan_ip()}:{args.port}\n", flush=True)
    uvicorn.run(make_app(st), host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

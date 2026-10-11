"""Where haptic cues go: console, DataFeel dots, or (later) the phone."""
from __future__ import annotations

import time


class HapticSink:
    """play() is what the engine rules call. `control` (a HapticControl) is what the app's Haptics tab
    and link check use: real dots for DataFeel sinks, mock dots for the console sink."""
    control = None

    def play(self, pattern: str, dot: int | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        if self.control:
            self.control.close()


class ConsoleSink(HapticSink):
    def __init__(self):
        from .haptic_control import HapticControl
        self.control = HapticControl.mocked()   # lets the app's Haptics tab work without hardware

    def play(self, pattern, dot=None):
        print(f"  >>> HAPTIC {pattern} on {'all dots' if dot is None else f'dot {dot}'}"
              f" (intensity {self.control.intensity:.2f})", flush=True)


class DataFeelSink(HapticSink):
    """Real dots. Connects once, cools down per pattern, scales by the app's master intensity.

    transport="serial": El Jefe on USB.  transport="ble": El Jefe over Bluetooth (no cable;
    run from Terminal so macOS grants Bluetooth). ble_address / ble_name pick a specific El Jefe.
    """

    def __init__(self, cooldown_s: float = 2.0, transport: str = "serial",
                 ble_address: str | None = None, ble_name: str | None = "DataFeelCE"):
        import spidey_haptics as sh   # haptics/ (pip install -e haptics)
        from .haptic_control import HapticControl

        self.sh = sh
        link, rssi = None, None
        if transport == "ble":
            from dots.ble import discover_ble_dots
            link, dots = discover_ble_dots(sh.MAX_DOTS, ble_address, ble_name)
            if not dots:
                raise RuntimeError("Connected to El Jefe over Bluetooth but no dots answered")
            rssi, name = link.rssi, ble_name or "El Jefe (BLE)"
        else:
            dots, name = sh.connect(), "El Jefe (USB)"   # ble_* settings only apply to Bluetooth
        self.dots = dots
        self.control = HapticControl(dots, "ble" if transport == "ble" else "usb", name, link=link, rssi=rssi)
        self.cooldown_s = cooldown_s
        self._last: dict[str, float] = {}

    def play(self, pattern, dot=None):
        now = time.time()
        if now - self._last.get(pattern, 0) < self.cooldown_s:
            return
        self._last[pattern] = now
        seq, rgb = self.sh.PATTERNS[pattern]
        r = self.control.play_pattern(pattern, seq, rgb, None if dot is None else dot - 1)
        if not r["ok"]:
            print(f"  !!! haptic {pattern} failed: {r['error']}", flush=True)


class PhoneSink(HapticSink):
    """TODO: the future iOS app, over WebSocket ({"pattern": ..., "dot": ...} per message)."""

    def __init__(self, url: str = "ws://localhost:8765"):
        self.url = url

    def play(self, pattern, dot=None):
        raise NotImplementedError("PhoneSink: iOS app not built yet")


def make_sink(cfg: dict) -> HapticSink:
    kind = cfg.get("type", "console")
    params = {k: v for k, v in cfg.items() if k != "type"}
    if kind == "console":
        return ConsoleSink()
    if kind == "datafeel":
        return DataFeelSink(**params)
    if kind == "datafeel-ble":
        return DataFeelSink(transport="ble", **params)
    if kind == "phone":
        return PhoneSink(**params)
    raise ValueError(f"unknown sink type {kind!r}")

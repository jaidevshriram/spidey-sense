"""Where haptic cues go: console, DataFeel dots, or (later) the phone."""
from __future__ import annotations

import sys
import time
from pathlib import Path


class HapticSink:
    def play(self, pattern: str, dot: int | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ConsoleSink(HapticSink):
    def play(self, pattern, dot=None):
        print(f"  >>> HAPTIC {pattern} on {'all dots' if dot is None else f'dot {dot}'}", flush=True)


class DataFeelSink(HapticSink):
    """Real dots via spidey_haptics. Connects once, cools down per pattern.

    transport="serial": El Jefe on USB.  transport="ble": El Jefe over Bluetooth (no cable;
    run from Terminal so macOS grants Bluetooth). ble_address / ble_name pick a specific El Jefe.
    """

    def __init__(self, cooldown_s: float = 2.0, transport: str = "serial",
                 ble_address: str | None = None, ble_name: str | None = "DataFeelCE"):
        import spidey_haptics as sh   # haptics/ (pip install -e haptics)

        self.sh = sh
        self._link = None
        if transport == "ble":
            from dots.ble import discover_ble_dots
            self._link, self.dots = discover_ble_dots(sh.MAX_DOTS, ble_address, ble_name)
            if not self.dots:
                raise RuntimeError("Connected to El Jefe over Bluetooth but no dots answered")
        else:
            self.dots = sh.connect()
            # ble_* settings only apply to Bluetooth
        from dots import fastfire

        self.ff = fastfire
        fastfire.prepare(self.dots)   # 2 writes per buzz instead of 6 (~117 ms over BLE vs 361)
        self.cooldown_s = cooldown_s
        self._last: dict[str, float] = {}

    def play(self, pattern, dot=None):
        now = time.time()
        if now - self._last.get(pattern, 0) < self.cooldown_s:
            return
        self._last[pattern] = now
        seq, rgb = self.sh.PATTERNS[pattern]
        self.ff.fire(self.dots, seq, rgb, None if dot is None else [dot - 1])

    def close(self):
        self.sh.reset(self.dots)
        if self._link:
            self._link.shutdown()


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

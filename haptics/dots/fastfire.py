"""Fewer Modbus round trips per haptic trigger (matters over Bluetooth, ~60 ms each).

The stock SDK path (spidey_haptics.fire) makes 6 writes per trigger:
LED mode, LED colour, vibration mode, sequence 0-3, sequence 4-7, go.
Here: modes are set once in prepare(); a trigger is
    1) both sequence words in one write-multiple-registers call
    2) go
    3) LED mode + colour (after the buzz has already started; reset() turns the LED mode off)
"""
from __future__ import annotations

import time

import minimalmodbus as mm
from datafeel.device import LedMode, VibrationMode

SEQ_0123 = 1044   # V63Registers.VIBRATION_SEQUENCE_0123 (2 regs) followed by _4567 (2 regs)


def _long_regs(value: int) -> list[int]:
    """Encode like the SDK's write_long(..., BYTEORDER_LITTLE_SWAP, 2 registers)."""
    b = mm._long_to_bytes(value, False, 2, mm.BYTEORDER_LITTLE_SWAP)
    return [int.from_bytes(b[i:i + 2], "big") for i in (0, 2)]


def _seq_words(sequence) -> list[int]:
    words = [0, 0]
    for i, w in enumerate(sequence[:8]):  # same packing as Dot.set_vibration_sequence
        words[i // 4] |= int(w) << (i % 4) * 8
    return words


def prepare(dots) -> None:
    """One-time setup so each trigger only writes the sequence + go."""
    for d in dots:
        d.registers.set_vibration_mode(VibrationMode.LIBRARY)


def fire(dots, sequence, rgb, which=None) -> tuple[float, float]:
    """Play `sequence` with LED `rgb` on all dots (or indices in `which`).
    Returns (seconds until the buzz was triggered, seconds until everything was sent)."""
    targets = dots if which is None else [dots[i] for i in which]
    regs = [r for w in _seq_words(sequence) for r in _long_regs(w)]
    t0 = time.perf_counter()
    for d in targets:
        d.registers.dev.write_registers(SEQ_0123, regs)
        d.start_vibration_sequence()
    t_buzz = time.perf_counter() - t0
    for d in targets:
        d.registers.set_led_mode(LedMode.GLOBAL_MANUAL)
        d.registers.set_global_led(*rgb)
    return t_buzz, time.perf_counter() - t0

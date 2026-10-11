"""Spidey Sense haptics smoke test for the DataFeel dev kit.

Built on DataFeel's devkit Python SDK (datafeel/device.py, MIT license).

Usage:
    python3 spidey_haptics.py info              # list dots and read their sensors
    python3 spidey_haptics.py urgent [dot]      # play one pattern (dot = 1-based index, default all)
    python3 spidey_haptics.py pulse
    python3 spidey_haptics.py ambient
    python3 spidey_haptics.py all               # play each pattern in turn
    python3 spidey_haptics.py blind             # blind test: guess which pattern you felt
    python3 spidey_haptics.py latency           # time how long a trigger takes to send
"""
import random
import sys
import time

from datafeel.device import LedMode, VibrationWaveforms as W, discover_devices

MAX_DOTS = 4

# Each pattern = (vibration sequence of up to 8 steps, LED colour)
PATTERNS = {
    # Imminent hazard: three hard clicks, then a buzz. Red light.
    "urgent": ([W.TRIPLE_CLICK_P100, W.Rest(0.08), W.STRONG_BUZZ_P100], (255, 0, 0)),
    # Heartbeat-like rhythm for a borrowed or body sense. Pink light.
    "pulse": ([W.DOUBLE_CLICK_P60, W.Rest(0.6), W.DOUBLE_CLICK_P60, W.Rest(0.6),
               W.DOUBLE_CLICK_P60], (255, 40, 120)),
    # Low, slow presence for background info. Soft blue light.
    "ambient": ([W.SMOOTH_HUM_P20, W.SMOOTH_HUM_P30, W.SMOOTH_HUM_P20], (0, 60, 255)),
}


def connect():
    dots = discover_devices(MAX_DOTS)
    if not dots:
        sys.exit("No dots found. Check the USB-C cable is in El Jefe's serial port and the dots are on.")
    print(f"Found {len(dots)} dot(s):")
    for d in dots:
        print("  ", d)
    return dots


def fire(dots, name, which=None):
    """Play pattern `name` on all dots, or only the dot indices in `which`.
    Returns seconds spent sending the commands."""
    seq, (r, g, b) = PATTERNS[name]
    targets = dots if which is None else [dots[i] for i in which]
    t0 = time.perf_counter()
    for d in targets:
        d.registers.set_led_mode(LedMode.GLOBAL_MANUAL)
        d.registers.set_global_led(r, g, b)
        d.play_vibration_sequence(seq)
    return time.perf_counter() - t0


def reset(dots):
    for d in dots:
        d.stop_vibration()
        d.set_led_off()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    dots = connect()
    which = [int(sys.argv[2]) - 1] if len(sys.argv) > 2 else None

    try:
        if cmd == "info":
            for d in dots:
                print(d.id, "skin", d.registers.get_skin_temperature(),
                      "sink", d.registers.get_sink_temperature())
        elif cmd in PATTERNS:
            dt = fire(dots, cmd, which)
            print(f"{cmd}: sent in {dt * 1000:.0f} ms")
            time.sleep(3)
        elif cmd == "all":
            for name in PATTERNS:
                print("Playing", name)
                fire(dots, name, which)
                time.sleep(3)
                reset(dots)
                time.sleep(1)
        elif cmd == "blind":
            names = list(PATTERNS)
            score = 0
            for i in range(6):
                name = random.choice(names)
                input(f"\nTrial {i + 1}: press Enter, then close your eyes...")
                for d in dots:  # lights off so only touch gives it away
                    d.set_led_off()
                seq, _ = PATTERNS[name]
                for d in dots:
                    d.play_vibration_sequence(seq)
                guess = input(f"Which was it? {names}: ").strip().lower()
                ok = guess == name
                score += ok
                print("Correct" if ok else f"No, it was {name}")
            print(f"\nScore: {score}/6")
        elif cmd == "latency":
            times = []
            for _ in range(10):
                times.append(fire(dots, "urgent", which))
                time.sleep(1.5)
            times.sort()
            print(f"send time over 10 runs: median {times[5] * 1000:.0f} ms, worst {times[-1] * 1000:.0f} ms")
        else:
            print(__doc__)
    finally:
        reset(dots)


if __name__ == "__main__":
    main()

"""Cable-free check: connect to El Jefe over Bluetooth and compare trigger latency
(stock SDK path vs dots.fastfire). Buzzes the Dot ~15 times.

    python3 haptics/ble_latency.py      # from Terminal.app (Bluetooth)
"""
import time

import spidey_haptics as sh
from dots import fastfire
from dots.ble import discover_ble_dots

t0 = time.perf_counter()
link, dots = discover_ble_dots(sh.MAX_DOTS, name_hint="DataFeelCE")
print(f"connect + discover: {time.perf_counter() - t0:.1f} s, dots: {[d.id for d in dots]}")
if not dots:
    raise SystemExit("no dots over BLE")

def ms(xs):
    xs = sorted(xs)
    return f"median {xs[len(xs) // 2] * 1000:.0f} ms, worst {xs[-1] * 1000:.0f} ms"

try:
    old = []
    for _ in range(5):
        old.append(sh.fire(dots, "urgent")); time.sleep(1.5)
    sh.reset(dots)
    print(f"A) stock SDK fire (6 writes): {ms(old)}")

    fastfire.prepare(dots)
    seq, rgb = sh.PATTERNS["urgent"]
    buzz, total = [], []
    for _ in range(10):
        b, t = fastfire.fire(dots, seq, rgb)
        buzz.append(b); total.append(t); time.sleep(1.5)
    w = fastfire._seq_words(seq)
    rb = dots[0].registers.get_vibration_sequence_0123()
    print(f"B) fastfire: until buzz {ms(buzz)}; all sent {ms(total)}")
    print(f"   readback seq word: wrote {w[0]:#x}, device has {rb:#x} -> {'OK' if rb == w[0] else 'MISMATCH'}")
finally:
    sh.reset(dots)
    link.shutdown()
print("BLE LATENCY DONE")

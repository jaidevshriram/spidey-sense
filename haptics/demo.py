"""Guided demo: plays each pattern twice with gaps so you can feel them."""
import sys, time
import spidey_haptics as s

dots = s.connect()
delay = float(sys.argv[1]) if len(sys.argv) > 1 else 5
print(f"starting in {delay:.0f}s", flush=True)
time.sleep(delay)
try:
    for name in s.PATTERNS:
        for rep in (1, 2):
            print("playing", name, rep, flush=True)
            s.fire(dots, name)
            time.sleep(4)
            s.reset(dots)
            time.sleep(6)
finally:
    s.reset(dots)
print("done", flush=True)

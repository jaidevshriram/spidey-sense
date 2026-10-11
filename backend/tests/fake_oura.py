"""Stand-in for the open_oura `oura` CLI (no Bluetooth): prints `live-hr` output the way
crates/oura-cli/src/main.rs cmd_live_hr does ("  72 bpm (IBI 833 ms)" per beat).

    fake_oura.py --key-file K --db D [--name N] [--address A] live-hr --seconds S

Env: FAKE_OURA_BPMS="70,71,72" (beats to print), FAKE_OURA_RC (exit code), FAKE_OURA_DELAY (s between beats),
FAKE_OURA_RUNS (a file; one line is appended per run, so tests can count restarts). Never prints the key.
"""
import argparse
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--key-file")
ap.add_argument("--db")
ap.add_argument("--name")
ap.add_argument("--address")
ap.add_argument("cmd")
ap.add_argument("--seconds", type=int, default=30)
a = ap.parse_args()

runs = os.environ.get("FAKE_OURA_RUNS")
if runs:
    key = open(a.key_file).read().strip() if a.key_file and os.path.exists(a.key_file) else ""
    mode = oct(os.stat(a.key_file).st_mode & 0o777) if key else "-"
    with open(runs, "a") as f:
        f.write(f"{a.cmd} seconds={a.seconds} key_len={len(key)} key_mode={mode}\n")
if a.cmd != "live-hr":
    sys.exit(2)
print(f"Streaming live heart rate for {a.seconds}s (Ctrl-C to stop early)...", flush=True)
print("2026-10-10T20:00:00Z  INFO connected", file=sys.stderr, flush=True)
bpms = [b for b in os.environ.get("FAKE_OURA_BPMS", "70,71,72").split(",") if b]
delay = float(os.environ.get("FAKE_OURA_DELAY", "0.01"))
if not bpms:
    print("No beats captured. Make sure the ring is worn.", flush=True)
for b in bpms:
    bpm = int(b)
    print(f"  {bpm} bpm (IBI {60000 // bpm} ms)", flush=True)
    time.sleep(delay)
sys.exit(int(os.environ.get("FAKE_OURA_RC", "0")))

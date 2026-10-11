"""Latency cost of the v3 prompt features: bbox per custom trigger, and temporal (3 labelled images per call).

    .venv/bin/python vlm/bench_v3.py [--n 8] [--sides 384 512]

Sequential calls (no concurrency) on mock desk-test frames (backend/desk_test/scene.py), so numbers are
per-call model latency (streamed; "early" = hazard/urgency/x/seen parsed). Prints one raw reply per mode.
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "backend" / "desk_test"))

import scene  # noqa: E402
from spidey.detectors import VLMDetector  # noqa: E402
from spidey.types import Frame  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=8)
ap.add_argument("--sides", type=int, nargs="+", default=[384, 512])
a = ap.parse_args()

lion, reg = scene.camera_frame("pictures", "lion")
gz = scene.center(reg["text"])
lake = [scene.camera_frame("video", t=t)[0] for t in (6.6, 7.3, 8.0)]

MODES = {
    "builtin only (no custom trigger)": ([], "lion"),
    "1 custom trigger + bbox": ([("lion_unnoticed", "a lion or tiger", False)], "lion"),
    "1 temporal trigger, 3 images": ([("person_to_water", "a person getting close to the edge of water "
                                       "(lake/river/sea) compared to earlier frames", True)], "lake"),
}
rows = []
for side in a.sides:
    det = VLMDetector(max_side=side, env_file=str(REPO / ".env"))
    det._deliver = lambda *x, **k: None            # early results: not needed here
    for name, (trig, img) in MODES.items():
        det.set_triggers(trig)
        full, early, toks, sample = [], [], [], None
        for i in range(a.n):
            now = time.time()
            if img == "lion":
                f = Frame(now, lion, gz, True)
                hist = None
            else:
                fr = [Frame(now - 1.4 + 0.7 * k, lake[k], gz, True) for k in range(3)]
                hist = [("t-1.4s", det._small(fr[0])), ("t-0.7s", det._small(fr[1])), ("now", fr[2])]
                f = fr[2]
            d = det.call(f, hist)
            full.append(d.latency_ms)
            if d.raw.get("early_ms"):
                early.append(d.raw["early_ms"])
            toks.append((d.raw.get("input_tokens"), d.raw.get("output_tokens")))
            sample = d.raw.get("text")
        med = lambda xs: round(statistics.median(xs)) if xs else None
        p90 = lambda xs: round(sorted(xs)[int(len(xs) * 0.9)]) if xs else None
        rows.append((side, name, med(early), med(full), p90(full), toks[-1]))
        print(f"[{side}px] {name}: early {med(early)} ms, full {med(full)} / p90 {p90(full)} ms, "
              f"tokens in/out {toks[-1]}\n    reply: {sample}", flush=True)
    det.close()

print("\n| max_side | mode | early fields ms (median) | full reply ms (median / p90) | input / output tokens |")
print("|---|---|---|---|---|")
for side, name, e, m, p, t in rows:
    print(f"| {side} | {name} | {e} | {m} / {p} | {t[0]} / {t[1]} |")

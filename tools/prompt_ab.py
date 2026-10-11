"""A/B the Haiku-path detector on labelled saved frames: model x prompt -> recall, false alarms, latency.

    python3 tools/prompt_ab.py logs/prompt_eval/obstacle_set.json [--models claude-haiku-5-5 claude-sonnet-5-5]
Set file: {"trigger": {"id", "describe"}, "items": [{"img": path, "label": true|false}]} (frames are the exact
JPEGs Haiku saw, gaze dot already drawn). One trigger per call, as in the live engine. Real API calls.
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from spidey.detectors import VLMDetector  # noqa: E402
from spidey.types import Frame  # noqa: E402


def run(model, prompt, trig, item, min_u):
    d = VLMDetector(model=model, max_side=512, stream=False, max_tokens=300, env_file=str(ROOT / ".env"),
                    treat_screens_as_real=False, prompt=prompt)
    d.set_triggers([(trig["id"], trig["describe"], False)])
    t0 = time.perf_counter()
    det = d.call(Frame(0.0, cv2.imread(item["img"]), None))
    ms = (time.perf_counter() - t0) * 1000
    t = det.triggers.get(trig["id"]) or {}
    hit = bool(t.get("match")) and float(t.get("urgency") or 0) >= min_u
    return {"img": item["img"], "label": item["label"], "pred": hit, "u": t.get("urgency"), "ms": ms,
            "reason": t.get("reason", "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set")
    ap.add_argument("--models", nargs="+", default=["claude-haiku-5-5", "claude-sonnet-5-5"])
    ap.add_argument("--prompts", nargs="+", default=["v1", "v2"])
    ap.add_argument("--min-urgency", type=float, default=0.3)
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    S = json.load(open(a.set))
    rows, out = [], {}
    for m in a.models:
        for p in a.prompts:
            with ThreadPoolExecutor(a.jobs) as ex:
                res = list(ex.map(lambda it: run(m, p, S["trigger"], it, a.min_urgency), S["items"]))
            pos = [r for r in res if r["label"]]
            neg = [r for r in res if not r["label"]]
            tp, fp = sum(r["pred"] for r in pos), sum(r["pred"] for r in neg)
            ms = statistics.median(r["ms"] for r in res)
            rows.append(f"| {m} | {p} | {tp}/{len(pos)} ({100 * tp / max(1, len(pos)):.0f}%) | "
                        f"{fp}/{len(neg)} ({100 * fp / max(1, len(neg)):.0f}%) | {ms:.0f} |")
            out[f"{m}/{p}"] = res
            print(rows[-1], flush=True)
    Path(a.set).with_suffix(".results.json").write_text(json.dumps(out, indent=1))
    print("\n| model | prompt | recall | false alarms | median ms |\n|---|---|---|---|---|\n" + "\n".join(rows))


if __name__ == "__main__":
    main()

"""Scored world-sim evaluation: run each desk scenario N times through the REAL engine path
(FakeSource frames -> VLM detector -> gaze cache / memory / rules -> console sink) and score fires against ground truth.

    python3 tools/world_eval.py [A B C D1 D2] [--reps 3]
    -> vlm/world_eval_results.md + logs/world_eval/<scenario>-<rep>/ (events + traces under backend/logs/runs/)
Frames come from tools/world_scene.py (no monitor in view). Real Haiku calls (key from .env), console sink, no server.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from desk_test import scene  # noqa: E402
from spidey.engine import load_config, run as engine_run  # noqa: E402

W = ROOT / "logs" / "world_sim"
SUBJ, SIGN = (0.69, 0.50), (0.17, 0.42)
PANEL_X0, PANEL_W = 0.38, 0.62


def fig_gaze(t):  # gaze following the lake figure (world coords)
    fx, _ = scene.figure_x(t)
    return PANEL_X0 + PANEL_W * fx, scene.FOOT_Y - scene.FIG_H / 2


def gap_bh(t):
    return scene.lake_truth_gap(t, PANEL_W * 1600, 1200)


T_NEAR = next(t / 10 for t in range(0, 140) if gap_bh(t / 10) < 1.0)   # person within 1 body-height of the edge
SCEN = {
    # id: (trigger, source params, seconds, [(lo, hi, label, expect_fire)])
    "A": ("big_cat_look", dict(paths=[str(W / f"world_{p}.jpg") for p in ("lion", "dog", "tiger")], hold_s=8,
                              gaze="script", gaze_script=[[0, *SIGN], [4, *SUBJ], [16, *SIGN]]), 24,
          [(0, 4, "lion, reading the sign", False), (4, 10.5, "lion, LOOKING at it (from 4 s)", True),
           (10.5, 16, "dog, looking at it", False), (16, 24, "tiger in view, reading the sign", False)]),
    "B": ("lion_unnoticed", dict(paths=[str(W / f"world_{p}.jpg") for p in ("lion", "lion", "dog")], hold_s=8,
                                gaze="script", gaze_script=[[0, *SUBJ], [6, *SIGN]]), 24,
          [(0, 8.8, "lion, looking at it (+3 s noticed memory after 6 s)", False),
           (8.8, 18.5, "lion, reading the sign, memory expired (from 9 s)", True), (18.5, 24, "dog, reading the sign", False)]),
    "C": ("person_to_water", dict(path=str(W / "lake_world.mp4"), gaze="script", gaze_script=[[0, *SIGN]]), 21,
          [(0, T_NEAR - 1.0, "far from the water", False), (T_NEAR - 1.0, 16.5, "near / at the edge", True),
           (16.5, 21, "walking back", False)]),
    "D1": ("person_to_water_unwatched", dict(path=str(W / "lake_world.mp4"), gaze="script", gaze_script=[[0, *SIGN]]),
           21, [(0, T_NEAR - 1.0, "far, reading the sign", False),
                (T_NEAR - 1.0, 16.5, "near the edge, reading the sign", True), (16.5, 21, "walking back", False)]),
    "D2": ("person_to_water_unwatched", dict(path=str(W / "lake_world.mp4"), gaze="script",
                                             gaze_script=[[t / 2, *fig_gaze(t / 2)] for t in range(0, 42)]), 21,
           [(0, 21, "watching the person the whole time", False)]),
}


def run_one(sid, rep):
    trig, src, secs, segs = SCEN[sid]
    out = ROOT / "logs" / "world_eval" / f"{sid}-{rep}"
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_config(ROOT / "backend" / "configs" / "config.world.sim.yaml")
    cfg["source"] = {"type": "fake", "fps": 15, "loop": False, "gaze_period": secs + 60, **src}  # script must not repeat
    seed = json.load(open(ROOT / "backend" / "desk_test" / "triggers.desk.json"))
    for r in seed["rules"]:
        r["enabled"] = r["id"] == trig
    (out / "triggers.json").write_text(json.dumps(seed))
    for f in ("events.jsonl",):
        (out / f).unlink(missing_ok=True)
    cfg.update(server={"enabled": False}, sink={"type": "console"}, log=str(out / "events.jsonl"),
               rules_file=str(out / "triggers.json"))
    cfg["trace"] = {**(cfg.get("trace") or {}), "enabled": True, "dir": str(out / "trace")}   # per-run traces
    t_start = time.time()
    engine_run(cfg, secs)
    time.sleep(2.5)
    lines = [json.loads(l) for l in open(out / "events.jsonl")]
    t0 = min(x["ts"]["frame"] for x in lines if "ts" in x) if lines else t_start
    fires = []
    for x in lines:
        for e in x.get("events") or []:
            if e.get("trigger") == trig:
                sent = x["ts"].get("sent") or x["ts"].get("frame")
                fires.append({"t": round(sent - t0, 2), "frame_t": round(e.get("frame_ts", sent) - t0, 2),
                              "fast": bool(x.get("gaze_cache")), "latency_ms": e.get("latency_ms"),
                              "why": (e.get("why") or e.get("reason") or "")[:110]})
    calls = sum(1 for x in lines if "detection" in x and not x["detection"].get("raw", {}).get("early"))
    seg_res = []
    for lo, hi, label, expect in segs:
        inside = [f for f in fires if lo <= f["t"] < hi]
        seg_res.append({"seg": label, "lo": lo, "hi": hi, "expect": expect, "fires": inside,
                        "ok": bool(inside) == expect})
    runs = sorted((out / "trace").glob("*"), key=lambda p: p.stat().st_mtime)
    return {"sid": sid, "rep": rep, "trigger": trig, "calls": calls, "fires": fires, "segs": seg_res,
            "trace_run": str(runs[-1]) if runs else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*", default=list(SCEN))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=6, help="runs in parallel (each real-time; separate processes)")
    a = ap.parse_args()
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed
    jobs = [(sid, rep) for sid in a.scenarios for rep in range(1, a.reps + 1)]
    res = []
    with ProcessPoolExecutor(max_workers=a.jobs, mp_context=mp.get_context("spawn")) as ex:
        futs = {ex.submit(run_one, sid, rep): (sid, rep) for sid, rep in jobs}
        for fu in as_completed(futs):
            r = fu.result()
            res.append(r)
            print(f"### {r['sid']} rep {r['rep']}: " + " | ".join(f"{s['seg']}: {'OK' if s['ok'] else 'FAIL'} "
                  f"({len(s['fires'])} fire{'s' if len(s['fires']) != 1 else ''})" for s in r["segs"]), flush=True)
    res.sort(key=lambda r: (a.scenarios.index(r["sid"]), r["rep"]))
    (ROOT / "logs" / "world_eval" / "results.json").write_text(json.dumps(res, indent=1))
    lines = ["# World-sim evaluation", "", f"Real Haiku, world-style frames (no monitor), engine path incl. gaze cache. "
             f"{a.reps} reps per scenario. T_NEAR (person within 1 body-height of the water) = {T_NEAR:.1f} s.", "",
             "| Scenario | Segment | Expect | Pass | Fires (t s, fast/haiku, latency ms) |", "|---|---|---|---|---|"]
    for sid in a.scenarios:
        rr = [r for r in res if r["sid"] == sid]
        for i, (lo, hi, label, expect) in enumerate(SCEN[sid][3]):
            oks = sum(r["segs"][i]["ok"] for r in rr)
            f = "; ".join(", ".join(f"{x['t']}s {'fast' if x['fast'] else 'haiku'} {x['latency_ms']}"
                                    for x in r["segs"][i]["fires"]) or "–" for r in rr)
            lines.append(f"| {sid} `{SCEN[sid][0]}` | {label} ({lo}–{hi} s) | {'fire' if expect else 'none'} | "
                         f"**{oks}/{len(rr)}** | {f} |")
    lat_fast = [f["latency_ms"] for r in res for s in r["segs"] if s["expect"] for f in s["fires"] if f["fast"] and f["latency_ms"]]
    lat_h = [f["latency_ms"] for r in res for s in r["segs"] if s["expect"] for f in s["fires"] if not f["fast"] and f["latency_ms"]]
    a_trans = [s["fires"][0]["t"] - 4 for r in res if r["sid"] == "A" for s in r["segs"] if s["expect"] and s["fires"]]
    fa = sum(len(s["fires"]) for r in res for s in r["segs"] if not s["expect"])
    lines += ["", f"- False alarms (fires in 'none' segments): **{fa}**",
              f"- Gaze-cache fires: latency (gaze sample → buzz) median "
              f"{statistics.median(lat_fast):.0f} ms (n={len(lat_fast)})" if lat_fast else "- Gaze-cache fires: none",
              f"- Haiku-path fires: frame → buzz median {statistics.median(lat_h):.0f} ms (n={len(lat_h)})" if lat_h else "",
              f"- A: gaze lands on the lion at 4 s → first buzz after {statistics.median(a_trans):.2f} s median "
              f"({', '.join(f'{x:.2f}' for x in a_trans)})" if a_trans else "",
              "", f"Runs in parallel ({a.jobs} at a time), each in real time. Traces per run: "
              "`TRACE_DIR=<dir> ./scripts/traces.sh` with " +
              ", ".join(f"{r['sid']}-{r['rep']} → `{r['trace_run']}`" for r in res)]
    out = ROOT / "vlm" / "world_eval_results.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

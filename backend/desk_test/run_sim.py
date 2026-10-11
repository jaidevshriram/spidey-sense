"""Pre-run the four desk tests without hardware: mock scene-camera frames + scripted gaze -> the real engine
(VLMDetector = real Haiku calls, TriggerMemory, rules) -> console sink. No server, no Bluetooth.

    .venv/bin/python backend/desk_test/run_sim.py            # all tests (A B C D1 D2), ~2 min of Haiku
    .venv/bin/python backend/desk_test/run_sim.py A B         # some
    .venv/bin/python backend/desk_test/run_sim.py --max-side 384

Writes logs/desk_sim/<test>.jsonl (engine log), logs/desk_sim/<test>.txt (console), and the hit/miss table to
vlm/desk_test_results.md. Classification is by decision time (when the engine would buzz) against the scripted
scene timeline; the first `GRACE_S` of each segment is a transition (Haiku is still judging older frames).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import statistics
import sys
import time
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import scene  # noqa: E402
from spidey.engine import run  # noqa: E402

OUT = REPO / "logs" / "desk_sim"
GRACE_S = 1.5
TRIGGERS = json.loads((HERE / "triggers.desk.json").read_text())["rules"]


def jitter_script(points, until: float, step: float = 0.25, sd: float = 0.006, seed: int = 1):
    """[(t, (x, y) | callable(t))] -> FakeSource gaze_script [[t, x, y], ...] sampled every `step` with jitter."""
    rnd = random.Random(seed)
    out, t = [], 0.0
    while t < until:
        cur = [p for p in points if p[0] <= t][-1][1]
        x, y = cur(t) if callable(cur) else cur
        out.append([round(t, 3), round(x + rnd.gauss(0, sd), 4), round(y + rnd.gauss(0, sd), 4)])
        t += step
    return out


def stills(names, tag):
    paths, regs = [], []
    for i, n in enumerate(names):
        img, reg = scene.camera_frame("pictures", n)
        p = OUT / f"{tag}_{i}_{n}.jpg"
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        paths.append(str(p))
        regs.append(reg)
    return paths, regs


def lake_video(seconds: float = scene.LAKE_PERIOD, fps: int = 15) -> Path:
    p = OUT / "lake.mp4"
    if p.exists():
        return p
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), fps, (scene.FRAME_W, scene.FRAME_H))
    for i in range(int(seconds * fps)):
        vw.write(scene.camera_frame("video", t=i / fps)[0])
    vw.release()
    return p


def lake_t_near(gap_bh: float = 1.0) -> float:
    pw = (scene.LAYOUT["video"]["panel"][2] - scene.LAYOUT["video"]["panel"][0]) * scene.SCREEN_W
    ph = (scene.LAYOUT["video"]["panel"][3] - scene.LAYOUT["video"]["panel"][1]) * scene.SCREEN_H
    t = 0.0
    while t < 10 and scene.lake_truth_gap(t, pw, ph) > gap_bh:
        t += 0.05
    return round(t, 2)


def build(test: str):
    """-> (source cfg, enabled trigger ids, duration, segments[(t0, t1, expect, label)], trigger id)"""
    text_c = scene.center(scene.camera_frame("pictures", "lion")[1]["text"])
    if test == "A":
        paths, regs = stills(["lion", "dog", "lion"], "A")
        gaze = jitter_script([(0, regs[0]["subject"]), (7, regs[1]["subject"]), (14, text_c)], 21)
        src = {"type": "fake", "paths": paths, "hold_s": 7, "fps": 15, "gaze": "script", "gaze_script": gaze,
               "gaze_period": 1000}
        segs = [(0, 7, "fire", "lion shown, gaze on the lion"), (7, 14, "none", "dog shown, gaze on the dog"),
                (14, 21, "none", "lion shown, gaze on the text")]
        return src, ["big_cat_look"], 21, segs, "big_cat_look"
    if test == "B":
        paths, regs = stills(["lion", "lion", "lion", "lion", "dog"], "B")
        gaze = jitter_script([(0, text_c), (5, regs[0]["subject"]), (9, text_c)], 20)
        src = {"type": "fake", "paths": paths, "hold_s": 4, "fps": 15, "gaze": "script", "gaze_script": gaze,
               "gaze_period": 1000}
        segs = [(0, 5, "fire", "lion, gaze on the text"), (5, 9, "none", "gaze on the lion"),
                (9, 12, "none", "looked away < 3 s ago (notice memory)"),
                (12, 16, "fire", "memory expired, lion unseen again"), (16, 20, "none", "dog, gaze on the text")]
        return src, ["lion_unnoticed"], 20, segs, "lion_unnoticed"
    vid = lake_video()
    t_near = lake_t_near(1.0)
    _, reg0 = scene.camera_frame("video", t=0)
    text_v = scene.center(reg0["text"])
    if test in ("C", "D1"):
        gaze = jitter_script([(0, text_v)], 20)
        tid = "person_to_water" if test == "C" else "person_to_water_unwatched"
        label = "gaze anywhere (on the text)" if test == "C" else "gaze on the text"
        segs = [(0, t_near, "none", f"far from the water (truth gap > 1 body-height); {label}"),
                (t_near, 14, "fire", f"near / at the edge; {label}"),
                (14, 20, "none", "walking away (hysteresis)")]
    else:   # D2: gaze follows the figure
        gaze = jitter_script([(0, lambda t: scene.camera_frame("video", t=t)[1]["subject"])], 20)
        tid = "person_to_water_unwatched"
        segs = [(0, 20, "none", "gaze on the figure the whole time")]
    src = {"type": "fake", "path": str(vid), "loop": False, "fps": 15, "gaze": "script", "gaze_script": gaze,
           "gaze_period": 1000}
    return src, [tid], 20, segs, tid


def run_test(test: str, max_side: int, concurrency: int = 3) -> dict:
    src, enabled, dur, segs, tid = build(test)
    rules = [dict(r, enabled=r["id"] in enabled) for r in TRIGGERS]
    rules_path = OUT / f"triggers_{test}.json"
    rules_path.write_text(json.dumps({"rules": rules}, indent=1))
    log = OUT / f"{test}.jsonl"
    log.unlink(missing_ok=True)
    cfg = {
        "source": src,
        "detector": {"type": "vlm", "model": "claude-haiku-5-5", "max_side": max_side, "jpeg_quality": 70,
                     "concurrency": concurrency, "stream": True, "max_tokens": 200, "min_interval_s": 0.4,
                     "env_file": str(REPO / ".env")},
        "detect_every_s": 0, "rules_file": str(rules_path), "feedback_log": str(OUT / "feedback.jsonl"),
        "sink": {"type": "console"}, "log": str(log), "server": {"enabled": False},
        "_config_path": f"desk_sim:{test}",
    }
    start_at = time.time() + 2.0
    src["start_at"] = start_at
    buf = io.StringIO()
    print(f"--- test {test}: {tid} ({dur} s) ---", flush=True)
    with contextlib.redirect_stdout(buf):
        run(cfg, dur + (start_at - time.time()))
    (OUT / f"{test}.txt").write_text(buf.getvalue())
    (OUT / f"{test}_meta.json").write_text(json.dumps({"start_at": start_at, "segments": segs, "trigger": tid}))
    return analyze(test, log, start_at, segs, tid)


def reanalyze(test: str) -> dict:
    meta = json.loads((OUT / f"{test}_meta.json").read_text())
    return analyze(test, OUT / f"{test}.jsonl", meta["start_at"], [tuple(x) for x in meta["segments"]],
                   meta["trigger"])


def analyze(test, log, start_at, segs, tid) -> dict:
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    fires, calls, e2e, reasons = [], [], [], []
    for ln in lines:
        d = ln["ts"]["sent"] - start_at
        raw = ln["detection"]["raw"]
        if not raw.get("early") and "text" in raw:
            calls.append(ln["detection"]["latency_ms"])
            e2e.append(ln["e2e_ms"])
            t = (ln["detection"].get("triggers") or {}).get(tid) or {}
            reasons.append((d, t))
        for ev in ln["events"]:
            if ev.get("trigger") == tid:
                fires.append({"d": round(d, 2), "frame_t": round(ev.get("frame_ts", 0) - start_at, 2),
                              "latency_ms": ev.get("latency_ms"), "why": ev.get("why"),
                              "looked_at_s_ago": ev.get("looked_at_s_ago"), "trend": ev.get("trend")})
    rows = []
    for t0, t1, expect, label in segs:
        inside = [f for f in fires if t0 <= f["d"] < t1]
        counted = [f for f in inside if f["d"] >= t0 + (GRACE_S if t0 > 0 else 0)]
        transition = [f for f in inside if f not in counted]
        seg_reasons = [r for d, r in reasons if t0 <= d < t1]
        if expect == "fire":
            ok = bool(inside)
            verdict = "HIT" if ok else "MISS"
        else:
            ok = not counted
            verdict = "OK (quiet)" if ok else "FALSE FIRE"
        first = (inside if expect == "fire" else counted or inside or [None])[0] if (inside or counted) else None
        last_r = seg_reasons[-1] if seg_reasons else {}
        rows.append({"segment": f"{t0:g}-{t1:g} s", "label": label, "expect": expect, "verdict": verdict,
                     "ok": ok, "fires": len(inside), "transition_fires": len(transition),
                     "onset_to_fire_s": round(first["d"] - t0, 2) if first and expect == "fire" else None,
                     "frame_to_fire_ms": first["latency_ms"] if first else None,
                     "why": (first or {}).get("why") or last_r.get("reason"),
                     "looked_at_s_ago": first.get("looked_at_s_ago") if first else last_r.get("looked_at_s_ago"),
                     "trend": (first or {}).get("trend") or last_r.get("trend")})
    med = lambda xs: round(statistics.median(xs)) if xs else None
    p90 = lambda xs: round(sorted(xs)[int(len(xs) * 0.9)]) if xs else None
    res = {"test": test, "trigger": tid, "rows": rows, "fires": fires, "n_calls": len(calls),
           "haiku_ms": {"median": med(calls), "p90": p90(calls)}, "e2e_ms": {"median": med(e2e), "p90": p90(e2e)}}
    (OUT / f"{test}_result.json").write_text(json.dumps(res, indent=1))
    for r in rows:
        print(f"  {r['segment']:>9} {r['expect']:>4} -> {r['verdict']:<11} fires={r['fires']} "
              f"onset->fire={r['onset_to_fire_s']} frame->fire={r['frame_to_fire_ms']}  {r['why']}", flush=True)
    print(f"  Haiku {res['haiku_ms']} e2e {res['e2e_ms']} calls={len(calls)}", flush=True)
    return res


def fmt_trend(tr):
    if not tr:
        return ""
    gap = " → ".join("–" if g is None else f"{g:.2f}" for g in tr.get("gap") or [])
    ords = ",".join(o or "–" for o in tr.get("ordinal") or [])
    return f"gap {gap} bh · {ords}{' · touching' if tr.get('touching') else ''} · loom {tr.get('loom')}"


def write_md(results: list[dict], max_side: int, concurrency: int = 3) -> Path:
    p = REPO / "vlm" / "desk_test_results.md"
    out = [f"# Desk test pre-runs (simulated scene camera + scripted gaze, real Haiku)", "",
           f"Run {time.strftime('%Y-%m-%d %H:%M')} · `backend/desk_test/run_sim.py` · claude-haiku-5-5 · "
           f"max_side {max_side} · up to {concurrency} calls in flight, launched every 0.4 s · console sink · logs in `logs/desk_sim/`.",
           "Verdicts are by decision time against the scripted timeline; the first 1.5 s of a segment is a "
           "transition (Haiku still judging older frames) and isn't counted as a false fire.", "",
           "| Test | Trigger | Segment | Scene | Expect | Result | Fires | Onset→fire | Frame→fire | Gaze / trend | Haiku's reason |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        for row in r["rows"]:
            gt = fmt_trend(row["trend"]) if row["trend"] else (
                "" if row["looked_at_s_ago"] is None and "unseen" not in r["trigger"] else
                f"looked_at_s_ago={row['looked_at_s_ago']}")
            why = (row["why"] or "").replace("|", "/")
            tf = f" (+{row['transition_fires']} in transition)" if row["transition_fires"] else ""
            out.append(f"| {r['test']} | `{r['trigger']}` | {row['segment']} | {row['label']} | {row['expect']} | "
                       f"**{row['verdict']}** | {row['fires']}{tf} | "
                       f"{'' if row['onset_to_fire_s'] is None else str(row['onset_to_fire_s']) + ' s'} | "
                       f"{'' if row['frame_to_fire_ms'] is None else str(round(row['frame_to_fire_ms'])) + ' ms'} | "
                       f"{gt} | {why} |")
    out += ["", "| Test | Haiku calls | Haiku call ms (median / p90) | Frame→result ms (median / p90) |", "|---|---|---|---|"]
    for r in results:
        out.append(f"| {r['test']} | {r['n_calls']} | {r['haiku_ms']['median']} / {r['haiku_ms']['p90']} | "
                   f"{r['e2e_ms']['median']} / {r['e2e_ms']['p90']} |")
    marker = "<!-- notes below are kept when the table is regenerated -->"
    old = p.read_text() if p.exists() else ""
    notes = old[old.index(marker):] if marker in old else marker + "\n"
    p.write_text("\n".join(out) + "\n\n" + notes)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tests", nargs="*", default=["A", "B", "C", "D1", "D2"])
    ap.add_argument("--max-side", type=int, default=512)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--no-md", action="store_true")
    ap.add_argument("--reanalyze", action="store_true", help="rebuild the table from the last logs (no Haiku)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    results = [reanalyze(t) if a.reanalyze else run_test(t, a.max_side, a.concurrency) for t in a.tests]
    if not a.no_md:
        print(f"table -> {write_md(results, a.max_side, a.concurrency)}")


if __name__ == "__main__":
    main()

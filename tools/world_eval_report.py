"""HTML report for tools/world_eval.py: "would the Dot have buzzed?" at a glance.

    python3 tools/world_eval_report.py   -> logs/reports/world_eval.html
Per run: a timeline strip (expected-buzz windows shaded, ground-truth segments, gaze target, every Haiku call as a
tick, every buzz as a marker: filled = gaze-cache fast path, ring = Haiku path), plus a verdict badge per segment.
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))
import world_eval as W  # noqa: E402

COL = {"big_cat_look": "#E07B00", "lion_unnoticed": "#C8202F", "person_to_water": "#1D5FB8",
       "person_to_water_unwatched": "#123E8C"}
e = html.escape


def gaze_lanes(sid):
    src = W.SCEN[sid][1]
    pts = src.get("gaze_script") or []
    out = []
    for i, (t, x, y) in enumerate(pts):
        t1 = pts[i + 1][0] if i + 1 < len(pts) else W.SCEN[sid][2]
        tgt = "subject" if x > 0.35 else "sign"
        if out and out[-1][2] == tgt:
            out[-1] = (out[-1][0], t1, tgt)
        else:
            out.append((t, t1, tgt))
    return out


def strip(r, secs, px=900):
    sx = lambda t: 40 + (px - 60) * max(0, min(t, secs)) / secs
    trig = r["trigger"]
    c = COL.get(trig, "#333")
    parts = [f'<svg width="{px}" height="128" style="font:11px Arial">']
    for i, s in enumerate(r["segs"]):                                   # lane 1: ground truth + expected windows
        x0, x1 = sx(s["lo"]), sx(s["hi"])
        fill = "#d9f2dd" if s["expect"] else "#f4f5f7"
        parts.append(f'<rect x="{x0}" y="4" width="{x1 - x0}" height="118" fill="{fill}" stroke="#fff"/>')
        parts.append(f'<text x="{x0 + 3}" y="16" fill="#333">{e(s["seg"][:46])}</text>')
    for t0, t1, tgt in gaze_lanes(r["sid"]) if r["sid"] != "D2" else [(0, secs, "subject (following the person)")]:
        parts.append(f'<rect x="{sx(t0)}" y="26" width="{sx(t1) - sx(t0)}" height="14" fill="'
                     f'{"#9cc3ff" if tgt.startswith("subject") else "#ddd"}"/>'
                     f'<text x="{sx(t0) + 3}" y="37">gaze: {e(tgt)}</text>')
    for t in r.get("call_ts", []):                                     # lane 3: Haiku calls
        parts.append(f'<line x1="{sx(t)}" x2="{sx(t)}" y1="50" y2="62" stroke="#777"/>')
    parts.append('<text x="2" y="60" fill="#777">calls</text><text x="2" y="96" fill="#333">buzz</text>')
    for f in r["fires"]:                                               # lane 4: buzzes
        x = sx(f["t"])
        if f["fast"]:
            parts.append(f'<circle cx="{x}" cy="92" r="8" fill="{c}"><title>{e(f["why"])}</title></circle>')
        else:
            parts.append(f'<circle cx="{x}" cy="92" r="8" fill="#fff" stroke="{c}" stroke-width="3">'
                         f'<title>{e(f["why"])}</title></circle>')
        parts.append(f'<text x="{x - 10}" y="116" fill="{c}">{f["t"]:.1f}s</text>')
    for t in range(0, int(secs) + 1, 2):
        parts.append(f'<text x="{sx(t) - 4}" y="126" fill="#999" font-size="9">{t}</text>')
    parts.append("</svg>")
    return "".join(parts)


def main():
    res = json.load(open(ROOT / "logs" / "world_eval" / "results.json"))
    for r in res:                                                       # Haiku call times for the tick lane
        ev = ROOT / "logs" / "world_eval" / f"{r['sid']}-{r['rep']}" / "events.jsonl"
        lines = [json.loads(l) for l in open(ev)] if ev.exists() else []
        t0 = min((x["ts"]["frame"] for x in lines if "ts" in x), default=0)
        r["call_ts"] = [round(x["ts"]["detected"] - t0, 2) for x in lines
                        if "detection" in x and not x["detection"].get("raw", {}).get("early") and "detected" in x["ts"]]
    md = (ROOT / "vlm" / "world_eval_results.md").read_text() if (ROOT / "vlm" / "world_eval_results.md").exists() else ""
    rows = []
    for r in res:
        secs = W.SCEN[r["sid"]][2]
        badges = []
        for s in r["segs"]:
            if s["fires"]:
                f = s["fires"][0]
                b = (f'<span class="b" style="background:{COL.get(r["trigger"], "#333")}">BUZZ: {e(r["trigger"])} '
                     f'({"gaze cache" if f["fast"] else "Haiku"}, {f["latency_ms"]} ms)</span>')
            else:
                b = '<span class="b" style="background:#9aa0a6">no buzz</span>'
            ok = "✅" if s["ok"] else "❌"
            why = e(s["fires"][0]["why"]) if s["fires"] else ""
            badges.append(f'<div>{ok} <b>{e(s["seg"])}</b> (expect {"buzz" if s["expect"] else "none"}) {b} '
                          f'<span class="why">{why}</span></div>')
        rows.append(f'<div class="run"><h3>{e(r["sid"])} · rep {r["rep"]} · <code>{e(r["trigger"])}</code> · '
                    f'{r["calls"]} Haiku calls</h3>{strip(r, secs)}{"".join(badges)}'
                    f'<div class="tr">trace: <code>TRACE_DIR={e(str(Path(r["trace_run"] or "").parent))} '
                    f'./scripts/traces.sh</code></div></div>')
    page = f'''<!doctype html><meta charset="utf-8"><title>World-sim evaluation</title>
<style>body{{font:14px Arial;margin:24px;color:#111;background:#fff;max-width:1000px}}.run{{border:1px solid #ddd;
border-radius:8px;padding:10px 14px;margin:14px 0}}.b{{color:#fff;border-radius:10px;padding:2px 8px;font-size:12px}}
.why{{color:#555;font-size:12px}}.tr{{color:#777;font-size:12px;margin-top:6px}}pre{{white-space:pre-wrap;background:#f6f7f9;padding:10px}}</style>
<h1>Would the Dot have buzzed? World-sim evaluation</h1>
<p><b>Legend.</b> Shaded green = a buzz is expected here; gray = no buzz expected. Gaze lane: blue = looking at the subject
(lion/tiger/person), gray = reading the sign. Calls = each Haiku result. Buzz markers: <b>filled</b> = fast gaze-cache path
(gaze checked against Haiku's cached box on every frame), <b>ring</b> = Haiku path. Badge colour = the trigger's LED colour.</p>
<details><summary>Summary table</summary><pre>{e(md)}</pre></details>{"".join(rows)}'''
    out = ROOT / "logs" / "reports" / "world_eval.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(out)


if __name__ == "__main__":
    main()

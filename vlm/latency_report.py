"""Summarize a VLM run: per-call latency (median/p90) and what the model said.

    python3 vlm/latency_report.py backend/logs/vlm_events.jsonl
"""
import json
import sys

rows = [json.loads(l) for l in open(sys.argv[1] if len(sys.argv) > 1 else "backend/logs/vlm_events.jsonl")]
lat = sorted(r["detection"]["latency_ms"] for r in rows)
pct = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]
print(f"{len(rows)} calls: median {pct(.5):.0f} ms, p90 {pct(.9):.0f} ms, max {lat[-1]:.0f} ms")
for r in rows:
    d = r["detection"]
    print(f"  gaze={r.get('gaze')}  hazard={d['hazard']!r} where={d['where']} seen={d['seen_by_user']} "
          f"u={d['urgency']} {d['latency_ms']:.0f} ms  events={[e['pattern'] for e in r['events']]}")

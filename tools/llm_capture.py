"""Run the engine on a config and save every Haiku request + reply, then build a gallery page.

    python3 tools/llm_capture.py backend/configs/config.desk.sim.yaml --seconds 30 [--enable big_cat_look lion_unnoticed]
    -> logs/llm_inputs/<run>/index.html  (each call: the exact image(s) sent with the gaze dot, prompt, raw reply)
Console sink, no server; never touches the Dot. The API key is loaded by the detector from .env and never written.
"""
import argparse, base64, html, json, sys, threading, time
from pathlib import Path

import anthropic.resources.messages as msgs

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("config"); ap.add_argument("--seconds", type=float, default=30)
ap.add_argument("--enable", nargs="*", default=None, help="trigger ids to enable (others off)")
ap.add_argument("--source-path", help="override source.path (e.g. a video) and drop paths")
ap.add_argument("--name", default=None)
args = ap.parse_args()

run = time.strftime("%Y%m%d-%H%M%S") + "-" + (args.name or Path(args.config).stem)
OUT = ROOT / "logs" / "llm_inputs" / run
(OUT / "img").mkdir(parents=True, exist_ok=True)
calls, lock = [], threading.Lock()


def record(kwargs, text, usage, t0):
    with lock:
        i = len(calls)
        imgs, texts = [], []
        for m in kwargs.get("messages", []):
            for c in m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]:
                if c["type"] == "image":
                    p = OUT / "img" / f"{i:04d}-{len(imgs)}.jpg"
                    p.write_bytes(base64.b64decode(c["source"]["data"])); imgs.append(p.name)
                elif c["type"] == "text":
                    texts.append(c["text"])
        system = "".join(b["text"] for b in kwargs.get("system", [])) if isinstance(kwargs.get("system"), list) else kwargs.get("system", "")
        calls.append({"i": i, "t": round(t0, 3), "ms": round((time.time() - t0) * 1000), "images": imgs,
                      "user_text": texts, "system": system, "reply": text,
                      "usage": {"in": getattr(usage, "input_tokens", None), "out": getattr(usage, "output_tokens", None)}})


_orig_stream, _orig_create = msgs.Messages.stream, msgs.Messages.create


def stream(self, **kw):
    t0 = time.time(); cm = _orig_stream(self, **kw)

    class Wrap:
        def __enter__(s):
            s.st = cm.__enter__(); return s
        def __exit__(s, *a):
            try:
                m = s.st.get_final_message()
                record(kw, "".join(b.text for b in m.content if b.type == "text"), m.usage, t0)
            except Exception as e:
                record(kw, f"<error {e}>", None, t0)
            return cm.__exit__(*a)
        @property
        def text_stream(s): return s.st.text_stream
        def get_final_message(s): return s.st.get_final_message()
    return Wrap()


def create(self, **kw):
    t0 = time.time(); m = _orig_create(self, **kw)
    record(kw, "".join(b.text for b in m.content if b.type == "text"), m.usage, t0); return m


msgs.Messages.stream, msgs.Messages.create = stream, create

sys.path.insert(0, str(ROOT / "backend"))
from spidey.engine import load_config, run as engine_run  # noqa: E402
cfg = load_config(ROOT / args.config)
cfg["server"] = {"enabled": False}; cfg["sink"] = {"type": "console"}
cfg["log"] = str(OUT / "events.jsonl"); cfg["rules_file"] = str(OUT / "triggers.json")
if args.source_path:
    cfg["source"].pop("paths", None); cfg["source"]["path"] = str((ROOT / args.source_path).resolve())
if args.enable is not None:
    seed = json.load(open(ROOT / "backend" / cfg.get("rules_seed", "desk_test/triggers.desk.json")))
    for r in seed["rules"]:
        r["enabled"] = r["id"] in args.enable
    (OUT / "triggers.json").write_text(json.dumps(seed, indent=1))
engine_run(cfg, args.seconds)
time.sleep(3)

# events (fires) by call time
fires = [json.loads(l) for l in open(OUT / "events.jsonl")] if (OUT / "events.jsonl").exists() else []
fired = [(x["ts"]["sent"], [e.get("trigger") for e in x["events"]]) for x in fires if x.get("events")]
(OUT / "calls.json").write_text(json.dumps(calls, indent=1))
e = html.escape
cards = []
for c in calls:
    try:
        parsed = json.dumps(json.loads(c["reply"][c["reply"].find("{"):c["reply"].rfind("}") + 1]), indent=1)
    except Exception:
        parsed = c["reply"]
    near = [ev for ts, ev in fired if 0 <= ts - c["t"] <= c["ms"] / 1000 + 0.5]
    imgs = "".join(f'<img src="img/{n}">' for n in c["images"])
    cards.append(f'''<div class="card"><div class="h">call {c["i"]} · +{c["t"] - calls[0]["t"]:.1f} s · {c["ms"]} ms ·
 tokens in {c["usage"]["in"]} / out {c["usage"]["out"]}{" · <b>FIRED " + e(str(near)) + "</b>" if near else ""}</div>
 <div class="imgs">{imgs}</div><details><summary>prompt sent (user text)</summary><pre>{e(chr(10).join(c["user_text"]))}</pre></details>
 <div class="lbl">Haiku reply (parsed)</div><pre>{e(parsed)}</pre></div>''')
sysprompt = e(calls[0]["system"]) if calls else ""
(OUT / "index.html").write_text(f'''<!doctype html><meta charset="utf-8"><title>LLM inputs {run}</title>
<style>body{{font:14px Arial;background:#fff;color:#111;margin:20px}}.card{{border:1px solid #ddd;border-radius:8px;padding:12px;margin:12px 0}}
.h{{font-weight:bold;margin-bottom:8px}}.imgs img{{max-width:46%;margin-right:8px;border:1px solid #ccc}}pre{{background:#f6f7f9;padding:8px;white-space:pre-wrap;font-size:12px}}
.lbl{{color:#555;margin-top:8px}}</style>
<h1>What Haiku saw: {e(run)}</h1><p>{len(calls)} calls · config {e(args.config)} · triggers on: {e(str(args.enable))}.
The red dot in each image is the gaze position drawn by the engine before sending.</p>
<details><summary>System prompt (same for every call)</summary><pre>{sysprompt}</pre></details>{"".join(cards)}''')
print(f"\nLLM CAPTURE: {len(calls)} calls -> {OUT / 'index.html'}")

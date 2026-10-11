"""Trace viewer: a tiny local web page over the engine's per-call traces (backend/spidey/trace.py).

    ./scripts/traces.sh                     # http://localhost:8096/  (runs -> recent calls as cards)
                                            # http://localhost:8096/live  (live scene + gaze + boxes + trigger states)
    .venv/bin/python -m spidey.trace_viewer --port 8096 --dir backend/logs/runs   (from backend/)

Also receives ground truth from the desk test page: POST /api/truth {t, test, shown} (text/plain or JSON, the page
uses a no-cors fetch) -> <newest run>/truth.jsonl. Each call card shows what the page showed at the call's frame time
and at decision time. Read-only otherwise; binds to localhost unless --host is given.
"""
from __future__ import annotations

import argparse
import bisect
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "logs" / "runs"
app = FastAPI(title="Spidey traces")


def runs() -> list[Path]:
    if not BASE.exists():
        return []
    return sorted((d for d in BASE.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)


def run_dir(name: str | None) -> Path:
    rs = runs()
    if not name:
        if not rs:
            raise HTTPException(404, "no runs yet")
        return rs[0]
    d = (BASE / name).resolve()
    if d.parent != BASE.resolve() or not d.is_dir():
        raise HTTPException(404, "no such run")
    return d


def read_jsonl(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for ln in p.read_text().splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass      # a line being written
    return out


class Truth:
    def __init__(self, d: Path):
        self.rows = sorted(read_jsonl(d / "truth.jsonl"), key=lambda r: r.get("t") or 0)
        self.ts = [r.get("t") or 0 for r in self.rows]

    def at(self, ts):
        if not ts or not self.rows:
            return None
        i = bisect.bisect_right(self.ts, ts) - 1
        if i < 0:
            return None
        r = self.rows[i]
        return {"shown": r.get("shown"), "test": r.get("test"), "since_s": round(ts - r["t"], 2)}


@app.get("/api/runs")
def api_runs():
    out = []
    for d in runs()[:50]:
        calls = d / "calls.jsonl"
        meta = {}
        try:
            meta = json.loads((d / "meta.json").read_text())
        except (OSError, ValueError):
            pass
        n = sum(1 for _ in open(calls)) if calls.exists() else 0
        out.append({"name": d.name, "calls": n, "mtime": d.stat().st_mtime, "config": meta.get("config"),
                    "commit": meta.get("commit"), "live": (d / "live" / "live.json").exists()})
    return out


@app.get("/api/calls")
def api_calls(run: str | None = None, limit: int = 60, trigger: str | None = None, fired: bool = False,
              misses: bool = False):
    d = run_dir(run)
    recs = read_jsonl(d / "calls.jsonl")
    truth = Truth(d)
    out = []
    for r in reversed(recs):
        decs = r.get("decisions") or []
        if trigger:
            decs = [x for x in decs if x.get("id") == trigger]
            if not decs:
                continue
        if fired and not any(x.get("fired") for x in decs):
            continue
        if misses and not any(x.get("matched") and not x.get("fired") for x in decs):
            continue
        r["truth"] = {"frame": truth.at(r.get("frame_ts")), "decision": truth.at(r.get("t_result"))}
        out.append(r)
        if len(out) >= limit:
            break
    meta = {}
    try:
        meta = json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        pass
    trig_ids = sorted({x.get("id") for r in recs[-500:] for x in r.get("decisions") or [] if x.get("id") != "*"})
    return {"run": d.name, "total": len(recs), "calls": out, "triggers": trig_ids,
            "system_prompts": meta.get("system_prompts", {}), "meta": {k: v for k, v in meta.items()
                                                                       if k != "system_prompts"}}


@app.get("/api/live")
def api_live(run: str | None = None):
    d = run_dir(run)
    try:
        st = json.loads((d / "live" / "live.json").read_text())
    except (OSError, ValueError):
        return {"run": d.name, "live": None}
    st["run"] = d.name
    st["stale_s"] = round(time.time() - (st.get("written") or 0), 2)
    st["truth"] = Truth(d).at(time.time())
    return st


@app.post("/api/truth")
async def api_truth(req: Request):
    body = (await req.body()).decode(errors="replace")
    try:
        rec = json.loads(body)
        assert isinstance(rec, dict)
    except (ValueError, AssertionError):
        raise HTTPException(400, "expected a JSON object")
    rec = {"t": rec.get("t") or time.time(), "test": rec.get("test"), "shown": rec.get("shown"),
           "received": time.time()}
    rs = runs()
    if not rs:
        return JSONResponse({"ok": False, "error": "no run dir yet"}, headers={"Access-Control-Allow-Origin": "*"})
    with open(rs[0] / "truth.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    return JSONResponse({"ok": True, "run": rs[0].name}, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/runs/{run}/{path:path}")
def run_file(run: str, path: str):
    d = run_dir(run)
    p = (d / path).resolve()
    if d.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


@app.get("/live", response_class=HTMLResponse)
def live():
    return LIVE_HTML


@app.get("/demo", response_class=HTMLResponse)
def demo():
    return DEMO_HTML


@app.get("/api/phone")
def api_phone(run: str | None = None):
    d = run_dir(run)
    try:
        st = json.loads((d / "live" / "phone.json").read_text())
    except (OSError, ValueError):
        return {"run": d.name, "on": False}
    st["run"] = d.name
    st["age_s"] = round(time.time() - (st.get("ts") or 0), 2)
    return st


STYLE = """
body{font-family:Arial,Helvetica,sans-serif;background:#fff;color:#111;margin:0;padding:12px 16px;font-size:15px}
h1{font-size:20px;margin:0 0 8px}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px;position:sticky;top:0;background:#fff;
     padding:6px 0;border-bottom:1px solid #ddd;z-index:5}
select,button,label{font-size:15px;font-family:Arial}
.card{border:1px solid #ccc;border-radius:8px;padding:10px;margin:10px 0;display:flex;gap:12px;flex-wrap:wrap}
.card.fired{border-color:#1a8f3a;box-shadow:0 0 0 2px #1a8f3a33}
.card.miss{border-color:#d58b00;box-shadow:0 0 0 2px #d58b0033}
.imgs{display:flex;gap:6px;flex-wrap:wrap}
.imgwrap{position:relative;display:inline-block}
.imgwrap img{display:block;max-width:420px;width:100%;height:auto;border-radius:4px}
.imgwrap svg{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none}
.lbl{font-size:12px;color:#555}
.body{flex:1;min-width:300px}
.dec{margin:3px 0}
.ok{color:#1a8f3a;font-weight:bold}.no{color:#555}.mi{color:#b36b00;font-weight:bold}
.mono{font-family:Menlo,monospace;font-size:12px;white-space:pre-wrap;word-break:break-word;background:#f5f5f5;
      padding:6px;border-radius:4px;max-height:220px;overflow:auto}
details{margin-top:4px}
.k{color:#666}.truth{background:#eef4ff;border-radius:4px;padding:2px 6px}
.err{color:#c00;font-weight:bold}
a{color:#0645ad}
"""

INDEX_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Spidey traces</title><style>""" + STYLE + """</style></head><body>
<h1>Spidey Sense: Haiku call traces <span style="font-size:14px;font-weight:normal"> · <a href="/live">live view</a> · <a href="/demo">demo screen</a></span></h1>
<div class=bar>
 <label>Run <select id=run></select></label>
 <label>Trigger <select id=trig><option value="">all</option></select></label>
 <label><input type=checkbox id=fired> fired only</label>
 <label><input type=checkbox id=misses> misses only (matched, not fired)</label>
 <label>Show <select id=limit><option>30</option><option selected>60</option><option>150</option><option>400</option></select></label>
 <label><input type=checkbox id=auto checked> auto-refresh (2 s)</label>
 <span id=info class=lbl></span>
</div>
<div id=list></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=t=>t?new Date(t*1000).toLocaleTimeString([], {hour12:false})+"."+String(Math.floor((t%1)*10)):"?";
const ms=v=>v==null?"–":Math.round(v)+" ms";
let sys={};
async function loadRuns(){
  const rs=await (await fetch("/api/runs")).json(); const sel=$("run"), cur=sel.value;
  sel.innerHTML=rs.map(r=>`<option value="${esc(r.name)}">${esc(r.name)} (${r.calls})</option>`).join("");
  if(cur && rs.some(r=>r.name===cur)) sel.value=cur;
}
function overlay(rec, imgIdx, nImgs){
  // boxes + gaze samples only on the newest ("now") image: every box refers to it
  if(imgIdx!==nImgs-1) return "";
  let s="";
  const decs={}; (rec.decisions||[]).forEach(d=>decs[d.id]=d);
  const parsed=(rec.parsed&&rec.parsed.triggers)||{};
  let n=0;
  for(const [id,g] of Object.entries(rec.gaze||{})){
    const d=decs[id]||{}; const col=d.fired?"#1a8f3a":(d.matched?"#e09000":"#888"); const off=0.045*(n++);
    for(const b of (g.boxes||[])) s+=`<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="${col}" stroke-width="0.008"/>
      <text x="${b[0]}" y="${Math.max(0.04,b[1]-0.01)+off}" font-size="0.04" fill="${col}" stroke="#fff" stroke-width="0.004" paint-order="stroke">${esc(d.name||id)}</text>`;
    for(const p of (g.samples||[])) s+=`<circle cx="${p[1]}" cy="${p[2]}" r="0.007" fill="${p[3]?'#1a8f3a':'#d00'}" opacity="0.7"/>`;
  }
  // boxes Haiku returned for triggers without a gaze record (e.g. temporal subject/target)
  for(const [id,t] of Object.entries(parsed)){
    if((rec.gaze||{})[id]) continue;
    for(const k of ["bbox","subject_bbox","target_bbox"]){ const b=t&&t[k]; if(!Array.isArray(b)||b.length!==4) continue;
      s+=`<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="#3a6fd8" stroke-width="0.006" stroke-dasharray="0.02 0.01"/>`; }
  }
  const gn=rec.decision_ctx&&rec.decision_ctx.gaze_now;
  if(gn) s+=`<circle cx="${gn[1]}" cy="${gn[2]}" r="0.025" fill="none" stroke="#00b7ff" stroke-width="0.008"/>`;
  return `<svg viewBox="0 0 1 1" preserveAspectRatio="none">${s}</svg>`;
}
function card(r){
  const decs=r.decisions||[], fired=decs.some(d=>d.fired), miss=decs.some(d=>d.matched&&!d.fired);
  const imgs=(r.images||[]).map((im,i)=>`<div><div class=imgwrap><img loading=lazy src="/runs/${encodeURIComponent(cur.run)}/${im.file}">${overlay(r,i,r.images.length)}</div>
     <div class=lbl>${esc(im.label)} ${im.dt!=null?"("+im.dt+" s)":""} · ${Math.round(im.bytes/1024)} KB</div></div>`).join("") || "<div class=lbl>(images pruned or tracing off)</div>";
  const tf=r.truth&&r.truth.frame, td=r.truth&&r.truth.decision;
  const truth=(tf||td)?`<div class=dec><span class=truth>page showed: <b>${esc(tf?tf.shown:"?")}</b> at the frame · <b>${esc(td?td.shown:"?")}</b> at decision</span></div>`:"";
  const det=r.detection||{}, t=r.timings||{}, u=r.usage||{};
  const decHtml=decs.map(d=>d.fired?`<div class=dec><span class=ok>✓ ${esc(d.name)} fired</span></div>`
      :`<div class=dec><span class="${d.matched?'mi':'no'}">✗ ${esc(d.name)}: ${esc(d.why)}</span>${d.haiku_reason?` <span class=lbl>(Haiku: ${esc(d.haiku_reason)})</span>`:""}</div>`).join("") || "<div class=lbl>no enabled camera triggers</div>";
  const gz=Object.entries(r.gaze||{}).map(([id,g])=>`${id}: ${g.n_samples??"-"} samples since ${g.since??"-"} s, ${g.n_inside??0} inside, looked_at_s_ago ${g.looked_at_s_ago??"null"}${g.note?" ("+g.note+")":""}${g.trend_state?"\\n  trend: "+(g.trend_state.why||"")+" "+JSON.stringify(g.trend_state.trend):""}`).join("\\n");
  return `<div class="card ${fired?'fired':miss?'miss':''}"><div class=imgs>${imgs}</div><div class=body>
   <div><b>#${r.id}</b> · frame ${fmt(r.frame_ts)} · result ${fmt(r.t_result)} ${r.stale?'<span class=err>STALE</span>':''} ${r.paused?'<span class=err>paused</span>':''} ${r.error?'<span class=err>'+esc(r.error)+'</span>':''}</div>
   ${truth}
   <div class=dec>${det.hazard?`<b style="color:#c00">hazard: ${esc(det.hazard)}</b> u=${det.urgency} · `:""}<i>${esc(det.reason)}</i></div>
   ${decHtml}
   <div class=lbl>frame→result ${ms(t.frame_to_result_ms)} · early fields ${ms(t.frame_to_early_ms)} (call +${ms(t.early_ms)}) · full call ${ms(t.full_ms)} · frame→dispatch ${ms(t.frame_to_dispatch_ms)} · tokens in ${u.input_tokens??"-"} / out ${u.output_tokens??"-"}${r.early&&r.early.fired&&r.early.fired.length?" · fired on early: "+esc(r.early.fired.join(", ")):""}</div>
   <details><summary>raw output, parsed JSON, prompt, gaze</summary>
     <div class=k>raw text</div><div class=mono>${esc(r.raw_text)}</div>
     <div class=k>user text (system prompt ${esc((r.prompt||{}).system_sha)})</div><div class=mono>${esc((r.prompt||{}).user_text)}</div>
     <div class=k>gaze checks</div><div class=mono>${esc(gz||"–")}</div>
     <div class=k>decision context</div><div class=mono>${esc(JSON.stringify(r.decision_ctx||{}))}</div>
   </details></div></div>`;
}
let cur={};
async function load(){
  const q=new URLSearchParams({limit:$("limit").value}); if($("run").value) q.set("run",$("run").value);
  if($("trig").value) q.set("trigger",$("trig").value); if($("fired").checked) q.set("fired","true"); if($("misses").checked) q.set("misses","true");
  let res; try{ res=await fetch("/api/calls?"+q); }catch(e){ $("info").textContent="viewer offline"; return; }
  if(!res.ok){ $("list").innerHTML="<p>No runs yet: start the engine (tracing is on by default).</p>"; return; }
  cur=await res.json();
  const ts=$("trig"), tv=ts.value; ts.innerHTML='<option value="">all</option>'+cur.triggers.map(t=>`<option>${esc(t)}</option>`).join(""); ts.value=tv;
  $("info").textContent=`${cur.calls.length} shown of ${cur.total} calls · updated ${new Date().toLocaleTimeString()}`;
  const openIds=new Set([...document.querySelectorAll("details[open]")].map(d=>d.closest(".card")?.dataset.id));
  $("list").innerHTML=cur.calls.map(r=>card(r).replace('<div class="card','<div data-id="'+r.id+'" class="card')).join("");
  document.querySelectorAll(".card").forEach(c=>{ if(openIds.has(c.dataset.id)) c.querySelector("details").open=true; });
}
["run","trig","fired","misses","limit"].forEach(id=>$(id).addEventListener("change",load));
(async()=>{ await loadRuns(); await load(); })();
setInterval(async()=>{ if($("auto").checked && !document.querySelector("details[open]")){ await loadRuns(); await load(); } },2000);
</script></body></html>"""

LIVE_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Spidey live</title><style>""" + STYLE + """
body{font-size:18px}
#wrap{position:relative;display:inline-block;max-width:100%}
#scene{display:block;max-width:100%;width:960px;height:auto;background:#eee;border-radius:6px}
#ov{position:absolute;left:0;top:0;width:100%;height:100%}
#head{font-size:20px;margin:6px 0}
.trow{margin:6px 0;font-size:20px}
.state{display:inline-block;min-width:110px;padding:2px 8px;border-radius:5px;color:#fff;font-weight:bold;text-align:center}
.fired{background:#1a8f3a}.armed{background:#666}.cool{background:#3a6fd8}
</style></head><body>
<h1>Spidey Sense: live <span style="font-size:14px;font-weight:normal"> · <a href="/">all calls</a></span></h1>
<div id=head>…</div>
<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">
 <div id=wrap><img id=scene alt="waiting for the engine"><svg id=ov viewBox="0 0 1 1" preserveAspectRatio="none"></svg></div>
 <div id=panel style="min-width:300px;flex:1"></div>
</div>
<p class=lbl>Red dot = gaze now (trail = last 1 s). Boxes: Haiku's newest box per trigger (green = fired, amber = matched but not fired, gray = old).
Polls ~5x/s; the engine rewrites the frame ~10x/s.</p>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let busy=false, run=null;
async function tick(){
  if(busy) return; busy=true;
  try{
    const st=await (await fetch("/api/live"+(run?"?run="+encodeURIComponent(run):""))).json();
    if(!st || !st.frame_ts){ $("head").textContent="No live data yet (run "+(st&&st.run||"?")+"): is the engine running with tracing on?"; return; }
    const img=new Image(); img.onload=()=>{$("scene").src=img.src;}; img.src="/runs/"+encodeURIComponent(st.run)+"/live/frame.jpg?t="+st.written;
    const lc=st.last_call;
    const mo=st.motion||{}, mtxt=mo.walking==null?"":` · ${mo.walking?`<b>walking</b>${mo.cadence_spm?` ${mo.cadence_spm} steps/min`:""}`:"still"}${mo.turning?" · turning":""}`;
    $("head").innerHTML=`frame age <b>${Math.round(st.frame_age_s*1000)} ms</b>${mtxt}${st.stale_s>1.5?` · <span class=err>no video from the glasses for ${st.stale_s} s (phone→Mac Wi-Fi stalled)</span>`:st.frame_age_s>0.8?` · <span class=err>video delayed (Wi-Fi)</span>`:""}`
      +(st.paused?` · <span class=err>PAUSED</span>`:"")
      +(st.truth?` · page shows <span class=truth><b>${esc(st.truth.shown)}</b></span>`:"")
      +(lc?`<br>Haiku #${lc.id} (${lc.age_s} s old): ${lc.hazard?`<b style="color:#c00">${esc(lc.hazard)}</b> · `:""}<i>${esc(lc.reason)}</i>`:"");
    let s="";
    for(const t of st.triggers||[]){
      const col=t.state==="fired"?"#1a8f3a":(t.matched?"#e09000":"#999");
      for(const [b,dash] of [[t.bbox,""],[t.target_bbox,"0.02 0.01"]]){ if(!b) continue;
        s+=`<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="${col}" stroke-width="0.006" stroke-dasharray="${dash}"/>`;
        if(!dash) s+=`<text x="${b[0]}" y="${Math.max(0.035,b[1]-0.01)}" font-size="0.035" fill="${col}" stroke="#fff" stroke-width="0.004" paint-order="stroke">${esc(t.name)} (${t.box_age_s} s)</text>`; }
    }
    const tr=st.trail||[];
    if(tr.length>1) s+=`<polyline points="${tr.map(p=>p[1]+","+p[2]).join(" ")}" fill="none" stroke="#ff3030" stroke-width="0.004" opacity="0.6"/>`;
    if(st.gaze) s+=`<circle cx="${st.gaze[0]}" cy="${st.gaze[1]}" r="0.012" fill="#ff1010" stroke="#fff" stroke-width="0.004"/>`;
    $("ov").innerHTML=s;
    $("panel").innerHTML=(st.triggers||[]).map(t=>{
      const cls=t.state==="fired"?"fired":t.state.startsWith("cooldown")?"cool":"armed";
      return `<div class=trow><span class="state ${cls}">${esc(t.state)}</span> <b>${esc(t.name)}</b> <span class=lbl>${esc(t.gaze_mode)}${t.temporal?" · temporal":""}</span>
        <div class=lbl style="font-size:16px">${t.hint?esc(t.hint)+" · ":""}last call: ${esc(t.why||"–")}</div></div>`;}).join("") || "<p>No enabled camera triggers.</p>";
  }catch(e){ $("head").textContent="viewer offline? "+e; }
  finally{ busy=false; }
}
setInterval(tick,200); tick();
</script></body></html>"""


DEMO_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Spidey Sense Demo</title>
<link rel=preconnect href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel=stylesheet>
<style>
:root{--bg:#0e0e11;--panel:#17171c;--ink:#f5f5f7;--muted:#9a9aa5;--coral:#ff5a5f;--line:#2a2a31}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font-family:"Plus Jakarta Sans",-apple-system,Helvetica,Arial,sans-serif;overflow:hidden}
#grid{display:grid;grid-template-columns:70fr 30fr;gap:18px;height:100vh;padding:18px}
.col{display:flex;flex-direction:column;min-height:0;min-width:0}
.top{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.brand{font-weight:800;font-size:26px;letter-spacing:-.02em;margin-right:8px}.brand span{color:var(--coral)}
.pill{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border-radius:999px;background:var(--panel);border:1px solid var(--line);
  font-size:15px;font-weight:600;color:var(--muted);transition:all .3s}
.pill.on{color:var(--ink);border-color:#3a3a44}.pill .d{width:9px;height:9px;border-radius:50%;background:#55555f}
.pill.on .d{background:#3ad16f;box-shadow:0 0 10px #3ad16f99}.pill.warn .d{background:#ffb020}
#stage{position:relative;flex:1;min-height:0;display:flex;align-items:center;justify-content:center;background:#000;border-radius:22px;
  overflow:hidden;border:2px solid var(--line);transition:border-color .4s, box-shadow .4s}
#scenebox{position:relative;max-width:100%;max-height:100%}
#scene{display:block;width:100%;height:100%;object-fit:contain}
#ov{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
#banner{position:absolute;left:24px;right:24px;bottom:24px;background:rgba(14,14,17,.86);backdrop-filter:blur(12px);
  border-radius:20px;padding:18px 22px;display:flex;gap:18px;align-items:center;transform:translateY(140%);opacity:0;
  transition:transform .45s cubic-bezier(.2,.9,.25,1.2),opacity .3s;border:1px solid #ffffff1f}
#banner.show{transform:none;opacity:1}
#swatch{width:54px;height:54px;border-radius:16px;flex:none;box-shadow:0 0 24px currentColor}
#bname{font-size:30px;font-weight:800;letter-spacing:-.01em}#bwhy{font-size:19px;color:#d6d6dc;margin-top:2px}
#bmeta{margin-left:auto;text-align:right;font-size:15px;color:var(--muted);white-space:nowrap}#bmeta b{display:block;font-size:30px;color:var(--ink)}
#heard{position:absolute;left:24px;top:20px;max-width:70%;background:rgba(14,14,17,.75);border-radius:14px;padding:10px 14px;font-size:17px;
  opacity:0;transition:opacity .4s}#heard.show{opacity:1}#heard .k{color:var(--coral);font-weight:700;margin-right:6px}
#status{position:absolute;right:20px;top:18px;font-size:14px;color:var(--muted);background:rgba(14,14,17,.7);padding:6px 12px;border-radius:999px}
#wait{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:20px;text-align:center;padding:40px}
.phonecol{align-items:center;justify-content:center}
.phonetitle{font-size:16px;color:var(--muted);font-weight:600;margin-bottom:12px;align-self:stretch;text-align:center}
#phone{position:relative;height:min(100%,calc((100vh - 90px)));aspect-ratio:9/19.5;max-width:100%;background:#000;border-radius:54px;
  padding:13px;box-shadow:0 0 0 2px #3a3a44,0 30px 80px #0009}
#phone .screen{position:relative;width:100%;height:100%;border-radius:42px;overflow:hidden;background:#f7f6f4}
#phoneimg{width:100%;height:100%;object-fit:cover;display:block}
#phonewait{position:absolute;inset:0;display:flex;flex-direction:column;gap:10px;align-items:center;justify-content:center;color:#666;
  font-size:16px;text-align:center;padding:28px;background:#f7f6f4}
#phonewait b{color:#222;font-size:19px}
</style></head><body>
<div id=grid>
 <div class=col>
  <div class=top>
   <div class=brand>Spidey <span>Sense</span></div>
   <div class=pill id=pgaze><span class=d></span><span>Gaze</span></div>
   <div class=pill id=pwalk><span class=d></span><span>Still</span></div>
   <div class=pill id=pear><span class=d></span><span>Hearing</span></div>
   <div class=pill id=pheart><span class=d></span><span>Heart</span></div>
  </div>
  <div id=stage>
   <div id=scenebox><img id=scene alt=""><svg id=ov viewBox="0 0 1 1" preserveAspectRatio="none"></svg></div>
   <div id=heard><span class=k>Heard</span><span id=heardtxt></span></div>
   <div id=status></div>
   <div id=wait>Waiting for the glasses…<br><span style="font-size:15px">Start the engine with tracing on.</span></div>
   <div id=banner><div id=swatch></div><div><div id=bname></div><div id=bwhy></div></div><div id=bmeta></div></div>
  </div>
 </div>
 <div class="col phonecol">
  <div class=phonetitle>Spidey Sense app · live from the phone</div>
  <div id=phone><div class=screen><img id=phoneimg alt=""><div id=phonewait><b>Phone not mirrored</b>In the app: Settings → Mirror screen to Mac</div></div></div>
 </div>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rgbcss=c=>c&&c.length===3?`rgb(${c[0]},${c[1]},${c[2]})`:"#ff5a5f";
let busy=false, lastFrame=0, lastPhone=0, shownFire=0;
function pill(id,on,text,warn){const p=$(id);p.classList.toggle("on",!!on);p.classList.toggle("warn",!!warn);p.lastElementChild.textContent=text;}
function fit(st){ // size the scene box to the image aspect inside the stage
  const wh=st.img_wh||[1600,1200], S=$("stage").getBoundingClientRect(), a=wh[0]/wh[1];
  let w=S.width, h=w/a; if(h>S.height){h=S.height; w=h*a;}
  $("scenebox").style.width=w+"px"; $("scenebox").style.height=h+"px";
}
async function tick(){
  if(busy) return; busy=true;
  try{
    const st=await (await fetch("/api/live")).json();
    if(!st||!st.frame_ts){ $("wait").style.display="flex"; return; }
    $("wait").style.display=st.stale_s>3?"flex":"none";
    if(st.stale_s>3) $("wait").innerHTML="Waiting for the glasses…<br><span style='font-size:15px'>no video for "+st.stale_s+" s</span>";
    fit(st);
    if(st.written!==lastFrame){ lastFrame=st.written; const im=new Image(); im.onload=()=>{$("scene").src=im.src;}; im.src="/runs/"+encodeURIComponent(st.run)+"/live/frame.jpg?t="+st.written; }
    let s="";
    const recent=st.last_fire&&(st.now-st.last_fire.ts)<6;
    for(const t of st.triggers||[]){ if(!t.bbox) continue;
      const fired=t.state==="fired", col=fired?rgbcss((st.last_fire||{}).rgb):(t.matched?"#ffb020":"#ffffff66"), b=t.bbox;
      if(!fired&&!t.matched) continue;
      s+=`<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" rx="0.01" fill="none" stroke="${col}" stroke-width="0.005"/>`
        +`<text x="${b[0]+0.006}" y="${Math.max(0.04,b[1]-0.012)}" font-size="0.032" font-weight="700" fill="${col}" stroke="#000" stroke-width="0.006" paint-order="stroke">${esc(t.name)}</text>`; }
    const tr=st.trail||[];
    if(tr.length>1) s+=`<polyline points="${tr.map(p=>p[1]+","+p[2]).join(" ")}" fill="none" stroke="#ff5a5f" stroke-width="0.005" stroke-linecap="round" opacity="0.55"/>`;
    if(st.gaze) s+=`<circle cx="${st.gaze[0]}" cy="${st.gaze[1]}" r="0.026" fill="#ff5a5f33" stroke="#ff5a5f" stroke-width="0.004"/><circle cx="${st.gaze[0]}" cy="${st.gaze[1]}" r="0.009" fill="#ff5a5f"/>`;
    $("ov").innerHTML=s;
    const mo=st.motion||{};
    pill("pgaze",!!st.gaze,st.gaze?"Gaze tracked":(st.worn===false?"Glasses off":"No gaze"),!st.gaze);
    pill("pwalk",!!mo.walking,mo.walking==null?"Motion –":mo.walking?`Walking${mo.cadence_spm?" · "+mo.cadence_spm+" steps/min":""}`:(mo.turning?"Turning head":"Still"));
    pill("pear",!!st.listening,st.listening?"Hearing":"Not listening");
    const h=st.heart; let ht="Heart –";
    if(h&&h.bpm){ const ago=h.reading_ts?Math.round((Date.now()/1000-h.reading_ts)/60):null; ht=`♥ ${Math.round(h.bpm)} bpm${ago!=null?` · ${ago<1?"now":ago+" min ago"}`:""}`; }
    else if(h&&h.connected) ht="♥ Oura · waiting for sync";
    pill("pheart",!!(h&&h.connected),ht);
    $("heardtxt").textContent=st.heard||""; $("heard").classList.toggle("show",!!st.heard);
    $("status").textContent=st.paused?"PAUSED":`video ${Math.round(st.frame_age_s*1000)} ms`;
    const b=$("banner"), lf=st.last_fire;
    if(recent){
      if(lf.ts!==shownFire){ shownFire=lf.ts; const c=rgbcss(lf.rgb);
        $("swatch").style.background=c; $("swatch").style.color=c;
        $("bname").textContent=lf.name||"Spidey Sense"; $("bwhy").textContent=lf.why||"";
        $("bmeta").innerHTML=lf.latency_ms?`<b>${(lf.latency_ms/1000).toFixed(1)} s</b>frame → buzz`:"";
        $("stage").style.borderColor=c; $("stage").style.boxShadow=`0 0 60px ${c}88`;
        setTimeout(()=>{$("stage").style.borderColor="";$("stage").style.boxShadow="";},1600); }
      b.classList.add("show");
    } else b.classList.remove("show");
  }catch(e){ $("status").textContent="viewer offline"; }
  finally{ busy=false; }
}
async function phone(){
  try{
    const p=await (await fetch("/api/phone")).json();
    const live=p.on&&p.age_s<3;
    $("phonewait").style.display=live?"none":"flex";
    if(live&&p.ts!==lastPhone){ lastPhone=p.ts; const im=new Image(); im.onload=()=>{$("phoneimg").src=im.src;};
      im.src="/runs/"+encodeURIComponent(p.run)+"/live/phone.jpg?t="+p.ts; }
  }catch(e){}
}
setInterval(tick,120); setInterval(phone,110); tick(); phone(); addEventListener("resize",()=>{lastFrame=0;});
</script></body></html>"""


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--dir", default=str(BASE), help="runs directory (trace.dir of the engine config)")
    a = ap.parse_args()
    BASE = Path(a.dir).expanduser().resolve()
    import uvicorn
    print(f"Trace viewer: http://localhost:{a.port}/   live: http://localhost:{a.port}/live   (runs: {BASE})", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()

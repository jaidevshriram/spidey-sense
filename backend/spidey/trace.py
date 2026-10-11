"""Per-call tracing of the Haiku detector, written off the engine loop.

Each engine run gets  <trace.dir>/<YYYYmmdd-HHMMSS>-<config name>/  (default backend/logs/runs/...):
    meta.json      config, commit, model, start time, the system prompt text (by its sha256)
    calls.jsonl    one record per full Haiku call (see call_record): images sent, prompt, raw text, parsed JSON,
                   timings, usage, gaze samples per trigger, trend state, the rule decision per enabled trigger
    img/<id>-<k>.jpg   the exact JPEG bytes sent in that call (gaze dot as drawn), k = 0.. oldest first
    live/frame.jpg + live/live.json   the newest scene frame (~640 px) + gaze trail + boxes + trigger states,
                   rewritten ~10x/s (atomic rename) for the viewer's /live page
    truth.jsonl    ground truth posted by the desk test page to the viewer (scripts/traces.sh)

Writes go through a bounded queue to a background thread (put_nowait: a full queue drops the record and
counts it, it never blocks the engine). Size cap: the oldest calls/images past keep_calls or keep_mb are pruned;
runs older than keep_days (2) are deleted at start. Viewer: backend/spidey/trace_viewer.py (scripts/traces.sh).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import threading
import time
from collections import deque
from pathlib import Path

import cv2

DEFAULTS = {"enabled": True, "dir": "logs/runs", "keep_calls": 2000, "keep_mb": 500, "keep_days": 2,
            "live": True, "live_hz": 10}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def prune_runs(base: Path, keep_days: float, keep_mb: float, exclude: Path | None = None) -> list[str]:
    """Delete run dirs older than keep_days, then the oldest ones while all runs together exceed keep_mb."""
    removed = []
    if not base.exists():
        return removed
    runs = sorted((d for d in base.iterdir() if d.is_dir() and d != exclude), key=lambda d: d.stat().st_mtime)
    now = time.time()
    for d in list(runs):
        if now - d.stat().st_mtime > keep_days * 86400:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
            runs.remove(d)
    sizes = {d: dir_size(d) for d in runs}
    total = sum(sizes.values())
    for d in runs:
        if total <= keep_mb * 1e6:
            break
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
        total -= sizes[d]
    return removed


class Tracer:
    def __init__(self, cfg: dict | None, root: Path, config_name: str, meta: dict | None = None):
        c = {**DEFAULTS, **(cfg or {})}
        self.cfg = c
        self.keep_calls, self.keep_bytes = int(c["keep_calls"]), float(c["keep_mb"]) * 1e6
        base = Path(c["dir"]).expanduser()
        base = base if base.is_absolute() else root / base
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.run_dir = base / f"{stamp}-{config_name}"
        removed = prune_runs(base, float(c["keep_days"]), float(c["keep_mb"]))
        (self.run_dir / "img").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "live").mkdir(exist_ok=True)
        self.calls_path = self.run_dir / "calls.jsonl"
        self.meta = {"run": self.run_dir.name, "started": time.time(), "config": config_name, **(meta or {}),
                     "system_prompts": {}}
        self._write_meta()
        self.q: queue.Queue = queue.Queue(maxsize=300)
        self.dropped = 0
        self.n_written = 0
        self._index: deque = deque()          # (call_id, [img paths], bytes) oldest first
        self._bytes = 0
        self._lines = 0
        self._live_slot = None                # latest-wins (jpeg source frame, state dict)
        self._live_evt = threading.Event()
        self._stop = False
        threading.Thread(target=self._writer, name="trace-writer", daemon=True).start()
        if c.get("live", True):
            threading.Thread(target=self._live_writer, name="trace-live", daemon=True).start()
        print(f"trace: {self.run_dir}" + (f" (pruned {len(removed)} old runs)" if removed else ""), flush=True)

    # ---- engine-facing (non-blocking) ---------------------------------------------------------
    def add_system_prompt(self, text: str) -> str:
        h = sha(text)
        if h not in self.meta["system_prompts"]:
            self.meta["system_prompts"][h] = text
            self.q.put(("meta", None))
        return h

    def submit(self, rec: dict, images: list[bytes]) -> None:
        try:
            self.q.put_nowait(("call", (rec, images)))
        except queue.Full:
            self.dropped += 1

    def live(self, frame, state: dict) -> None:
        """Latest-wins: the live thread encodes + writes it (frame.image is not modified by the engine)."""
        self._live_slot = (frame, state)
        self._live_evt.set()

    def close(self) -> None:
        self._stop = True
        try:
            self.q.put(("stop", None), timeout=1)
        except queue.Full:
            pass

    # ---- background ---------------------------------------------------------------------------
    def _write_meta(self) -> None:
        _atomic_write(self.run_dir / "meta.json", json.dumps(self.meta, indent=1, default=str).encode())

    def _writer(self) -> None:
        while True:
            kind, item = self.q.get()
            if kind == "stop":
                return
            try:
                if kind == "meta":
                    self._write_meta()
                elif kind == "call":
                    self._write_call(*item)
            except Exception as e:      # tracing must never take the engine down
                print(f"trace: write failed ({type(e).__name__}: {e})", flush=True)

    def _write_call(self, rec: dict, images: list[bytes]) -> None:
        paths, nbytes = [], 0
        for k, jpg in enumerate(images):
            p = self.run_dir / "img" / f"{rec['id']}-{k}.jpg"
            p.write_bytes(jpg)
            paths.append(p)
            nbytes += len(jpg)
        line = json.dumps(rec, default=str) + "\n"
        with open(self.calls_path, "a") as f:
            f.write(line)
        self._lines += 1
        self.n_written += 1
        nbytes += len(line)
        self._index.append((rec["id"], paths, nbytes))
        self._bytes += nbytes
        self._prune()

    def _prune(self) -> None:
        pruned = False
        while self._index and (len(self._index) > self.keep_calls or self._bytes > self.keep_bytes):
            _, paths, nbytes = self._index.popleft()
            for p in paths:
                try:
                    p.unlink()
                except OSError:
                    pass
            self._bytes -= nbytes
            pruned = True
        # calls.jsonl is append-only; rewrite it (atomically) once it holds 10% more lines than kept calls
        if pruned and self._lines > len(self._index) * 1.1 + 20:
            keep = {cid for cid, _, _ in self._index}
            lines = [ln for ln in self.calls_path.read_text().splitlines(keepends=True)
                     if _line_id(ln) in keep]
            _atomic_write(self.calls_path, "".join(lines).encode())
            self._lines = len(lines)

    def _live_writer(self) -> None:
        period = 1.0 / max(1.0, float(self.cfg.get("live_hz", 10)))
        live = self.run_dir / "live"
        while not self._stop:
            self._live_evt.wait(1.0)
            self._live_evt.clear()
            slot, self._live_slot = self._live_slot, None
            if slot is None:
                continue
            t0 = time.time()
            frame, state = slot
            try:
                img = frame.image
                h, w = img.shape[:2]
                s = min(1.0, 640 / max(h, w))
                small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img
                ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    _atomic_write(live / "frame.jpg", jpg.tobytes())
                    state["img_wh"] = [small.shape[1], small.shape[0]]
                    state["written"] = time.time()
                    _atomic_write(live / "live.json", json.dumps(state, default=str).encode())
            except Exception as e:
                print(f"trace: live write failed ({type(e).__name__}: {e})", flush=True)
            time.sleep(max(0.0, period - (time.time() - t0)))


def _line_id(line: str):
    try:
        return json.loads(line).get("id")
    except ValueError:
        return None


def make_tracer(cfg: dict, root: Path, meta: dict | None = None) -> Tracer | None:
    tc = cfg.get("trace")
    tc = {} if tc is None else tc
    if not {**DEFAULTS, **tc}.get("enabled", True):
        return None
    name = Path(cfg.get("_config_path", "config.yaml")).stem
    try:
        return Tracer(tc, root, name, meta)
    except Exception as e:
        print(f"trace: off ({type(e).__name__}: {e})", flush=True)
        return None


# ---- record building (engine thread; cheap: no encoding, no I/O) ----------------------------------
def _ms(a, b):
    return None if a is None or b is None else round((b - a) * 1000, 1)


def call_record(det, *, decisions: list[dict] | None, gaze: dict | None, early: dict | None,
                t_result: float | None, events: list[dict] | None, paused: bool, quiet: bool,
                system_sha: str | None, ctx: dict | None = None, stale: bool = False) -> tuple[dict, list[bytes]]:
    """One calls.jsonl record + the JPEG bytes to save, from a full Detection carrying det.trace."""
    from .detectors import parse_json
    tr = det.trace or {}
    raw = det.raw or {}
    cid = tr.get("call_id") or raw.get("call_id") or 0
    frame_ts = raw.get("frame_ts")
    imgs, images = [], []
    for k, im in enumerate(tr.get("images") or []):
        images.append(im["jpeg"])
        imgs.append({"k": k, "label": im["label"], "file": f"img/{cid}-{k}.jpg", "frame_ts": im["frame_ts"],
                     "dt": round(im["frame_ts"] - frame_ts, 3) if frame_ts else None,
                     "gaze": im["gaze"], "gaze_norm": im["gaze_norm"], "src_wh": im["src_wh"],
                     "bytes": len(im["jpeg"])})
    text = raw.get("text")
    t_sub, t_start, t_early, t_full = tr.get("t_submit"), tr.get("t_start"), tr.get("t_early"), tr.get("t_full")
    rec = {
        "id": cid, "frame_ts": frame_ts, "t_result": t_result, "n_images": raw.get("n_images"),
        "images": imgs,
        "prompt": {"model": tr.get("model"), "system_sha": system_sha, "user_text": tr.get("user_text"),
                   "triggers": tr.get("triggers")},
        "raw_text": text, "parsed": parse_json(text) if text else None,
        "parse_error": bool(raw.get("parse_error")), "error": raw.get("error"), "stop_reason": raw.get("stop_reason"),
        "usage": {"input_tokens": raw.get("input_tokens"), "output_tokens": raw.get("output_tokens"),
                  "cache_read": raw.get("cache_read")},
        "timings": {"dispatch": t_sub, "queue_ms": _ms(t_sub, t_start),
                    "frame_to_dispatch_ms": _ms(frame_ts, t_sub),
                    "early_ms": _ms(t_start, t_early), "full_ms": _ms(t_start, t_full),
                    "frame_to_early_ms": _ms(frame_ts, (early or {}).get("t") or t_early),
                    "frame_to_result_ms": _ms(frame_ts, t_result)},
        "detection": {"hazard": det.hazard, "where": det.where, "seen_by_user": det.seen_by_user,
                      "urgency": det.urgency, "reason": det.reason},
        "gaze": gaze or {},
        "decisions": decisions or [],
        "early": early,
        "events": [{k: e.get(k) for k in ("id", "rule", "pattern", "latency_ms", "paused", "quiet")}
                   for e in events or []],
        "paused": paused, "quiet": quiet,
    }
    if ctx:
        rec["decision_ctx"] = {"max_result_age_s": ctx.get("max_result_age_s"), "gaze_now": ctx.get("gaze_now")}
    if stale:
        rec["stale"] = True
        rec["decisions"] = [{"id": "*", "name": "(all)", "fired": False, "matched": False,
                             "why": "stale: a newer frame's result was already delivered; not evaluated"}]
    return rec, images


def light_record(rec: dict, raw_max: int = 2000) -> dict:
    """The app's `trace` message (no images)."""
    d = rec["detection"]
    return {"type": "trace", "id": rec["id"], "ts": rec["frame_ts"], "t": rec["t_result"], "hazard": d["hazard"],
            "urgency": d["urgency"], "reason": d["reason"], "latency_ms": rec["timings"]["frame_to_result_ms"],
            "n_images": rec["n_images"], "paused": rec["paused"], "stale": bool(rec.get("stale")),
            "error": rec["error"], "raw": (rec["raw_text"] or "")[:raw_max],
            "decisions": rec["decisions"]}

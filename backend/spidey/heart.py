"""Heart-rate "sense": live bpm from an Oura ring (via open_oura), a CSV replay, or a fake generator.

    HeartRateSense(backend="replay" | "open_oura" | "fake", on_sample=None, **opts)
        .status() -> {connected, backend, last_bpm, median_bpm, rate_hz, since_last_s, detail, ...}
        .close()

Like `spidey.radar.RadarSense`: everything runs on a background thread, the constructor never raises or
blocks, and `on_sample` errors are caught and printed. `on_sample` gets one dict per heartbeat/reading:

    {"ts": unix s (data time, see below), "bpm": 104.0, "bpm_smooth": 103.0 (median of the last 5 s),
     "median_bpm": 71.0 (rolling 2-min median) | None, "baseline_s": seconds of history in the window,
     "baseline_ready": bool (>= min_baseline_s of history), "backend": "replay", "ibi_ms": 577 | None}

Time: `ts` is the time the reading *describes*. For open_oura and real-time replay/fake it is wall-clock time;
with `speed > 1` replay/fake run ahead of the wall clock (ts advances `speed` x faster), so the median window,
the rule's sustain and its cooldown all stay in data time. That is what makes accelerated tests exact.

Trigger logic (pure, see `heart_step`): a `source: "heart_rate"` trigger fires when the smoothed bpm is above
the rolling 2-min median by >= `min_delta_bpm` (default 15) OR by >= `min_delta_pct` % (default 20), sustained
for >= `sustain_s` (default 8), not within `cooldown_s` (default 60) of the last fire, and only once per
excursion: it re-arms after the bpm returns below the threshold.

Backends:
  * replay    - plays a CSV `ts_s,bpm` (header optional) at real time x `speed`; `loop=True` repeats it.
  * fake      - synthetic resting HR (`rest_bpm` 70 +- `noise_bpm` 2) at ~1 Hz; optional scripted spike
                (`spike_at_s`, `spike_bpm` 105, `spike_s` 20), repeating every `spike_every_s` if set.
  * open_oura - runs the open_oura Rust CLI (github.com/Th0rgal/open_oura) as a subprocess:
                `oura --key-file K --db D [--name N] [--address A] live-hr --seconds CHUNK`, parses its
                "  72 bpm (IBI 833 ms)" lines (verified in crates/oura-cli/src/main.rs, cmd_live_hr), restarts it
                when a chunk ends and with backoff on failure. The auth key comes from the repo-root .env:
                OURA_KEY_FILE (path to the hex key file `oura pair` writes) or OURA_AUTH_KEY (the 32 hex chars;
                written to a private 0600 temp file for the CLI, deleted on close). OURA_BIN, OURA_NAME,
                OURA_ADDRESS are optional. The key is never printed or logged. See docs/oura.md.
                This backend talks Bluetooth (through the CLI). Run it from Terminal.app, only when intended.

CLI:  python -m spidey.heart --backend replay --file tests/data/hr_spike.csv --speed 10
      python -m spidey.heart --backend open_oura          (at home, ring paired: prints live bpm + fires)
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKENDS = ("replay", "fake", "open_oura")

WINDOW_S = 120.0            # rolling median window ("2-min median")
SMOOTH_S = 5.0              # bpm compared against the median = median of the last 5 s (per-beat bpm is jumpy)
MIN_BASELINE_S = 30.0       # no fires until the window holds this much history
MAX_GAP_S = 10.0            # a gap this long between readings restarts a sustained excursion
OURA_CHUNK_S = 600          # `live-hr --seconds`; the CLI restores the ring's AUTOMATIC HR mode after each chunk
OURA_BACKOFF_S = (2, 5, 10, 20, 30)
LIVE_HR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*bpm(?:\s*\(\s*IBI\s*(\d+)\s*ms\s*\))?", re.IGNORECASE)
HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")

DEFAULT_IF = {"min_delta_bpm": 15.0, "min_delta_pct": 20.0, "sustain_s": 8.0}
DEFAULT_COOLDOWN_S = 60.0


# =====================================================================================
# Trigger logic (pure)
# =====================================================================================

@dataclass(frozen=True)
class HeartParams:
    min_delta_bpm: float = 15.0
    min_delta_pct: float = 20.0
    sustain_s: float = 8.0
    cooldown_s: float = 60.0

    @classmethod
    def from_rule(cls, if_: dict | None, cooldown_s: float | None = None) -> "HeartParams":
        """A trigger's `if` block (+ cooldown_s) -> params. Accepts the legacy `hr_band_bpm` as min_delta_bpm."""
        c = dict(if_ or {})

        def num(key, default):
            try:
                v = float(c.get(key))
                return v if math.isfinite(v) and v > 0 else default
            except (TypeError, ValueError):
                return default

        bpm = num("min_delta_bpm", None)
        if bpm is None:
            bpm = num("hr_band_bpm", DEFAULT_IF["min_delta_bpm"])
        try:
            cd = float(cooldown_s) if cooldown_s is not None else DEFAULT_COOLDOWN_S
        except (TypeError, ValueError):
            cd = DEFAULT_COOLDOWN_S
        return cls(min_delta_bpm=bpm, min_delta_pct=num("min_delta_pct", DEFAULT_IF["min_delta_pct"]),
                   sustain_s=num("sustain_s", DEFAULT_IF["sustain_s"]), cooldown_s=max(0.0, cd))

    def threshold(self, median: float) -> float:
        """bpm above which we count as "racing": the lower of median + X bpm and median * (1 + Y%)."""
        return min(median + self.min_delta_bpm, median * (1 + self.min_delta_pct / 100.0))


@dataclass(frozen=True)
class HeartState:
    armed: bool = True
    above_since: float | None = None      # data time the current excursion started
    last_ts: float | None = None          # previous reading
    last_fire: float | None = None


def heart_step(state: HeartState, ts: float, bpm: float, median: float | None, p: HeartParams,
               baseline_ready: bool = True) -> tuple[HeartState, dict | None]:
    """One reading -> (new state, fire | None). Pure: no clocks, no I/O.

    fire = {"bpm", "median_bpm", "delta_bpm", "delta_pct", "above_s", "threshold_bpm"}."""
    if median is None or not baseline_ready or median <= 0 or not math.isfinite(bpm):
        return replace(state, above_since=None, last_ts=ts), None
    above = bpm >= p.threshold(median)
    gap = state.last_ts is not None and ts - state.last_ts > MAX_GAP_S
    if not above:
        return replace(state, armed=True, above_since=None, last_ts=ts), None      # back below: re-arm
    since = ts if (state.above_since is None or gap) else state.above_since
    st = replace(state, above_since=since, last_ts=ts)
    above_s = ts - since
    cooled = state.last_fire is None or ts - state.last_fire >= p.cooldown_s
    if st.armed and above_s >= p.sustain_s and cooled:
        fire = {"bpm": round(bpm, 1), "median_bpm": round(median, 1), "delta_bpm": round(bpm - median, 1),
                "delta_pct": round((bpm - median) / median * 100, 1), "above_s": round(above_s, 1),
                "threshold_bpm": round(p.threshold(median), 1)}
        return replace(st, armed=False, last_fire=ts), fire
    return st, None


def rule_get(rule, key, default=None):
    """Rules may be spidey.rules.Rule objects (if_) or plain dicts (if)."""
    if isinstance(rule, dict):
        return rule.get("if" if key == "if_" else key, default)
    return getattr(rule, key, default)


class HeartTriggers:
    """Per-trigger heart state keyed by rule id, for the CLI and tests: `evaluate(rules, sample)` -> fires for
    enabled `source: "heart_rate"` rules (Rule objects or dicts). Thread-safe. The engine instead calls
    `rules.apply_heart` (Rule.check_heart keeps the same state on each Rule, via `step_rule`)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.states: dict[str, HeartState] = {}

    @staticmethod
    def step_rule(rule, sample: dict, state: HeartState) -> tuple[HeartState, dict | None] | None:
        """One rule, one sample -> (new state, fire dict | None); None if the sample has no ts/bpm.
        fire = {rule, trigger_name, pattern, dot, intensity, rgb, ts, why, reason, heart{bpm, median_bpm,
        delta_bpm, above_s}, sample_ts}."""
        ts, bpm = sample.get("ts"), sample.get("bpm_smooth", sample.get("bpm"))
        if ts is None or bpm is None:
            return None
        p = HeartParams.from_rule(rule_get(rule, "if_"), rule_get(rule, "cooldown_s"))
        st, fire = heart_step(state, float(ts), float(bpm), sample.get("median_bpm"), p,
                              bool(sample.get("baseline_ready", True)))
        if not fire:
            return st, None
        rid = rule_get(rule, "id") or rule_get(rule, "name") or "heart"
        name = rule_get(rule, "name") or rid
        why = (f"HR {fire['bpm']:.0f} bpm, {fire['delta_bpm']:+.0f} over your 2-min median "
               f"{fire['median_bpm']:.0f} for {fire['above_s']:.0f} s")
        return st, {"rule": rid, "trigger_name": name, "pattern": rule_get(rule, "feel") or "pulse",
                    "dot": rule_get(rule, "dot"), "intensity": rule_get(rule, "intensity"),
                    "rgb": rule_get(rule, "rgb"), "ts": float(ts), "why": why, "reason": f"{name}: {why}",
                    "heart": {k: fire[k] for k in ("bpm", "median_bpm", "delta_bpm", "above_s")},
                    "sample_ts": sample.get("ts")}

    def evaluate(self, rules, sample: dict) -> list[dict]:
        out = []
        with self._lock:
            for r in rules:
                if not rule_get(r, "enabled", True) or rule_get(r, "source") != "heart_rate":
                    continue
                rid = rule_get(r, "id") or rule_get(r, "name") or "heart"
                res = self.step_rule(r, sample, self.states.get(rid, HeartState()))
                if res is None:
                    return out
                self.states[rid], fire = res
                if fire:
                    out.append(fire)
        return out


def oura_card(status: dict | None, now: float | None = None) -> dict:
    """The `oura` device card for device.status from HeartRateSense.status() (None = heart not enabled).
    status: connected (open_oura, a reading in the last 10 s) | simulated (replay/fake running) | disconnected
    | off (heart not enabled; same convention as the radar card)."""
    base = {"id": "oura", "name": "Oura", "kind": "heart_rate"}
    if status is None:
        return {**base, "status": "off", "detail": "off (heart.enabled: false)",
                "backend": None, "last_bpm": None, "median_bpm": None, "since_last_s": None, "last_seen": None}
    now = time.time() if now is None else now
    since = status.get("since_last_s")
    fresh = since is not None and since < 10
    backend = status.get("backend")
    if backend == "open_oura":
        st = "connected" if fresh else "disconnected"
    else:
        st = "simulated" if status.get("connected") or fresh else "disconnected"
    bits = [backend]
    if status.get("last_bpm") is not None:
        bits.append(f"{status['last_bpm']:.0f} bpm")
    if status.get("median_bpm") is not None:
        bits.append(f"2-min median {status['median_bpm']:.0f}")
    if status.get("detail"):
        bits.append(str(status["detail"]))
    return {**base, "status": st, "detail": " · ".join(b for b in bits if b),
            "backend": backend, "last_bpm": status.get("last_bpm"), "median_bpm": status.get("median_bpm"),
            "since_last_s": since, "rate_hz": status.get("rate_hz"), "connected": bool(status.get("connected")),
            "last_seen": None if since is None else round(now - since, 3)}


def start_from_config(cfg: dict, on_sample) -> "HeartRateSense | None":
    """Engine hook: cfg `heart: {enabled, backend, ...opts}` -> a running HeartRateSense, or None. Never raises."""
    hc = (cfg or {}).get("heart") or {}
    if not hc.get("enabled"):
        return None
    opts = {k: v for k, v in hc.items() if k not in ("enabled", "backend")}
    try:
        hs = HeartRateSense(hc.get("backend", "fake"), on_sample=on_sample, **opts)
        print(f"heart: {hs.backend} sense started", flush=True)
        return hs
    except Exception as e:
        print(f"heart: could not start ({type(e).__name__}: {e}); continuing without heart rate", flush=True)
        return None


# =====================================================================================
# Rolling window (median + smoothing)
# =====================================================================================

class HeartWindow:
    """Rolling 2-min median and 5-s smoothing over (ts, bpm) readings, in data time."""

    def __init__(self, window_s: float = WINDOW_S, smooth_s: float = SMOOTH_S, min_baseline_s: float = MIN_BASELINE_S):
        self.window_s, self.smooth_s, self.min_baseline_s = window_s, smooth_s, min_baseline_s
        self.buf: deque = deque()

    def add(self, ts: float, bpm: float) -> dict:
        if self.buf and ts < self.buf[-1][0]:      # time went backwards (loop / restart): start over
            self.buf.clear()
        self.buf.append((ts, bpm))
        while self.buf and self.buf[0][0] < ts - self.window_s:
            self.buf.popleft()
        vals = [b for _, b in self.buf]
        recent = [b for t, b in self.buf if t >= ts - self.smooth_s]
        span = ts - self.buf[0][0]
        return {"median_bpm": round(statistics.median(vals), 1) if vals else None,
                "bpm_smooth": round(statistics.median(recent), 1) if recent else bpm,
                "baseline_s": round(span, 1), "baseline_ready": span >= self.min_baseline_s}

    @property
    def median(self) -> float | None:
        return round(statistics.median(b for _, b in self.buf), 1) if self.buf else None


# =====================================================================================
# Data sources
# =====================================================================================

def load_csv(path: str | Path) -> list[tuple[float, float]]:
    """CSV `ts_s,bpm` (a header row and blank/# lines are skipped) -> [(ts_s, bpm)] sorted by time."""
    rows = []
    with open(path, newline="") as f:
        for rec in csv.reader(f):
            if not rec or rec[0].strip().startswith("#"):
                continue
            try:
                rows.append((float(rec[0]), float(rec[1])))
            except (ValueError, IndexError):
                continue                           # header / junk
    rows.sort(key=lambda r: r[0])
    return rows


def fake_series(duration_s: float = 1e9, rest_bpm: float = 70.0, noise_bpm: float = 2.0, rate_hz: float = 1.0,
                spike_at_s: float | None = None, spike_bpm: float = 105.0, spike_s: float = 20.0,
                spike_every_s: float | None = None, seed: int | None = None):
    """Generator of (t_s, bpm): resting HR + noise, optional spikes with ~4 s ramps."""
    rng = random.Random(seed)
    dt, t = 1.0 / max(0.05, rate_hz), 0.0
    while t <= duration_s:
        level = rest_bpm + 1.5 * math.sin(t / 37.0)            # slow drift
        if spike_at_s is not None:
            rel = t - spike_at_s
            if spike_every_s and rel >= 0:
                rel %= spike_every_s
            ramp = 4.0
            if 0 <= rel < ramp:
                level += (spike_bpm - rest_bpm) * rel / ramp
            elif ramp <= rel < ramp + spike_s:
                level = spike_bpm
            elif ramp + spike_s <= rel < 2 * ramp + spike_s:
                level += (spike_bpm - rest_bpm) * (1 - (rel - ramp - spike_s) / ramp)
        yield t, round(level + rng.uniform(-noise_bpm, noise_bpm), 1)
        t += dt


def parse_live_hr_line(line: str) -> tuple[float, int | None] | None:
    """open_oura `live-hr` stdout line -> (bpm, ibi_ms). The CLI prints `  72 bpm (IBI 833 ms)` per beat."""
    m = LIVE_HR_RE.search(line or "")
    if not m:
        return None
    bpm = float(m.group(1))
    if not 25 <= bpm <= 240:
        return None
    return bpm, (int(m.group(2)) if m.group(2) else None)


def read_env_file(path: Path) -> dict:
    """Minimal KEY=VALUE reader for the repo-root .env (no shell expansion). Values never printed."""
    out = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k] = v
    except OSError:
        pass
    return out


def oura_settings(opts: dict, env_file: Path | None = None) -> dict:
    """Resolve open_oura settings: explicit opts > process env > repo-root .env. Returns
    {bin, key_file, key_hex (secret), name, address, db, chunk_s}; missing entries are None."""
    file_env = read_env_file(env_file or (REPO / ".env"))

    def pick(opt, env):
        v = opts.get(opt)
        if v in (None, ""):
            v = os.environ.get(env) or file_env.get(env)
        return v or None

    bin_ = pick("bin", "OURA_BIN") or shutil.which("oura")
    # name: unset = the CLI default ("Oura"); an explicit empty value = `--name ""` (match any device name)
    if opts.get("name") is not None:
        name = opts["name"]
    elif "OURA_NAME" in os.environ:
        name = os.environ["OURA_NAME"]
    else:
        name = file_env.get("OURA_NAME")
    key_file = pick("key_file", "OURA_KEY_FILE")
    if key_file:
        kp = Path(key_file).expanduser()
        key_file = str(kp if kp.is_absolute() else REPO / kp)       # relative = relative to the repo root
    return {"bin": str(Path(bin_).expanduser()) if bin_ else None, "key_file": key_file,
            "key_hex": pick("auth_key", "OURA_AUTH_KEY"), "name": name,
            "address": pick("address", "OURA_ADDRESS"),
            "db": str(opts.get("db") or (REPO / "logs" / "oura_live.db")),
            "chunk_s": int(opts.get("chunk_s") or OURA_CHUNK_S)}


def oura_command(s: dict, key_file: str) -> list[str]:
    cmd = [s["bin"], "--key-file", key_file, "--db", s["db"]]
    if s.get("name") is not None:
        cmd += ["--name", s["name"]]
    if s.get("address"):
        cmd += ["--address", s["address"]]
    return cmd + ["live-hr", "--seconds", str(int(s["chunk_s"]))]


def write_temp_key(key_hex: str) -> tuple[str, str]:
    """OURA_AUTH_KEY -> a 0600 key file in a private 0700 temp dir. Returns (dir, path). Raises ValueError
    (without echoing the value) if it isn't 32 hex chars."""
    k = (key_hex or "").strip()
    if not HEX32_RE.match(k):
        raise ValueError(f"OURA_AUTH_KEY must be 32 hex characters (16 bytes); got {len(k)} characters")
    d = tempfile.mkdtemp(prefix="spidey-oura-")
    os.chmod(d, 0o700)
    p = os.path.join(d, "oura.key")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k.lower() + "\n")
    return d, p


# =====================================================================================
# The sense
# =====================================================================================

class HeartRateSense:
    def __init__(self, backend: str = "replay", on_sample=None, **opts):
        self.backend = backend
        self.on_sample = on_sample
        self.opts = opts
        self.window = HeartWindow(float(opts.get("window_s", WINDOW_S)), float(opts.get("smooth_s", SMOOTH_S)),
                                  float(opts.get("min_baseline_s", MIN_BASELINE_S)))
        self.speed = max(0.01, float(opts.get("speed", 1.0) or 1.0))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._connected = False
        self._detail = "starting"
        self._last: dict | None = None
        self._last_rx: float | None = None          # wall clock of the last reading
        self._rx_times: deque = deque()
        self._n = 0
        self._proc: subprocess.Popen | None = None
        self._tmpdir: str | None = None
        self.done = threading.Event()               # set when a non-looping replay / bounded fake finishes
        self._thread = threading.Thread(target=self._thread_main, name=f"heart-{backend}", daemon=True)
        try:
            self._thread.start()
        except Exception as e:                      # never raise from __init__
            print(f"[heart] could not start thread: {e!r}", flush=True)

    # ---- public ---------------------------------------------------------------------------
    def status(self) -> dict:
        now = time.time()
        with self._lock:
            while self._rx_times and now - self._rx_times[0] > 10.0:
                self._rx_times.popleft()
            rate = len(self._rx_times) / 10.0 if self._rx_times else 0.0
            last = self._last or {}
            return {"connected": self._connected, "backend": self.backend,
                    "last_bpm": last.get("bpm"), "median_bpm": last.get("median_bpm"),
                    "bpm_smooth": last.get("bpm_smooth"), "baseline_ready": last.get("baseline_ready", False),
                    "rate_hz": round(rate, 2), "samples": self._n,
                    "since_last_s": None if self._last_rx is None else round(now - self._last_rx, 2),
                    "last_ts": last.get("ts"), "detail": self._detail}

    def close(self) -> None:
        self._stop.set()
        self._kill_proc()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
        self._cleanup_key()

    # ---- plumbing ------------------------------------------------------------------------
    def _thread_main(self):
        try:
            if self.backend == "replay":
                self._run_replay()
            elif self.backend == "fake":
                self._run_fake()
            elif self.backend == "open_oura":
                self._run_open_oura()
            else:
                self._set(False, f"unknown backend {self.backend!r} (replay | fake | open_oura)")
                print(f"[heart] {self._detail}", flush=True)
        except Exception as e:                      # never let the thread die loudly
            self._set(False, f"{type(e).__name__}: {e}")
            print(f"[heart] stopped: {self._detail}", flush=True)
        finally:
            self.done.set()

    def _set(self, connected: bool | None = None, detail: str | None = None):
        with self._lock:
            if connected is not None:
                self._connected = connected
            if detail is not None:
                self._detail = detail

    def _emit(self, ts: float, bpm: float, ibi_ms: int | None = None):
        w = self.window.add(ts, bpm)
        sample = {"ts": ts, "bpm": round(bpm, 1), "ibi_ms": ibi_ms, "backend": self.backend, **w}
        now = time.time()
        with self._lock:
            self._last, self._last_rx = sample, now
            self._rx_times.append(now)
            self._n += 1
        if self.on_sample:
            try:
                self.on_sample(dict(sample))
            except Exception as e:
                self._errors = getattr(self, "_errors", 0) + 1
                if self._errors == 1 or self._errors % 100 == 0:      # don't flood the console at 1 Hz
                    print(f"[heart] on_sample error #{self._errors}: {e!r}", flush=True)

    def _play(self, series, detail: str):
        """Emit (t_s, bpm) pairs paced at real time / speed. Data ts = start + t_s (wall time when speed=1)."""
        t_wall0, origin, first = time.time(), None, None
        self._set(True, detail)
        for t_s, bpm in series:
            if self._stop.is_set():
                return
            if first is None:
                first = t_s
                origin = float(self.opts.get("t0", t_wall0))
            due = t_wall0 + (t_s - first) / self.speed
            delay = due - time.time()
            if delay > 0 and self._stop.wait(delay):
                return
            self._emit(origin + (t_s - first), bpm)

    # ---- replay -----------------------------------------------------------------------------
    def _run_replay(self):
        path = self.opts.get("file") or self.opts.get("path")
        if not path:
            self._set(False, "replay: no file= given")
            return
        p = Path(path).expanduser()
        if not p.is_absolute() and not p.exists():
            p = REPO / "backend" / path
        rows = load_csv(p)
        if not rows:
            self._set(False, f"replay: no rows in {p}")
            return
        loop = bool(self.opts.get("loop", False))
        span = rows[-1][0] - rows[0][0] + (rows[1][0] - rows[0][0] if len(rows) > 1 else 1.0)

        def series():
            k = 0
            while True:
                for t, b in rows:
                    yield t + k * span, b
                k += 1
                if not loop:
                    return

        self._play(series(), f"replay {p.name} x{self.speed:g}" + (" (loop)" if loop else ""))
        if not self._stop.is_set():
            self._set(False, f"replay {p.name} finished")

    # ---- fake -------------------------------------------------------------------------------
    def _run_fake(self):
        o = self.opts
        gen = fake_series(duration_s=float(o.get("duration_s", 1e9)), rest_bpm=float(o.get("rest_bpm", 70)),
                          noise_bpm=float(o.get("noise_bpm", 2)), rate_hz=float(o.get("rate_hz", 1.0)),
                          spike_at_s=o.get("spike_at_s"), spike_bpm=float(o.get("spike_bpm", 105)),
                          spike_s=float(o.get("spike_s", 20)), spike_every_s=o.get("spike_every_s"),
                          seed=o.get("seed"))
        spike = f", spike {o.get('spike_bpm', 105)} bpm at {o['spike_at_s']} s" if o.get("spike_at_s") is not None else ""
        self._play(gen, f"fake {o.get('rest_bpm', 70)} bpm{spike} x{self.speed:g}")
        if not self._stop.is_set():
            self._set(False, "fake series finished")

    # ---- open_oura (subprocess; Bluetooth via the CLI) --------------------------------------
    def _run_open_oura(self):
        s = oura_settings(self.opts, Path(self.opts["env_file"]) if self.opts.get("env_file") else None)
        if not s["bin"] or not (os.path.exists(s["bin"]) or shutil.which(s["bin"])):
            self._set(False, "open_oura: `oura` binary not found (build open_oura, set OURA_BIN in .env)")
            print(f"[heart] {self._detail}", flush=True)
            return
        key_file = s["key_file"]
        if key_file and not os.path.exists(key_file):
            self._set(False, f"open_oura: OURA_KEY_FILE {key_file} does not exist (run `oura pair` first)")
            print(f"[heart] {self._detail}", flush=True)
            return
        if not key_file:
            if not s["key_hex"]:
                self._set(False, "open_oura: no OURA_KEY_FILE or OURA_AUTH_KEY in .env (see docs/oura.md)")
                print(f"[heart] {self._detail}", flush=True)
                return
            try:
                self._tmpdir, key_file = write_temp_key(s["key_hex"])
            except ValueError as e:
                self._set(False, f"open_oura: {e}")
                print(f"[heart] {self._detail}", flush=True)
                return
        Path(s["db"]).parent.mkdir(parents=True, exist_ok=True)
        cmd = oura_command(s, key_file)
        shown = " ".join(c if c != key_file else "<key-file>" for c in cmd)
        attempt = 0
        while not self._stop.is_set():
            got = self._run_oura_once(cmd, shown)
            if self._stop.is_set():
                break
            if got:
                attempt = 0                          # a chunk that produced beats ended normally: restart now
                delay = 0.5
            else:
                delay = OURA_BACKOFF_S[min(attempt, len(OURA_BACKOFF_S) - 1)]
                attempt += 1
            self._set(False, f"{self._detail} · restarting in {delay:g} s")
            if self._stop.wait(delay):
                break
        self._set(False, "open_oura stopped")

    def _run_oura_once(self, cmd: list[str], shown: str) -> int:
        print(f"[heart] starting: {shown}", flush=True)
        self._set(False, "open_oura: connecting to the ring…")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, bufsize=1, start_new_session=True)
        except OSError as e:
            self._set(False, f"open_oura: could not start ({type(e).__name__}: {e})")
            print(f"[heart] {self._detail}", flush=True)
            return 0
        self._proc = proc
        got, last_line = 0, ""
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                line = line.rstrip()
                hit = parse_live_hr_line(line)
                if hit:
                    got += 1
                    if got == 1:
                        self._set(True, "open_oura: live")
                        print("[heart] open_oura: first beat", flush=True)
                    self._emit(time.time(), hit[0], hit[1])
                elif line.strip():
                    last_line = line.strip()[:200]
                    if line.lstrip().startswith("Streaming"):
                        self._set(True, "open_oura: connected, waiting for beats (ring worn?)")
                    print(f"[heart] oura: {last_line}", flush=True)
        finally:
            rc = proc.wait() if not self._stop.is_set() else self._kill_proc()
            self._proc = None
        why = "ended" if rc == 0 else f"exited {rc}"
        if got == 0 and "No beats" in last_line:
            why += ": no beats (is the ring worn?)"
        elif got == 0 and last_line:
            why += f": {last_line}"
        self._set(False, f"open_oura {why} ({got} beats)")
        print(f"[heart] {self._detail}", flush=True)
        return got

    def _kill_proc(self):
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return proc.returncode if proc else None
        try:
            os.killpg(proc.pid, signal.SIGINT)      # the CLI has no Ctrl-C handler; SIGINT ends it
            return proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                return proc.wait(timeout=2)
            except Exception:
                return None

    def _cleanup_key(self):
        d, self._tmpdir = self._tmpdir, None
        if d:
            shutil.rmtree(d, ignore_errors=True)


# =====================================================================================
# CLI
# =====================================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description="Print heart-rate samples and heart_rate trigger fires.")
    ap.add_argument("--backend", default="replay", choices=BACKENDS)
    ap.add_argument("--file", default=str(REPO / "backend" / "tests" / "data" / "hr_spike.csv"), help="replay CSV")
    ap.add_argument("--speed", type=float, default=1.0, help="replay/fake speed-up")
    ap.add_argument("--loop", action="store_true", help="replay: loop the file")
    ap.add_argument("--spike-at", type=float, default=None, help="fake: spike at this many seconds")
    ap.add_argument("--min-delta-bpm", type=float, default=15)
    ap.add_argument("--min-delta-pct", type=float, default=20)
    ap.add_argument("--sustain", type=float, default=8)
    ap.add_argument("--cooldown", type=float, default=60)
    ap.add_argument("--every", type=float, default=1.0, help="seconds (wall) between printed sample lines")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until done / Ctrl-C)")
    args = ap.parse_args(argv)

    rule = {"id": "heart_racing", "name": "Heart racing", "source": "heart_rate", "feel": "pulse",
            "if": {"min_delta_bpm": args.min_delta_bpm, "min_delta_pct": args.min_delta_pct,
                   "sustain_s": args.sustain}, "cooldown_s": args.cooldown}
    trig, last_line, t0 = HeartTriggers(), [0.0], time.time()
    d0 = [None]

    def on_sample(s):
        d0[0] = s["ts"] if d0[0] is None else d0[0]
        rel = s["ts"] - d0[0]
        now = time.time()
        if now - last_line[0] >= args.every:
            last_line[0] = now
            med = "  …" if s["median_bpm"] is None else f"{s['median_bpm']:5.1f}"
            print(f"{rel:7.1f}s  bpm {s['bpm']:5.1f}  smooth {s['bpm_smooth']:5.1f}  median {med}"
                  f"{'' if s['baseline_ready'] else '  (warming up)'}", flush=True)
        for f in trig.evaluate([rule], s):
            h = f["heart"]
            print(f"{rel:7.1f}s  *** FIRE {f['trigger_name']}: HR {h['bpm']:.0f} (median {h['median_bpm']:.0f}, "
                  f"{h['delta_bpm']:+.0f}) · {h['above_s']:.0f} s -> {f['pattern']}", flush=True)

    opts = {"file": args.file, "speed": args.speed, "loop": args.loop}
    if args.spike_at is not None:
        opts["spike_at_s"] = args.spike_at
    hs = HeartRateSense(args.backend, on_sample=on_sample, **opts)
    try:
        next_status = time.time() + 5
        while not hs.done.is_set() and (not args.seconds or time.time() - t0 < args.seconds):
            time.sleep(0.1)
            if time.time() >= next_status:
                next_status += 5
                st = hs.status()
                print(f"[status] connected={st['connected']} last={st['last_bpm']} median={st['median_bpm']} "
                      f"rate={st['rate_hz']} Hz since_last={st['since_last_s']} s · {st['detail']}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        hs.close()


if __name__ == "__main__":
    main()

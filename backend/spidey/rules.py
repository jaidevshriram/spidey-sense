"""Detection -> which pattern to feel. Rules are data, so they can live in config.yaml or triggers.json.

A rule's JSON form (the phone app's "sense", see docs/protocol.md):
    {id, name, enabled, kind, describe, source, when, if, feel, intensity, rgb, dot, cooldown_s,
     gaze_mode, notice_memory_s, temporal, trend}

kind "builtin": matched on the detector's hazard/urgency/seen. kind "custom": `describe` is plain English that
the VLM judges in the same call; matched on detection.triggers[id], after `TriggerMemory.annotate` added the
real-gaze and trend signals. source "radar": matched on RadarSense targets (`check_radar`).
"""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field

from . import heart as H
from .types import Detection, Frame, HapticEvent

# Sources a rule can listen to. "neon" (vision detections) and "radar" (RadarSense targets) are wired in.
VISION_SOURCES = ("neon", "vision")
SOURCES = ("neon", "radar", "heart_rate", "mac_metric", "manual")
GAZE_MODES = ("unseen", "looking_at", "any")
LOOKING_AT_S = 0.6          # looking_at: gaze inside the box within this many seconds


@dataclass
class Rule:
    name: str
    feel: str                         # pattern name in spidey_haptics.PATTERNS
    when: list[str] | str = "any"     # hazard kinds this rule covers, or "any"
    if_: dict = field(default_factory=dict)  # {unseen, min_urgency, max_urgency, max_distance_m, min_speed_mps, ...}
    cooldown_s: float = 3.0
    dot: int | None = None            # 1-based dot, None = all
    id: str = ""                      # stable id for the phone app; defaults to a slug of name
    enabled: bool = True
    source: str = "neon"              # neon | radar | heart_rate | mac_metric | manual
    kind: str = "builtin"             # builtin | custom
    describe: str = ""                # custom: what to watch for, in plain English
    intensity: float | None = None    # per-trigger vibration strength 0..1
    rgb: list | None = None           # per-trigger LED colour
    gaze_mode: str | None = None      # unseen | looking_at | any; None = derived from if.unseen / if.seen
    notice_memory_s: float = 3.0      # unseen: stay quiet this long after the gaze was last on the thing
    temporal: bool = False            # custom: judged over the last 3 frames, fires on a consistent trend
    trend: dict = field(default_factory=dict)   # temporal: {ticks: 3, sensitivity: low|medium|high}
    _last: float = field(default=-1e9, repr=False)
    _heart: H.HeartState | None = field(default=None, repr=False)   # heart_rate: armed / sustain state

    def __post_init__(self):
        if not self.id:
            self.id = slug(self.name)
        if self.gaze_mode not in GAZE_MODES:
            self.gaze_mode = None

    @property
    def effective_gaze_mode(self) -> str:
        """Back-compat: no gaze_mode -> if.unseen => unseen, if.seen => looking_at, else any."""
        if self.gaze_mode:
            return self.gaze_mode
        if self.if_.get("unseen"):
            return "unseen"
        if self.if_.get("seen"):
            return "looking_at"
        return "any"

    # ---- camera ---------------------------------------------------------------------
    def _gaze_ok(self, seen_by_haiku: bool, t: dict | None) -> bool:
        mode = self.effective_gaze_mode
        if mode == "any":
            return True
        if t is not None and "looked_at_s_ago" in t:     # custom trigger with a box: real gaze history
            ago = t["looked_at_s_ago"]
            if mode == "looking_at":
                return ago is not None and ago <= LOOKING_AT_S
            return ago is None or ago > self.notice_memory_s
        # built-ins (and custom triggers without a box): Haiku's red-dot judgement
        return seen_by_haiku if mode == "looking_at" else not seen_by_haiku

    def matches(self, d: Detection) -> bool:
        return self.explain(d)[0]

    def explain(self, d: Detection) -> tuple[bool, str]:
        """(matches, why): the same decision as before, plus a plain-English reason when it doesn't match.
        Cooldown, staleness and the live-gaze check are applied in `decide`."""
        if not self.enabled:
            return False, "disabled"
        if self.source not in VISION_SOURCES:
            # radar: check_radar(); heart_rate: check_heart(). TODO(stub): mac_metric / manual have no input stream yet;
            # their conditions (hr_band_bpm, value_gt, ...) are stored and round-tripped only.
            return False, f"not a camera trigger (source {self.source})"
        c = self.if_
        if self.kind == "custom":
            if self.id not in d.triggers:
                return False, "not in Haiku's reply"
            t = d.triggers.get(self.id) or {}
            if self.temporal:
                # the trend layer decides (TriggerMemory), not one Haiku verdict
                if not t.get("trend_fire"):
                    return False, "trend: " + (t.get("trend_why") or "no fire")
                ok, why = self._gaze_why(bool(t.get("seen_by_user")), t)
                return ok, ("trend fired, but " + why) if not ok else "trend fired"
            if not t.get("match"):
                return False, "no match"
            ok, why = self._gaze_why(bool(t.get("seen_by_user")), t)
            if not ok:
                return False, "match, but " + why
            urgency = float(t.get("urgency") or 0.0)
        else:
            if d.hazard is None:
                return False, "no hazard"
            if self.when != "any" and d.hazard not in self.when:
                return False, f"hazard {d.hazard!r} not in {self.when}"
            ok, why = self._gaze_why(d.seen_by_user, None)
            if not ok:
                return False, f"hazard {d.hazard!r}, but " + why
            urgency = d.urgency
        lo, hi = c.get("min_urgency", 0.0), c.get("max_urgency", 1.0)
        if urgency < lo:
            return False, f"urgency {urgency:.2f} < min {lo}"
        if urgency > hi:
            return False, f"urgency {urgency:.2f} > max {hi}"
        return True, "match"

    def _gaze_why(self, seen_by_haiku: bool, t: dict | None) -> tuple[bool, str]:
        """_gaze_ok with the reason when it fails."""
        ok = self._gaze_ok(seen_by_haiku, t)
        if ok:
            return True, ""
        mode = self.effective_gaze_mode
        if t is not None and "looked_at_s_ago" in t:
            ago = t["looked_at_s_ago"]
            if mode == "looking_at":
                last = "gaze never in the box since it appeared" if ago is None else f"last in box {ago:.1f} s ago"
                return False, f"looking_at needs gaze in box \u2264{LOOKING_AT_S} s; {last}"
            return False, f"unseen: looked at it {ago:.1f} s ago (notice memory {self.notice_memory_s:g} s)"
        if mode == "looking_at":
            return False, "looking_at: Haiku says not seen (no box for the gaze check)"
        return False, "unseen: Haiku says the red dot is on it (no box for the gaze check)"

    def decide(self, d: Detection, now: float, ctx: dict | None = None) -> tuple[HapticEvent | None, dict]:
        """Rule decision record for one detection: (event or None, {id, name, fired, matched, why, gaze_mode}).
        ctx (engine): max_result_age_s (don't fire on results older than this, measured from frame capture) and
        gaze_now ((ts, x, y) newest gaze sample, 0..1): looking_at triggers with a box also need the newest gaze
        inside the box at decision time. Without ctx it behaves like check()."""
        ctx = ctx or {}
        ok, why = self.explain(d)
        t = d.triggers.get(self.id) if self.kind == "custom" else None
        matched = bool(d.hazard) if self.kind != "custom" else bool((t or {}).get("match") or (t or {}).get("trend_fire"))
        if ok:
            # per trigger (if.max_age_s / if.keep_on_view_change): static things like obstacles are still there
            # after 3 s or a head turn; fast things (a fist) are not
            max_age = self.if_.get("max_age_s") or ctx.get("max_result_age_s")
            frame_ts = d.raw.get("frame_ts")
            if max_age and frame_ts and now - frame_ts > max_age:
                ok, why = False, f"stale: result {now - frame_ts:.1f} s old (max {max_age:g} s)"
            vc = ctx.get("view_changed_ts")
            if ok and vc and frame_ts and frame_ts < vc and not self.temporal and not self.if_.get("keep_on_view_change"):
                ok, why = False, f"stale: the view changed {now - vc:.1f} s ago, after this frame"
        if ok and "gaze_now" in ctx and self.effective_gaze_mode == "looking_at" and t is not None \
                and not self.temporal and t.get("bbox"):
            g = ctx["gaze_now"]
            if g is None:
                ok, why = False, "gaze left the box (no gaze sample now)"
            elif not in_box(g[1], g[2], t["bbox"]):
                ok, why = False, f"gaze left the box (gaze now at {g[1]:.2f},{g[2]:.2f})"
        if ok and now - self._last < self.cooldown_s:
            ok, why = False, f"cooldown {self.cooldown_s - (now - self._last):.1f} s left"
        rec = {"id": self.id, "name": self.name, "fired": ok, "matched": matched, "why": "fired" if ok else why,
               "gaze_mode": self.effective_gaze_mode}
        if t and t.get("reason"):
            rec["haiku_reason"] = t["reason"]
        if not ok:
            return None, rec
        return self._fire(d, now), rec

    def check(self, d: Detection, now: float) -> HapticEvent | None:
        return self.decide(d, now)[0]

    def _fire(self, d: Detection, now: float) -> HapticEvent:
        self._last = now
        extra = {"gaze_mode": self.effective_gaze_mode}
        if self.kind == "custom":
            t = d.triggers.get(self.id) or {}
            why = t.get("reason") or d.reason
            text = f"{self.name}: {why}"
            if "looked_at_s_ago" in t:
                extra["looked_at_s_ago"] = t["looked_at_s_ago"]
            box = t.get("subject_bbox") if self.temporal else t.get("bbox")
            if box:
                extra["bbox"] = box
            if self.temporal and t.get("target_bbox"):
                extra["target_bbox"] = t["target_bbox"]
            if self.temporal and t.get("trend"):
                extra["trend"] = t["trend"]
        else:
            why = d.reason
            text = f"{self.name}: {d.hazard} {d.where or ''} {'seen' if d.seen_by_user else 'unseen'} u={d.urgency:.2f}"
        return HapticEvent(self.feel, self.dot, text.replace("  ", " "), now, self.id,
                           trigger_name=self.name, why=why, intensity=self.intensity, rgb=self.rgb,
                           sensor="camera", extra=extra)

    # ---- heart rate -----------------------------------------------------------------
    def check_heart(self, sample: dict, now: float) -> HapticEvent | None:
        """HeartRateSense sample {ts, bpm, bpm_smooth, median_bpm, baseline_ready} -> event. Fires when the
        smoothed bpm is >= if.min_delta_bpm (15) or >= if.min_delta_pct % (20) over the rolling 2-min median for
        >= if.sustain_s (8), once per excursion (re-arms below the threshold), not within cooldown_s.
        The logic is spidey.heart.heart_step (pure, unit-tested); this keeps its state on the rule."""
        if not self.enabled or self.source != "heart_rate":
            return None
        f = H.HeartTriggers.step_rule(self, sample, self._heart or H.HeartState())
        if f is None:
            return None
        self._heart, fire = f
        if fire is None:
            return None
        self._last = now
        extra = {"heart": fire["heart"]}
        lat = (now - fire["sample_ts"]) * 1000 if fire.get("sample_ts") else None
        if lat is not None and 0 <= lat < 60_000:
            extra["latency_ms"] = round(lat, 1)
        return HapticEvent(self.feel, self.dot, fire["reason"], now, self.id, trigger_name=self.name,
                           why=fire["why"], intensity=self.intensity, rgb=self.rgb, sensor="heart", extra=extra)

    # ---- radar ----------------------------------------------------------------------
    def check_radar(self, target: dict, now: float) -> HapticEvent | None:
        """RadarSense target {state, distance_m, speed_mps, angle_deg, side, ts, confirmed} -> event.
        Fires on a confirmed approaching/too_close target within if.max_distance_m (default 2.0) moving at
        >= if.min_speed_mps (default 0.3). Radar targets are unseen by definition."""
        if not self.enabled or self.source != "radar":
            return None
        c = self.if_
        try:
            dist, speed = float(target.get("distance_m")), float(target.get("speed_mps") or 0.0)
        except (TypeError, ValueError):
            return None
        if not (target.get("confirmed") and target.get("state") in ("approaching", "too_close")):
            return None
        if dist > float(c.get("max_distance_m", 2.0)) or speed < float(c.get("min_speed_mps", 0.3)):
            return None
        if "min_urgency" in c and target.get("urgency") is not None \
                and float(target["urgency"]) < float(c["min_urgency"]):
            return None
        if now - self._last < self.cooldown_s:
            return None
        self._last = now
        side = target.get("side") or "center"
        where = {"left": "behind-left", "right": "behind-right"}.get(side, "behind")
        verb = "approaching" if target.get("state") == "approaching" else "very close"
        why = f"Person {verb} from {where}, {dist:.1f} m at {speed:.1f} m/s"
        radar = {k: target.get(k) for k in ("distance_m", "speed_mps", "angle_deg", "side")}
        return HapticEvent(self.feel, self.dot, f"{self.name}: {why}", now, self.id, trigger_name=self.name,
                           why=why, intensity=self.intensity, rgb=self.rgb, sensor="radar",
                           extra={"gaze_mode": "unseen", "radar": radar})

    def to_dict(self) -> dict:
        d = {"id": self.id, "name": self.name, "enabled": self.enabled, "kind": self.kind,
             "describe": self.describe, "source": self.source, "when": self.when, "if": dict(self.if_),
             "feel": self.feel, "intensity": self.intensity, "rgb": self.rgb, "dot": self.dot,
             "cooldown_s": self.cooldown_s, "gaze_mode": self.effective_gaze_mode,
             "notice_memory_s": self.notice_memory_s, "temporal": self.temporal}
        if self.temporal:
            d["trend"] = trend_params(self.trend)
        return d


def trend_params(trend: dict | None) -> dict:
    trend = trend or {}
    sens = trend.get("sensitivity", "medium")
    return {"ticks": max(1, int(trend.get("ticks", 3) or 3)),
            "sensitivity": sens if sens in SENSITIVITY else "medium"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "rule"


def rule_from_dict(c: dict) -> Rule:
    """Config/triggers.json/phone-app dict -> Rule. Accepts "if" or "if_" for the condition."""
    nm = c.get("notice_memory_s")
    return Rule(name=c.get("name") or c.get("id") or "rule", feel=c.get("feel", "urgent"),
                when=c.get("when", "any") or "any", if_=dict(c.get("if", c.get("if_")) or {}),
                cooldown_s=float(c.get("cooldown_s", 3.0)), dot=c.get("dot"),
                id=c.get("id") or "", enabled=bool(c.get("enabled", True)),
                source=c.get("source") or "neon",
                kind=c.get("kind") or ("custom" if c.get("describe") else "builtin"),
                describe=c.get("describe") or "",
                intensity=None if c.get("intensity") is None else max(0.0, min(1.0, float(c["intensity"]))),
                rgb=list(c["rgb"])[:3] if c.get("rgb") else None,
                gaze_mode=c.get("gaze_mode"),
                notice_memory_s=3.0 if nm is None else max(0.0, float(nm)),
                temporal=bool(c.get("temporal", False)), trend=dict(c.get("trend") or {}))


# Something dangerous you haven't looked at -> urgent buzz; something milder -> ambient hum.
DEFAULT_RULES = [Rule("blindspot", feel="urgent", if_={"unseen": True, "min_urgency": 0.6}),
                 Rule("heads_up", feel="ambient", if_={"unseen": True, "max_urgency": 0.59}, cooldown_s=6)]


def custom_triggers(rules: list[Rule]) -> list[tuple[str, str, bool]]:
    """(id, description, temporal) of enabled custom camera triggers, for the VLM prompt."""
    return [(r.id, r.describe, r.temporal) for r in rules
            if r.enabled and r.kind == "custom" and r.describe and r.source in VISION_SOURCES]


def load_rules(cfgs: list[dict] | None) -> list[Rule]:
    if not cfgs:
        return [rule_from_dict(r.to_dict()) for r in DEFAULT_RULES]
    rules, seen = [], set()
    for c in cfgs:
        r = rule_from_dict(c)
        base, n = r.id, 2
        while r.id in seen:  # keep ids unique
            r.id, n = f"{base}_{n}", n + 1
        seen.add(r.id)
        rules.append(r)
    return rules


def apply(rules: list[Rule], d: Detection, now: float) -> list[HapticEvent]:
    return [e for r in rules if r.enabled and (e := r.check(d, now))]


def apply_explained(rules: list[Rule], d: Detection, now: float,
                    ctx: dict | None = None) -> tuple[list[HapticEvent], list[dict]]:
    """apply() + a decision record per enabled camera trigger ({id, name, fired, matched, why, gaze_mode})."""
    events, decisions = [], []
    for r in rules:
        if not r.enabled:
            continue
        e, rec = r.decide(d, now, ctx)
        if e is not None:
            events.append(e)
        if r.source in VISION_SOURCES:
            decisions.append(rec)
    return events, decisions


def apply_radar(rules: list[Rule], target: dict, now: float) -> list[HapticEvent]:
    return [e for r in rules if r.enabled and r.source == "radar" and (e := r.check_radar(target, now))]


def apply_heart(rules: list[Rule], sample: dict, now: float) -> list[HapticEvent]:
    return [e for r in rules if r.enabled and r.source == "heart_rate" and (e := r.check_heart(sample, now))]


# =====================================================================================
# Temporal + gaze memory layer
# =====================================================================================
# Only relative, measurable quantities are remembered: gaze samples (0..1 of the scene frame), Haiku's boxes
# (0..1 of the frame it saw), the subject->target gap in subject body-heights, the subject's box area ratio
# ("loom") and Haiku's ordinal closer/same/farther. No metric distances from the camera.

NOTICE_WIN_S = 0.35      # gaze samples within this long before a frame are compared with its boxes
NOTICE_FRAC = 0.5        # ...and at least this share must be inside (a fixation, not a glance)
NO_GAZE_AFTER_S = 0.3   # newest gaze sample older than this (vs the newest frame) = no gaze now
GAZE_HISTORY_S = 5.0
BOX_MARGIN = 0.04            # gaze within 4% of the frame around a box counts as "on it"
MATCH_GAP_S = 2.0            # a trigger unmatched this long = the thing is gone; its memory resets
TREND_GAP_S = 4.0            # temporal: no tick for this long = restart the trend (same subject: arm state kept)
TRACK_FORGET_S = 10.0        # temporal: subject unseen this long = a new track (re-armed)
# min gap shrink per tick (body-heights) that counts as "moving toward", and the proximity gate
SENSITIVITY = {"low": 0.15, "medium": 0.07, "high": 0.03}
NEAR_GAP = {"low": 0.5, "medium": 1.0, "high": 2.0}
REARM_FACTOR = 1.3           # hysteresis: re-arm once the gap grew back 30% (+0.2 body-heights) past the firing gap


def valid_box(b) -> list[float] | None:
    """[x0, y0, x1, y1] in 0..1 (clamped), or None."""
    try:
        x0, y0, x1, y1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 < 1e-3 or y1 - y0 < 1e-3:
        return None
    return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def box_gap(subject, target, w: float, h: float) -> float:
    """Box-to-box distance in pixels / subject box height in pixels (0 = boxes touch or overlap)."""
    dx = max(target[0] - subject[2], subject[0] - target[2], 0.0) * w
    dy = max(target[1] - subject[3], subject[1] - target[3], 0.0) * h
    sh = max((subject[3] - subject[1]) * h, 1.0)
    return math.hypot(dx, dy) / sh


def in_box(x: float, y: float, b, m: float = BOX_MARGIN) -> bool:
    return b[0] - m <= x <= b[2] + m and b[1] - m <= y <= b[3] + m


@dataclass
class TriggerState:
    """Rolling memory of one custom trigger: when it matched, when the gaze was last on it, and (temporal)
    the per-tick track of the subject relative to the target."""
    match_since: float | None = None     # frame ts of the first match in the current streak
    last_match: float | None = None
    noticed_ts: float | None = None      # last gaze sample inside the box while matched
    ticks: deque = field(default_factory=lambda: deque(maxlen=12))   # temporal: per-tick dicts
    shrink_streak: int = 0
    closer_streak: int = 0
    armed: bool = True
    fired_gap: float | None = None

    def reset_track(self) -> None:
        self.ticks.clear()
        self.shrink_streak = self.closer_streak = 0
        self.armed, self.fired_gap = True, None


class TriggerMemory:
    """Feeds on every frame's gaze and every full Detection; writes into det.triggers[id]:
    looked_at_s_ago (camera custom triggers with a box), and for temporal triggers trend + trend_fire."""

    def __init__(self):
        self.gaze: deque = deque()              # (ts, x, y) normalized 0..1
        self.states: dict[str, TriggerState] = {}

    def add_frame(self, frame: Frame) -> None:
        self.last_frame_ts = frame.ts
        g = frame.gaze_px()
        if g is not None:
            h, w = frame.image.shape[:2]
            self.gaze.append((frame.ts, g[0] / w, g[1] / h))
        while self.gaze and self.gaze[0][0] < frame.ts - GAZE_HISTORY_S:
            self.gaze.popleft()

    def newest_gaze(self) -> tuple[float, float, float] | None:
        """(ts, x, y) of the newest gaze sample (0..1), for the decision-time "gaze left the box" check."""
        if not self.gaze:
            return None
        if self.gaze[-1][0] < getattr(self, "last_frame_ts", 0.0) - NO_GAZE_AFTER_S:
            return None                     # gaze lost (glasses off, tracking dropped): not looking at anything
        return self.gaze[-1]

    def fired(self, rule_id: str) -> None:
        """A temporal trigger actually fired: disarm it until the trend reverses (hysteresis)."""
        st = self.states.get(rule_id)
        if st is not None:
            st.armed = False
            st.fired_gap = st.ticks[-1].get("gap_s") if st.ticks else None
            st.shrink_streak = st.closer_streak = 0

    def annotate(self, det: Detection, rules: list[Rule], now: float, trace: dict | None = None) -> None:
        """trace (optional, tracing only): filled per trigger id with the gaze samples used for the box check
        and the trend state; it does not change any decision."""
        if det.raw.get("early"):
            return                      # early detections carry no custom triggers
        frame_ts = det.raw.get("frame_ts", now)
        w, h = (det.frame.image.shape[1], det.frame.image.shape[0]) if det.frame is not None else (1600, 1200)
        for r in rules:
            if not (r.enabled and r.kind == "custom" and r.source in VISION_SOURCES):
                continue
            t = det.triggers.setdefault(r.id, {"match": False})
            st = self.states.setdefault(r.id, TriggerState())
            if r.temporal:
                sb, tb = valid_box(t.get("subject_bbox")), valid_box(t.get("target_bbox"))
                t["subject_bbox"], t["target_bbox"] = sb, tb
                boxes = [b for b in (sb, tb) if b]
                present = sb is not None
            else:
                b = valid_box(t.get("bbox"))
                t["bbox"] = b
                boxes = [b] if b else []
                present = bool(t.get("match"))
            # -- match streak + gaze "noticed" memory --------------------------------------
            if present:
                if st.last_match is None or frame_ts - st.last_match > MATCH_GAP_S:
                    st.match_since, st.noticed_ts = frame_ts, None
                st.last_match = max(st.last_match or 0.0, frame_ts)   # out-of-order results never move it back
            elif st.last_match is not None and now - st.last_match > max(MATCH_GAP_S, r.notice_memory_s):
                st.match_since = st.last_match = st.noticed_ts = None
            if present and boxes:
                # gaze is only comparable with this frame's boxes around this frame's time (the view moves when
                # walking / turning); "noticed" = a fixation (>= NOTICE_FRAC of those samples inside), not a sweep.
                # Earlier calls' fixations carry over through st.noticed_ts.
                since = frame_ts - NOTICE_WIN_S
                win_s = [(ts, x, y) for ts, x, y in self.gaze if since <= ts <= frame_ts + 0.1]
                hits = [ts for ts, x, y in win_s if any(in_box(x, y, b) for b in boxes)]
                if hits and len(hits) >= max(2, NOTICE_FRAC * len(win_s)):
                    noticed = max(hits)
                    for ts, x, y in self.gaze:              # the fixation continues while the gaze stays inside
                        if ts <= frame_ts + 0.1:
                            continue
                        if not any(in_box(x, y, b) for b in boxes):
                            break
                        noticed = ts
                    st.noticed_ts = max(st.noticed_ts or 0.0, noticed)
                t["looked_at_s_ago"] = None if st.noticed_ts is None else round(max(0.0, now - st.noticed_ts), 2)
                if trace is not None:
                    win = [(ts, x, y) for ts, x, y in self.gaze if ts >= since]
                    step = max(1, len(win) // 40)        # <= ~40 samples in the record
                    trace[r.id] = {"boxes": boxes, "since": round(since - frame_ts, 2), "n_samples": len(win),
                                   "n_inside": len(hits), "noticed_ts": st.noticed_ts,
                                   "looked_at_s_ago": t["looked_at_s_ago"],
                                   "samples": [[round(ts - frame_ts, 3), round(x, 3), round(y, 3),
                                                any(in_box(x, y, b) for b in boxes)] for ts, x, y in win[::step]]}
            elif trace is not None:
                trace[r.id] = {"boxes": boxes, "present": present, "note": "no box: gaze not checked"
                               if present else "not matched: gaze not checked"}
            # -- temporal trend -----------------------------------------------------------------
            if r.temporal:
                self._trend(r, t, st, frame_ts, w, h)
                if trace is not None:
                    trace.setdefault(r.id, {})["trend_state"] = {
                        "trend": t.get("trend"), "why": t.get("trend_why"), "armed": st.armed,
                        "fired_gap": st.fired_gap, "shrink_streak": st.shrink_streak,
                        "ticks": [{"dt": round(k["ts"] - frame_ts, 2), "gap": k["gap"], "gap_s": k["gap_s"],
                                   "ordinal": k["ordinal"], "touching": k["touching"]} for k in st.ticks]}

    def _trend(self, r: Rule, t: dict, st: TriggerState, frame_ts: float, w: int, h: int) -> None:
        """Per tick: gap (body-heights, median of 3), loom, Haiku's ordinal. Fires (if armed) when
        touching, or near the target (gap <= NEAR_GAP) and either
          - the gap shrank over the last `ticks` ticks (no step growing by > eps, net shrink >= eps * (ticks-1)), or
          - Haiku said "closer" `ticks` times in a row, cross-checked against the gap (a growing gap resets it)."""
        p = trend_params(r.trend)
        n, eps, near = p["ticks"], SENSITIVITY[p["sensitivity"]], NEAR_GAP[p["sensitivity"]]
        sb, tb = t["subject_bbox"], t["target_bbox"]
        t["trend_fire"] = False
        if sb is None:
            t["trend_why"] = "no subject box from Haiku"
            return
        prev = st.ticks[-1] if st.ticks else None
        if prev is not None:
            same = iou(prev["sb"], sb) >= 0.2
            # small boxes jitter: also accept a centre within one subject height of the previous one
            pc = ((prev["sb"][0] + prev["sb"][2]) / 2 * w, (prev["sb"][1] + prev["sb"][3]) / 2 * h)
            nc = ((sb[0] + sb[2]) / 2 * w, (sb[1] + sb[3]) / 2 * h)
            same = same or math.dist(pc, nc) <= (sb[3] - sb[1]) * h
            if not same or frame_ts - prev["ts"] > TRACK_FORGET_S:
                st.reset_track()            # a different subject (big jump), or gone for long: re-arm
                prev = None
            elif frame_ts - prev["ts"] > TREND_GAP_S:
                st.ticks.clear()            # same subject after a gap: restart the trend, keep the arm state
                st.closer_streak = 0
                prev = None
        gap = box_gap(sb, tb, w, h) if tb else None
        raw_gaps = [k["gap"] for k in list(st.ticks)[-2:] if k["gap"] is not None] + ([gap] if gap is not None else [])
        gap_s = sorted(raw_gaps)[len(raw_gaps) // 2] if raw_gaps else None      # median of the last 3
        area = (sb[2] - sb[0]) * (sb[3] - sb[1])
        loom = round(area / prev["area"], 2) if prev and prev["area"] > 0 else 1.0
        ordinal = t.get("ordinal") if t.get("ordinal") in ("closer", "same", "farther") else None
        touching = bool(t.get("touching"))
        prev_gap_s = next((k["gap_s"] for k in reversed(st.ticks) if k["gap_s"] is not None), None)
        growing = gap_s is not None and prev_gap_s is not None and gap_s - prev_gap_s > eps
        if ordinal == "closer" and not growing:
            st.closer_streak += 1
        elif ordinal == "farther" or growing:
            st.closer_streak = 0
        st.ticks.append({"ts": frame_ts, "sb": sb, "gap": gap, "gap_s": gap_s, "area": area,
                         "ordinal": ordinal, "touching": touching})
        gaps = [k["gap_s"] for k in st.ticks if k["gap_s"] is not None][-n:]
        shrinking = (len(gaps) >= n and all(b - a <= eps for a, b in zip(gaps, gaps[1:]))
                     and gaps[0] - gaps[-1] >= eps * max(1, n - 1))
        st.shrink_streak = st.shrink_streak + 1 if shrinking else 0
        if not st.armed and gap_s is not None and st.fired_gap is not None \
                and gap_s >= st.fired_gap * REARM_FACTOR + 0.2 and not touching:
            st.armed = True
        is_near = gap_s is None or gap_s <= near
        # veto (unless touching): the latest tick says the subject is moving away, by Haiku's ordinal or by a gap
        # that grew by more than eps; noisy boxes can otherwise pass the shrink test while walking away
        veto = None if touching else ("Haiku says farther" if ordinal == "farther"
                                      else f"gap grew {prev_gap_s:.2f} -> {gap_s:.2f}" if growing else None)
        fire = touching or (is_near and (shrinking or st.closer_streak >= n) and veto is None)
        t["trend_fire"] = bool(st.armed and fire)
        t["trend_why"] = (f"veto: {veto}" if veto and is_near and (shrinking or st.closer_streak >= n)
                          else _trend_why(st, fire, touching, is_near, near, gap_s, gaps, n, shrinking))
        last = list(st.ticks)[-3:]
        t["trend"] = {"gap": [None if k["gap_s"] is None else round(k["gap_s"], 2) for k in last],
                      "ordinal": [k["ordinal"] for k in last], "touching": touching, "loom": loom,
                      "shrinking": shrinking, "closer_streak": st.closer_streak, "armed": st.armed,
                      **({"veto": veto} if veto else {})}


def _trend_why(st: TriggerState, fire: bool, touching: bool, is_near: bool, near: float, gap_s, gaps, n: int,
               shrinking: bool) -> str:
    """Plain-English trend state for the trace ("why not"); never used for the decision itself."""
    g = "?" if gap_s is None else f"{gap_s:.2f}"
    if fire and not st.armed:
        return (f"disarmed after firing (re-arms when the gap grows to "
                f"{(st.fired_gap or 0) * REARM_FACTOR + 0.2:.2f}); gap {g}")
    if fire:
        return "touching" if touching else f"near (gap {g} \u2264 {near}) and closing"
    if not is_near:
        return f"too far: gap {g} > {near} body-heights"
    if len(gaps) < n:
        return f"near, but only {len(gaps)}/{n} ticks of gap history"
    return (f"gap not shrinking (last gaps {[round(x, 2) for x in gaps]}), "
            f"Haiku 'closer' streak {st.closer_streak}/{n}")


"""Detection -> which pattern to feel. Rules are data, so they can live in config.yaml or rules.json.

A rule's JSON form (the phone app's "sense", see docs/protocol.md):
    {id, name, enabled, kind, describe, source, when, if, feel, intensity, rgb, dot, cooldown_s}

kind "builtin": matched on the detector's hazard/urgency/seen. kind "custom": `describe` is plain English that
the VLM judges in the same call; matched on detection.triggers[id].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .types import Detection, HapticEvent

# Sources a rule can listen to. Only "neon" (vision detections) is wired into the engine today.
VISION_SOURCES = ("neon", "vision")
SOURCES = ("neon", "heart_rate", "mac_metric", "manual")


@dataclass
class Rule:
    name: str
    feel: str                         # pattern name in spidey_haptics.PATTERNS
    when: list[str] | str = "any"     # hazard kinds this rule covers, or "any"
    if_: dict = field(default_factory=dict)  # {unseen: bool, min_urgency: float, max_urgency: float, ...}
    cooldown_s: float = 3.0
    dot: int | None = None            # 1-based dot, None = all
    id: str = ""                      # stable id for the phone app; defaults to a slug of name
    enabled: bool = True
    source: str = "neon"              # neon | heart_rate | mac_metric | manual
    kind: str = "builtin"             # builtin | custom
    describe: str = ""                # custom: what to watch for, in plain English
    intensity: float | None = None    # per-trigger vibration strength 0..1
    rgb: list | None = None           # per-trigger LED colour
    _last: float = field(default=-1e9, repr=False)

    def __post_init__(self):
        if not self.id:
            self.id = slug(self.name)

    def matches(self, d: Detection) -> bool:
        if not self.enabled:
            return False
        if self.source not in VISION_SOURCES:
            # TODO(stub): heart_rate / mac_metric / manual sources have no input stream yet.
            # Their conditions (hr_band_bpm, value_gt, ...) are stored and round-tripped only.
            return False
        c = self.if_
        if self.kind == "custom":
            t = d.triggers.get(self.id) or {}
            if not t.get("match"):
                return False
            seen, urgency = bool(t.get("seen_by_user")), float(t.get("urgency") or 0.0)
        else:
            if d.hazard is None:
                return False
            if self.when != "any" and d.hazard not in self.when:
                return False
            seen, urgency = d.seen_by_user, d.urgency
        if c.get("unseen") and seen:
            return False
        if c.get("seen") and not seen:
            return False
        if urgency < c.get("min_urgency", 0.0) or urgency > c.get("max_urgency", 1.0):
            return False
        return True

    def check(self, d: Detection, now: float) -> HapticEvent | None:
        if not self.matches(d) or now - self._last < self.cooldown_s:
            return None
        self._last = now
        if self.kind == "custom":
            t = d.triggers.get(self.id) or {}
            why = t.get("reason") or d.reason
            text = f"{self.name}: {why}"
        else:
            why = d.reason
            text = f"{self.name}: {d.hazard} {d.where or ''} {'seen' if d.seen_by_user else 'unseen'} u={d.urgency:.2f}"
        return HapticEvent(self.feel, self.dot, text.replace("  ", " "), now, self.id,
                           trigger_name=self.name, why=why, intensity=self.intensity, rgb=self.rgb)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "enabled": self.enabled, "kind": self.kind,
                "describe": self.describe, "source": self.source, "when": self.when, "if": dict(self.if_),
                "feel": self.feel, "intensity": self.intensity, "rgb": self.rgb, "dot": self.dot,
                "cooldown_s": self.cooldown_s}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "rule"


def rule_from_dict(c: dict) -> Rule:
    """Config/rules.json/phone-app dict -> Rule. Accepts "if" or "if_" for the condition."""
    return Rule(name=c.get("name") or c.get("id") or "rule", feel=c.get("feel", "urgent"),
                when=c.get("when", "any") or "any", if_=dict(c.get("if", c.get("if_")) or {}),
                cooldown_s=float(c.get("cooldown_s", 3.0)), dot=c.get("dot"),
                id=c.get("id") or "", enabled=bool(c.get("enabled", True)),
                source=c.get("source") or "neon",
                kind=c.get("kind") or ("custom" if c.get("describe") else "builtin"),
                describe=c.get("describe") or "",
                intensity=None if c.get("intensity") is None else max(0.0, min(1.0, float(c["intensity"]))),
                rgb=list(c["rgb"])[:3] if c.get("rgb") else None)


# Something dangerous you haven't looked at -> urgent buzz; something milder -> ambient hum.
DEFAULT_RULES = [Rule("blindspot", feel="urgent", if_={"unseen": True, "min_urgency": 0.6}),
                 Rule("heads_up", feel="ambient", if_={"unseen": True, "max_urgency": 0.59}, cooldown_s=6)]


def custom_triggers(rules: list[Rule]) -> list[tuple[str, str]]:
    """(id, description) of enabled custom triggers, for the VLM prompt."""
    return [(r.id, r.describe) for r in rules if r.enabled and r.kind == "custom" and r.describe]


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

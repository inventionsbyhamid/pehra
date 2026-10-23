import hashlib
from dataclasses import dataclass, field

from watchful.rules.signals import REGISTRY, SignalContext, SignalValue, Sighting, TrackHistory
from watchful.rules.watch import Condition, Watch
from watchful.tuning import Tuning
from watchful.vision.detect import COCO
from watchful.vision.perceive import Needs
from watchful.vision.reason import Tick, TrackState
from watchful.vision.zones import Zone

FILE_KINDS = {"file", "demo"}
POSITION_BUCKET_S = 5.0
SIGHTING_MEMORY_S = 7200.0


@dataclass
class Candidate:
    watch_id: str
    camera_id: str
    ts: float
    track_id: int | None
    severity: str
    evidence: dict
    keyframe_keys: list[str]
    fingerprint: str
    watch_name: str = ""
    verify: str = ""
    clip_key: str | None = None


@dataclass
class _Result:
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)


def compare(value, op: str, target) -> bool:
    if op in ("==", "is"):
        return str(value).lower() == str(target).lower() if isinstance(target, str) else value == target
    try:
        v, t = float(value), float(target)
    except (TypeError, ValueError):
        return False
    return v >= t if op == ">=" else v <= t


def validate(w: Watch, zone_names: set[str]) -> list[str]:
    problems = []
    for c in w.conditions:
        if c.signal not in REGISTRY:
            problems.append(f"unknown signal: {c.signal}")
        problems += [f"unknown zone: {z}" for z in c.zones if z not in zone_names]
    problems += [f"unknown zone: {z}" for z in w.zones if z not in zone_names]
    problems += [f"unknown class: {c}" for c in w.subject.classes if c not in COCO]
    return problems


def needs_for(watches: list[Watch]) -> Needs:
    classes: set[str] = set()
    needs = Needs(classes=classes)
    for w in watches:
        classes.update(w.subject.classes or COCO)
        if w.subject.who != "anyone":
            needs.faces = True
        if w.subject.looks_like or w.subject.same_as:
            needs.embed = True
        for c in w.conditions:
            spec = REGISTRY.get(c.signal)
            if spec:
                needs.pose |= "pose" in spec.needs
                needs.posture |= "posture" in spec.needs
                needs.faces |= "faces" in spec.needs
                needs.embed |= "embed" in spec.needs
    return needs


class RuleEngine:
    def __init__(self, watches: list[tuple[str, Watch]], zones: list[Zone], tuning: Tuning, tz: str,
                 camera_kind: str = "stream", embedder=None):
        names = {z.name for z in zones}
        for _, w in watches:
            problems = validate(w, names)
            if problems:
                raise ValueError(f"{w.name}: " + "; ".join(problems))
        self.watches, self.tuning, self.tz, self.kind = watches, tuning, tz, camera_kind
        self.zones = {z.name: z for z in zones}
        self.embedder = embedder
        self.history: dict[int, TrackHistory] = {}
        self.sightings: list[Sighting] = []
        self.state: dict[str, dict] = {}
        self.last_fired: dict[tuple, float] = {}

    def on_tick(self, camera_id: str, tick: Tick, now: float, image=None) -> list[Candidate]:
        self._remember(tick)
        out = []
        for wid, w in self.watches:
            if w.expires_at and now > w.expires_at:
                continue
            if w.time_window and not w.time_window.contains(tick.ts, self.tz):
                continue
            subjects = [s for s in tick.tracks if self._matches(w, s, now)]
            ctx = SignalContext(camera_id, self.zones, tick, self.tuning.signals, self.history, self.sightings,
                                subjects, self.embedder, image, self.state.setdefault(wid, {}))
            scene = [(c, REGISTRY[c.signal].fn(ctx, c, None)) for c in w.conditions
                     if REGISTRY[c.signal].level == "scene"]
            per_track = [c for c in w.conditions if REGISTRY[c.signal].level == "track"]
            if not per_track:
                r = self._combine(w, scene)
                if r.passed and self._ready(wid, None, w, tick.ts, always_cooldown=True):
                    out.append(self._candidate(wid, w, camera_id, tick, None, r))
                continue
            for s in subjects:
                r = self._combine(w, scene + [(c, REGISTRY[c.signal].fn(ctx, c, s)) for c in per_track])
                if r.passed and self._ready(wid, s.track_id, w, tick.ts):
                    out.append(self._candidate(wid, w, camera_id, tick, s, r))
        return out

    def _matches(self, w: Watch, s: TrackState, now: float) -> bool:
        sub = w.subject
        if sub.classes and s.cls not in sub.classes:
            return False
        if sub.who == "unknown" and s.known_person_id is not None:
            return False
        if sub.who == "known" and s.known_person_id is None:
            return False
        if sub.who not in ("anyone", "unknown", "known") and s.known_person_id != sub.who:
            return False
        if w.zones and not any(z in s.zones_inside for z in w.zones):
            return False
        return self._appearance_matches(w, s, now)

    def _appearance_matches(self, w: Watch, s: TrackState, now: float) -> bool:
        return not (w.subject.looks_like or w.subject.same_as)

    @staticmethod
    def _combine(w: Watch, values: list[tuple[Condition, SignalValue]]) -> _Result:
        passed = [(c, v) for c, v in values if compare(v.value, c.op, c.value)]
        score = sum(c.weight for c, _ in passed)
        if w.combine == "all":
            ok = len(passed) == len(values)
        elif w.combine == "any":
            ok = bool(passed)
        else:
            ok = score >= w.threshold
        return _Result(ok, score, [v.reason for _, v in passed if v.reason],
                       {c.signal: v.value for c, v in values})

    def _ready(self, wid: str, track_id: int | None, w: Watch, ts: float, always_cooldown: bool = False) -> bool:
        key = (wid, track_id)
        last = self.last_fired.get(key)
        if last is not None and (not (w.repeat or always_cooldown) or ts - last < w.cooldown_s):
            return False
        self.last_fired[key] = ts
        return True

    def _candidate(self, wid: str, w: Watch, camera_id: str, tick: Tick, s: TrackState | None, r: _Result):
        if self.kind in FILE_KINDS and tick.pos is not None:
            basis = f"{camera_id}|{wid}|{int(tick.pos // POSITION_BUCKET_S)}"
        elif s is not None:
            basis = f"{camera_id}|{wid}|{s.track_id}|{int(s.first_ts)}"
        else:
            basis = f"{camera_id}|{wid}|{int(tick.ts // max(w.cooldown_s, 1))}"
        keys = [k for k in ([s.best_key or s.keyframe_key] if s else []) if k]
        return Candidate(wid, camera_id, tick.ts, s.track_id if s else None, w.severity,
                         {"signals": r.signals, "reasons": r.reasons, "score": r.score}, keys,
                         hashlib.sha1(basis.encode()).hexdigest(), w.name, w.verify)

    def _remember(self, tick: Tick) -> None:
        for s in tick.tracks:
            h = self.history.setdefault(s.track_id, TrackHistory())
            h.boxes.append((tick.ts, s.box))
            h.zones.append((tick.ts, frozenset(s.zones_inside)))
            if s.fresh_pose and s.pose is not None:
                h.poses.append((tick.ts, s.pose))
            h.last_ts = tick.ts
        for s in tick.ended:
            self.history.pop(s.track_id, None)
            if s.cls == "person" and s.signature is not None:
                self.sightings.append(Sighting(s.track_id, s.first_ts, s.last_ts, s.signature,
                                               s.known_person_id is not None))
        self.sightings = [x for x in self.sightings if tick.ts - x.last_ts <= SIGHTING_MEMORY_S]

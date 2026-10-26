import hashlib
import math
from dataclasses import dataclass, field

import numpy as np

from watchful.rules.signals import OPS, REGISTRY, SignalContext, SignalValue, Sighting, TrackHistory
from watchful.rules.watch import Condition, Watch
from watchful.rules.zonestate import correlation, edge_picture
from watchful.store.db import EventRow
from watchful.tuning import Tuning
from watchful.vision.appearance import same_person
from watchful.vision.detect import COCO
from watchful.vision.perceive import Needs
from watchful.vision.reason import Tick, TrackState
from watchful.vision.track import iou
from watchful.vision.zones import Zone

FILE_KINDS = {"file", "demo"}
POSITION_BUCKET_S = 5.0
SIGHTING_MEMORY_S = 7200.0
HEALTH_EVENTS = {"tamper_covered": "covered", "tamper_blurred": "blurred", "tamper_moved": "moved",
                 "tamper_restored": "live"}


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
    output: str = "alert"
    cls: str | None = None  # the track's class (person, car, ...); None for a zone or scene watch


@dataclass
class _Result:
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)


def _norm(v) -> str:
    return str(v).strip().lower()


def compare(value, op: str, target) -> bool:
    """Unknown (nothing read yet, no examples, camera not live) fails every comparison, even != and in."""
    if isinstance(value, str) and _norm(value) == "unknown":
        return False
    if op in ("==", "is"):
        return _norm(value) == _norm(target) if isinstance(target, str) else value == target
    if op == "!=":
        return not compare(value, "==", target)
    if op == "contains":
        return _norm(target) in _norm(value)
    if op == "in":
        options = target if isinstance(target, list) else str(target).split(",")
        return _norm(value) in {_norm(o) for o in options}
    try:
        v, t = float(value), float(target)
    except (TypeError, ValueError):
        return False
    return v >= t if op == ">=" else v <= t


def kind_problem(name: str, kind: str | None, want_line: bool) -> str | None:
    """Lines go only in crossed, areas everywhere else; a mix up would compile and never fire."""
    if kind is None or (kind == "line") == want_line:
        return None
    if want_line:
        return f"{name} is an area, not a line, so nothing can cross it; crossed needs a drawn line"
    return f"{name} is a line, not an area, so nothing can be inside it; only crossed can use a line"


STAY_SIGNAL = "inside_s"
STAY_SIGNALS = {STAY_SIGNAL, "attribute", "present"}  # all the gate log knows of a vehicle inside: plate and colour
STAY_PROBLEM = "inside_s comes from the gate log"


def is_stay_watch(w: Watch) -> bool:
    """A watch about how long something has been inside: it runs on the gate log (logs/ledger.py), not on a camera."""
    return any(c.signal == STAY_SIGNAL for c in w.conditions)


def stay_problems(w: Watch) -> list[str]:
    """inside_s mixed with what the gate log cannot know would compile and never fire."""
    if not is_stay_watch(w):
        return []
    mixed = sorted({c.signal for c in w.conditions if c.signal not in STAY_SIGNALS})
    if any(c.zones or c.params.get("line") for c in w.conditions) or w.zones:
        mixed.append("a zone or line")
    if w.subject.who != "anyone":
        mixed.append("who the person is")
    if w.subject.same_as or w.subject.looks_like:
        mixed.append("what it looks like")
    if not mixed:
        return []
    return [f"{STAY_PROBLEM}, which knows only a vehicle's plate and colour and how long since it came in, so it "
            f"can't be combined with {', '.join(mixed)}"]


COMPARE_WITH = {"text": "is, !=, contains or in", "number": ">= or <=", "bool": "is true or is false"}


def _number(v) -> bool:
    try:
        float(v)
    except (TypeError, ValueError):
        return False
    return not isinstance(v, str) or bool(v.strip())


def contract(c: Condition) -> list[str]:
    """What a signal's spec (rules.signals.SignalSpec) says a condition needs to ever be true: a comparison and a
    value of its kind, a zone, params, and only the words it can give. Without them a watch compiles, saves and runs
    but never fires. Plain sentences, which the compiler also gives the AI when it asks again."""
    spec = REGISTRY.get(c.signal)
    if spec is None:
        return []
    name, v, out = c.signal, c.value, []
    if c.op not in OPS[spec.value]:
        kind = {"text": "words", "number": "a number", "bool": "yes or no"}[spec.value]
        out.append(f"{name} gives {kind}, so compare it with {COMPARE_WITH[spec.value]}, not {c.op}")
    if spec.value == "text":
        given = v if isinstance(v, list) else str(v).split(",") if c.op == "in" else [v]
        given = [str(g).strip().lower() for g in given if not isinstance(g, bool) and str(g).strip()]
        if isinstance(v, list) and c.op != "in":
            out.append(f"{name} compared with a list needs op in")
        if isinstance(v, bool) or not given:
            out.append(f"{name} needs words to compare with" + (f", such as {spec.words[-1]}" if spec.words else ""))
        elif spec.words and c.op != "contains" and (bad := [g for g in given if g not in spec.words]):
            out.append(f"{name} is only ever {', '.join(spec.words[:-1])} or {spec.words[-1]}, never {bad[0]}")
    elif spec.value == "number" and (isinstance(v, list) or not _number(v)):
        out.append(f"{name} needs a number to compare with, not {v}")
    elif spec.value == "bool" and (isinstance(v, list) or isinstance(v, str)
                                   and v.strip().lower() not in ("true", "false")):
        out.append(f"{name} is yes or no, so compare it with true or false, not {v}")
    if spec.zone and not c.zones:
        out.append(f"{name} needs a zone: name the zone it reads")
    out += [f"{name} needs params.{p}" for p in spec.params if not str(c.params.get(p) or "").strip()]
    return out


def validate(w: Watch, zones: set[str] | dict[str, str | None]) -> list[str]:
    """zones are the drawn names, or names to kinds; with kinds, a line used as an area (or the other way) is a
    problem too. Lines are zones of kind line."""
    kinds = zones if isinstance(zones, dict) else dict.fromkeys(zones)
    problems = []

    def check(name: str, want_line: bool, unknown: str) -> None:
        if name not in kinds:
            problems.append(f"{unknown}: {name}")
        elif p := kind_problem(name, kinds[name], want_line):
            problems.append(p)

    for c in w.conditions:
        if c.signal not in REGISTRY:
            problems.append(f"unknown signal: {c.signal}")
        problems += contract(c)
        crossing = c.signal == "crossed"
        for z in c.zones:
            check(z, crossing, "unknown line" if crossing else "unknown zone")
        if c.params.get("line"):
            check(str(c.params["line"]), True, "unknown line")
    for z in w.zones:
        check(z, False, "unknown zone")
    problems += [f"unknown class: {c}" for c in w.subject.classes if c not in COCO]
    return problems + stay_problems(w)


MOMENTS = {"entered", "exited", "crossed"}  # true on one tick only: the subject's look must be known by then
INSIDE = {"in_zone", "dwell_s", "climbing"}  # true only while the subject stays inside the condition's zones


def _embed_places(w: Watch) -> set[str]:
    """The zones a subject of w must be inside for w to fire, while a later look still counts; empty: anywhere."""
    if any(c.signal in MOMENTS for c in w.conditions):
        return set()
    places = set(w.zones)
    if w.combine == "all":
        places |= {z for c in w.conditions if c.signal in INSIDE for z in c.zones}
    return places


def needs_for(watches: list[Watch]) -> Needs:
    """What the watches need. Track crops are embedded only for the classes and inside the zones of the watches that
    use them (embed_zones None: a watch that looks anywhere)."""
    classes: set[str] = set()
    needs = Needs(classes=classes)
    embed_classes: set[str] = set()
    embed_zones: set[str] | None = set()
    for w in watches:
        classes.update(w.subject.classes or COCO)
        if w.subject.who != "anyone":
            needs.faces = True
        embeds = bool(w.subject.looks_like or w.subject.same_as)
        for c in w.conditions:
            spec = REGISTRY.get(c.signal)
            if spec:
                needs.pose |= "pose" in spec.needs
                needs.posture |= "posture" in spec.needs
                needs.faces |= "faces" in spec.needs
                embeds |= "embed" in spec.needs
                needs.slow |= "slow" in spec.needs
                needs.embedder |= "embedder" in spec.needs
        if embeds:
            needs.embed = True
            embed_classes.update(w.subject.classes or COCO)
            places = _embed_places(w)
            embed_zones = None if embed_zones is None or not places else embed_zones | places
    if needs.embed:
        needs.embed_classes, needs.embed_zones = embed_classes, embed_zones
    return needs


def log_row(c: Candidate) -> EventRow:
    """What a log watch writes instead of an alert: one event row the logs and backtests can read."""
    return EventRow(None, c.camera_id, c.ts, "watch_log", c.track_id, None, None,
                    {"watch_id": c.watch_id, "watch": c.watch_name, "reasons": c.evidence.get("reasons", []),
                     "signals": c.evidence.get("signals", {})},
                    c.keyframe_keys[0] if c.keyframe_keys else None, None, None, None)


@dataclass
class _Parked:
    cls: str
    box: tuple
    since: float
    seen: float
    template: np.ndarray | None


class RuleEngine:
    def __init__(self, watches: list[tuple[str, Watch]], zones: list[Zone], tuning: Tuning, tz: str,
                 camera_kind: str = "stream", embedder=None, states=None):
        kinds = {z.name: z.kind for z in zones}
        for _, w in watches:
            problems = validate(w, kinds)
            if problems:
                raise ValueError(f"{w.name}: " + "; ".join(problems))
        self.watches, self.tuning, self.tz, self.kind = watches, tuning, tz, camera_kind
        self.zones = {z.name: z for z in zones}
        self.embedder = embedder
        self.history: dict[int, TrackHistory] = {}
        self.sightings: list[Sighting] = []
        self.state: dict[str, dict] = {}
        self.last_fired: dict[tuple, float] = {}
        self.armed: dict[tuple, bool] = {}
        self.states = states
        self.health = "live"
        self.parked: list[_Parked] = []
        self.watch_stationary = any(c.signal == "stationary_s" for _, w in watches for c in w.conditions)
        self.shared: dict = {"zone_state": {}}  # per camera, shared by every watch: zone states, the flow tool
        self._last_slow = float("-inf")

    def on_tick(self, camera_id: str, tick: Tick, now: float, image=None) -> list[Candidate]:
        slow = tick.ts - self._last_slow >= self.tuning.zones.slow_tick_s
        if slow:
            self._last_slow = tick.ts
        self._health(tick)
        self._remember(tick, image, slow)
        out = []
        for wid, w in self.watches:
            if w.expires_at and now > w.expires_at:
                continue
            if w.time_window and not w.time_window.contains(tick.ts, self.tz):
                continue
            subjects = [s for s in tick.tracks if self._matches(w, s, now, tick)]
            state = self.state.setdefault(wid, {})
            ctx = SignalContext(camera_id, self.zones, tick, self.tuning.signals, self.history, self.sightings,
                                subjects, self.embedder, image, state, self.tuning.appearance.same, slow,
                                self.health, self.states, list(w.subject.classes), self.shared)
            holds = state.setdefault("_holds", {})
            scene = [self._eval(ctx, holds, i, c, None) for i, c in enumerate(w.conditions)
                     if REGISTRY[c.signal].level == "scene"]
            per_track = [(i, c) for i, c in enumerate(w.conditions) if REGISTRY[c.signal].level == "track"]
            if not per_track:
                r = self._combine(w, scene)
                if self._fires(wid, None, w, tick.ts, r.passed):
                    out.append(self._candidate(wid, w, camera_id, tick, None, r))
                continue
            for s in subjects:
                r = self._combine(w, scene + [self._eval(ctx, holds, i, c, s) for i, c in per_track])
                if self._fires(wid, s.track_id, w, tick.ts, r.passed):
                    out.append(self._candidate(wid, w, camera_id, tick, s, r))
        return out

    @staticmethod
    def _eval(ctx: SignalContext, holds: dict, index: int, c: Condition, s: TrackState | None):
        """A signal's value and whether its condition passes; params.hold_s keeps it true for that long first."""
        spec = REGISTRY[c.signal]
        v = spec.fn(ctx, c, s)
        ok = compare(v.value, c.op, c.value)
        hold = float(c.params.get("hold_s") or 0) if not spec.own_hold else 0.0
        if hold > 0:
            key = (index, s.track_id if s else None)
            if not ok:
                holds.pop(key, None)
            else:
                since = holds.setdefault(key, ctx.tick.ts)
                ok = ctx.tick.ts - since >= hold
                if ok and v.reason:
                    v = SignalValue(v.value, f"{v.reason} for {ctx.tick.ts - since:.0f} s")
        return c, v, ok

    def _health(self, tick: Tick) -> None:
        for e in tick.events:
            if e.kind in HEALTH_EVENTS:
                self.health = HEALTH_EVENTS[e.kind]
        if tick.health:
            self.health = tick.health
        elif self.health == "offline":
            self.health = "live"  # frames are arriving again

    def _matches(self, w: Watch, s: TrackState, now: float, tick: Tick) -> bool:
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
            # a track that left one of the zones on this tick still counts, so "leaves" watches can fire
            left = {e.zone for e in tick.events if e.kind == "exit" and e.track_id == s.track_id}
            if not left & set(w.zones):
                return False
        return self._appearance_matches(w, s, now)

    def _text_vector(self, sub):
        if sub.looks_like_embedding:
            return np.asarray(sub.looks_like_embedding, np.float32)
        if self.embedder is None:
            return None
        cache = self.state.setdefault("_texts", {})
        if sub.looks_like not in cache:
            cache[sub.looks_like] = self.embedder.text(sub.looks_like)
        return cache[sub.looks_like]

    def _matches_text(self, cosine: float) -> bool:
        cfg = self.tuning.signals
        if hasattr(self.embedder, "probability"):
            return self.embedder.probability(cosine) >= cfg.looks_like_probability
        return cosine >= cfg.looks_like_threshold

    def _appearance_matches(self, w: Watch, s: TrackState, now: float) -> bool:
        sub, cfg = w.subject, self.tuning.signals
        if sub.looks_like:
            q = self._text_vector(sub)
            if q is None or s.embedding is None or not self._matches_text(float(np.dot(s.embedding, q))):
                return False
        if sub.same_as:
            target = sub.same_as
            if target.expires_at and now > target.expires_at:
                return False
            if target.embedding is not None and s.embedding is not None:
                if float(np.dot(np.asarray(target.embedding, np.float32), s.embedding)) < cfg.same_as_threshold:
                    return False
            elif target.signature is not None and s.signature is not None:
                if same_person(target.signature, s.signature) >= self.tuning.appearance.same:
                    return False
            else:
                return False
        return True

    @staticmethod
    def _method(w: Watch) -> str | None:
        if w.conditions and all(c.signal == "camera_health" for c in w.conditions):
            return "camera"  # the camera's own state, measured by OpenCV: nothing in the picture to check
        if w.subject.who not in ("anyone", "unknown", "known"):
            return "face"
        if w.subject.same_as:
            return "appearance"
        if w.subject.looks_like:
            return "description"
        return None

    @staticmethod
    def _combine(w: Watch, values: list[tuple[Condition, SignalValue, bool]]) -> _Result:
        passed = [(c, v) for c, v, ok in values if ok]
        score = sum(c.weight for c, _ in passed)
        if w.combine == "all":
            ok = len(passed) == len(values)
        elif w.combine == "any":
            ok = bool(passed)
        else:
            ok = score >= w.threshold
        return _Result(ok, score, [v.reason for _, v in passed if v.reason],
                       {c.signal: v.value for c, v, _ in values})

    def _fires(self, wid: str, track_id: int | None, w: Watch, ts: float, passed: bool) -> bool:
        """Fires when a watch becomes true; it re-arms once it is false again (a second fall, a second visit).
        repeat watches also fire again every cooldown while they stay true; the cooldown stops flapping."""
        key = (wid, track_id)
        if not passed:
            self.armed[key] = True
            return False
        last = self.last_fired.get(key)
        cooldown = w.cooldown_s if w.output == "log" else max(w.cooldown_s, self.tuning.signals.alert_cooldown_floor_s)
        cooled = last is None or ts - last >= cooldown
        if not cooled or not (self.armed.get(key, True) or w.repeat):
            return False
        self.armed[key] = False
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
        if w.subject.same_as and w.subject.same_as.crop_key:
            keys.append(w.subject.same_as.crop_key)
        evidence = {"signals": r.signals, "reasons": r.reasons, "score": r.score}
        if self._method(w):
            evidence["method"] = self._method(w)
        severity = "info" if w.output == "log" else w.severity
        return Candidate(wid, camera_id, tick.ts, s.track_id if s else None, severity, evidence, keys,
                         hashlib.sha1(basis.encode()).hexdigest(), w.name, w.verify, output=w.output,
                         cls=s.cls if s else None)

    def _settle(self, s: TrackState, h: TrackHistory, tick: Tick, image, slow: bool, new: bool) -> None:
        """Where a box settled and since when, for stationary_s; a new track on a remembered parked spot takes
        over that spot's clock, and the slow tier keeps a parked spot alive while its picture still matches."""
        cfg = self.tuning.signals
        cx, cy = s.box[0] + s.box[2] / 2, s.box[1] + s.box[3] / 2
        if new:
            spot = max((p for p in self.parked if p.cls == s.cls and tick.ts - p.seen <= cfg.parked_rejoin_s
                        and iou(p.box, s.box) >= cfg.parked_rejoin_iou), key=lambda p: iou(p.box, s.box), default=None)
            if spot is not None:
                self.parked.remove(spot)
                h.anchor = (spot.since, cx, cy)
        if h.anchor is None or math.hypot(cx - h.anchor[1], cy - h.anchor[2]) > cfg.stationary_move * max(s.box[3], 1):
            h.anchor, h.template = (tick.ts, cx, cy), None
        if slow and image is not None:
            h.template = self._spot_picture(image, s.box)

    def _spot_picture(self, image, box) -> np.ndarray | None:
        x, y, w, h = (int(v) for v in box)
        crop = image[max(0, y):y + h, max(0, x):x + w]
        return edge_picture(crop, self.tuning.zones) if crop.size else None

    def _keep_parked(self, image, slow: bool, ts: float) -> None:
        cfg = self.tuning.signals
        keep = []
        for p in self.parked:
            if slow and image is not None and p.template is not None:
                now = self._spot_picture(image, p.box)
                if now is not None and correlation(p.template, now) >= cfg.parked_template_match:
                    p.seen = ts
                elif now is not None:
                    continue  # the spot looks different now: whatever was parked has gone
            if ts - p.seen <= cfg.parked_rejoin_s:
                keep.append(p)
        self.parked = keep

    def _remember(self, tick: Tick, image=None, slow: bool = False) -> None:
        for s in tick.tracks:
            new = s.track_id not in self.history
            h = self.history.setdefault(s.track_id, TrackHistory())
            h.boxes.append((tick.ts, s.box))
            h.zones.append((tick.ts, frozenset(s.zones_inside)))
            if s.fresh_pose and s.pose is not None:
                h.poses.append((tick.ts, s.pose))
            h.last_ts = tick.ts
            if self.watch_stationary:
                self._settle(s, h, tick, image, slow, new)
        for s in tick.ended:
            h = self.history.pop(s.track_id, None)
            if h is not None and h.anchor is not None and self.watch_stationary:
                self.parked.append(_Parked(s.cls, s.box, h.anchor[0], s.last_ts, h.template))
            for key in [k for k in self.last_fired if k[1] == s.track_id]:
                self.last_fired.pop(key, None)
                self.armed.pop(key, None)
            for st in self.state.values():
                holds = st.get("_holds") or {}
                for key in [k for k in holds if k[1] == s.track_id]:
                    holds.pop(key)
            if s.cls == "person" and s.signature is not None:
                self.sightings.append(Sighting(s.track_id, s.first_ts, s.last_ts, s.signature,
                                               s.known_person_id is not None))
        self.sightings = [x for x in self.sightings if tick.ts - x.last_ts <= SIGHTING_MEMORY_S]
        if self.parked:
            self._keep_parked(image, slow, tick.ts)
